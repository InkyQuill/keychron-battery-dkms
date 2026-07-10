# Keychron Link Protocol Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, repeatable userspace harness that discovers Keychron Link HID channels, records stock receiver traffic, executes only confirmed read commands, and produces an evidence-based go/no-go decision for a later kernel implementation.

**Architecture:** A dependency-free Python package separates pure packet decoding, sysfs device discovery, hidraw transport, and the command-line interface. The harness has no arbitrary-write mode: its only active requests are the confirmed receiver reads `0xb1`, `0xb2`, and `0xb3`; all other investigation starts as passive capture. A later kernel plan is prohibited until a stock-firmware battery field or query is independently reproduced.

**Tech Stack:** Python 3.12 standard library, `unittest`, Linux sysfs/hidraw, JSON Lines capture files, existing C/DKMS kernel module unchanged.

## Global Constraints

- The first target is Keychron Link USB-A `3434:d030`, firmware `d.3.0`, paired with stock Keychron V6 Max `3434:0960`.
- Do not require or perform keyboard or receiver firmware changes.
- Do not brute-force command bytes, report IDs, feature reports, or USB control requests.
- Do not expose a CLI option that sends caller-provided bytes.
- Block reset, pairing, configuration writes, bootloader entry, and DFU commands.
- Preserve complete timestamped evidence for every live experiment outside git; commit only redacted protocol notes and synthetic fixtures.
- Do not capture normal keyboard interfaces; capture only the Link status and `0xff60/0x61` vendor channels.
- Do not modify `keychron_battery.c` until a later, separately reviewed kernel implementation plan.
- If the stock receiver exposes no retrievable percentage, record that boundary instead of estimating capacity.

## File Structure

- `tools/__init__.py`: marks repository tooling as an importable package.
- `tools/keychron_link/__init__.py`: exports the investigation package version.
- `tools/keychron_link/protocol.py`: pure command construction and packet decoders; no file or device I/O.
- `tools/keychron_link/discovery.py`: finds Keychron hidraw interfaces from sysfs and classifies their HID descriptors.
- `tools/keychron_link/transport.py`: bounded nonblocking hidraw reads and allowlisted receiver exchanges.
- `tools/keychron_link/cli.py`: `list`, `info`, and passive `capture` commands with JSON/JSONL output.
- `tests/test_link_protocol.py`: synthetic fixtures for confirmed receiver packets and malformed inputs.
- `tests/test_link_discovery.py`: temporary-sysfs discovery tests.
- `tests/test_link_transport.py`: fake-I/O tests for timeouts, response matching, and command allowlisting.
- `tests/test_link_cli.py`: CLI behavior and safety-surface tests.
- `docs/protocol/keychron-link-stock.md`: redacted evidence, confirmed packet formats, rejected hypotheses, and the kernel go/no-go decision.
- `.gitignore`: excludes local capture artifacts and Python cache files.

---

### Task 1: Pure Link packet protocol

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/keychron_link/__init__.py`
- Create: `tools/keychron_link/protocol.py`
- Create: `tests/test_link_protocol.py`

**Interfaces:**
- Produces: `build_receiver_request(command: int) -> bytes`
- Produces: `decode_receiver_version(packet: bytes) -> ReceiverVersion`
- Produces: `decode_receiver_state(packet: bytes) -> ReceiverState`
- Produces: `decode_receiver_firmware(packet: bytes) -> ReceiverFirmware`
- Produces: `decode_state_notification(packet: bytes) -> StateNotification`
- Consumes: no earlier task interfaces.

- [ ] **Step 1: Write failing tests for confirmed live packets**

```python
# tests/test_link_protocol.py
import unittest

from tools.keychron_link.protocol import (
    ProtocolError,
    build_receiver_request,
    decode_receiver_firmware,
    decode_receiver_state,
    decode_receiver_version,
    decode_state_notification,
)


class LinkProtocolTests(unittest.TestCase):
    def test_builds_only_32_byte_confirmed_reads(self):
        self.assertEqual(build_receiver_request(0xB1), bytes([0xB1]) + bytes(31))
        self.assertEqual(build_receiver_request(0xB2), bytes([0xB2]) + bytes(31))
        self.assertEqual(build_receiver_request(0xB3), bytes([0xB3]) + bytes(31))
        with self.assertRaises(ProtocolError):
            build_receiver_request(0xBA)

    def test_decodes_live_receiver_version(self):
        value = decode_receiver_version(bytes.fromhex("b1 01 00") + bytes(29))
        self.assertEqual(value.version, 1)
        self.assertEqual(value.feature_flags, (0, 0))

    def test_decodes_live_v6_max_slot_as_little_endian(self):
        packet = bytes.fromhex("b2 01 34 34 60 09 01") + bytes(25)
        value = decode_receiver_state(packet)
        self.assertEqual((value.slots[0].vid, value.slots[0].pid), (0x3434, 0x0960))
        self.assertTrue(value.slots[0].connected)

    def test_decodes_live_receiver_firmware(self):
        packet = bytes.fromhex(
            "b3 64 2e 33 2e 30 20 4a 75 6e 20 20 37 20 32 30 "
            "32 34 20 31 32 3a 33 32 3a 31 38 00 00 00 00 00"
        )
        self.assertEqual(
            decode_receiver_firmware(packet).text,
            "d.3.0 Jun  7 2024 12:32:18",
        )

    def test_decodes_status_channel_notification_after_report_id(self):
        packet = bytes.fromhex("54 e2 01 02") + bytes(17)
        value = decode_state_notification(packet)
        self.assertEqual(value.report_id, 0x54)
        self.assertEqual((value.kind, value.work_mode, value.state), (0xE2, 1, 2))

    def test_rejects_truncated_wrong_command_and_trailing_garbage(self):
        for decoder, packet in (
            (decode_receiver_version, b"\xb1"),
            (decode_receiver_state, bytes(31)),
            (decode_receiver_firmware, b"\xb2" + bytes(31)),
            (decode_state_notification, b"\x54\xe2\x01"),
        ):
            with self.subTest(decoder=decoder.__name__):
                with self.assertRaises(ProtocolError):
                    decoder(packet)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the protocol tests and verify the import fails**

Run: `python -m unittest tests.test_link_protocol -v`

Expected: `ModuleNotFoundError: No module named 'tools.keychron_link'`.

- [ ] **Step 3: Implement immutable packet types and strict decoders**

```python
# tools/keychron_link/protocol.py
from dataclasses import dataclass

PACKET_SIZE = 32
CONFIRMED_READS = frozenset({0xB1, 0xB2, 0xB3})


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ReceiverVersion:
    version: int
    feature_flags: tuple[int, int]


@dataclass(frozen=True)
class ReceiverSlot:
    vid: int
    pid: int
    connected: bool


@dataclass(frozen=True)
class ReceiverState:
    slots: tuple[ReceiverSlot, ...]


@dataclass(frozen=True)
class ReceiverFirmware:
    text: str


@dataclass(frozen=True)
class StateNotification:
    report_id: int
    kind: int
    work_mode: int
    state: int


def _require(packet: bytes, size: int, command: int, offset: int = 0) -> None:
    if len(packet) != size:
        raise ProtocolError(f"expected {size} bytes, got {len(packet)}")
    if packet[offset] != command:
        raise ProtocolError(
            f"expected command 0x{command:02x}, got 0x{packet[offset]:02x}"
        )


def build_receiver_request(command: int) -> bytes:
    if command not in CONFIRMED_READS:
        raise ProtocolError(f"command 0x{command:02x} is not an allowlisted read")
    return bytes([command]) + bytes(PACKET_SIZE - 1)


def decode_receiver_version(packet: bytes) -> ReceiverVersion:
    _require(packet, PACKET_SIZE, 0xB1)
    return ReceiverVersion(packet[1] | packet[2] << 8, (packet[3], packet[4]))


def decode_receiver_state(packet: bytes) -> ReceiverState:
    _require(packet, PACKET_SIZE, 0xB2)
    slots = []
    for base in (2, 7, 12):
        slots.append(
            ReceiverSlot(
                packet[base] | packet[base + 1] << 8,
                packet[base + 2] | packet[base + 3] << 8,
                packet[base + 4] == 1,
            )
        )
    return ReceiverState(tuple(slots))


def decode_receiver_firmware(packet: bytes) -> ReceiverFirmware:
    _require(packet, PACKET_SIZE, 0xB3)
    payload = packet[1:].split(b"\0", 1)[0]
    try:
        return ReceiverFirmware(payload.decode("ascii"))
    except UnicodeDecodeError as error:
        raise ProtocolError("firmware response is not ASCII") from error


def decode_state_notification(packet: bytes) -> StateNotification:
    _require(packet, 21, 0xE2, offset=1)
    return StateNotification(packet[0], packet[1], packet[2], packet[3])
```

Create empty package markers in `tools/__init__.py` and set `__version__ = "0.1.0"` in `tools/keychron_link/__init__.py`.

- [ ] **Step 4: Run protocol tests**

Run: `python -m unittest tests.test_link_protocol -v`

Expected: six tests pass.

- [ ] **Step 5: Commit the pure protocol layer**

```bash
git add tools/__init__.py tools/keychron_link/__init__.py \
  tools/keychron_link/protocol.py tests/test_link_protocol.py
git commit -m "test: define confirmed Keychron Link packets"
```

---

### Task 2: Sysfs HID discovery

**Files:**
- Create: `tools/keychron_link/discovery.py`
- Create: `tests/test_link_discovery.py`

**Interfaces:**
- Consumes: no protocol calls; uses the package types only.
- Produces: `HidrawInterface`
- Produces: `discover_keychron_links(sys_class: Path = Path('/sys/class/hidraw')) -> tuple[HidrawInterface, ...]`
- Produces: `find_raw_channel(interfaces) -> HidrawInterface`
- Produces: `find_status_channel(interfaces) -> HidrawInterface`

- [ ] **Step 1: Write discovery tests with a synthetic sysfs tree**

```python
# tests/test_link_discovery.py
import tempfile
import unittest
from pathlib import Path

from tools.keychron_link.discovery import (
    DiscoveryError,
    discover_keychron_links,
    find_raw_channel,
)


class LinkDiscoveryTests(unittest.TestCase):
    def make_hidraw(self, root: Path, name: str, descriptor: bytes, interface: int):
        device = root / "devices" / f"1-10:1.{interface}" / f"0003:3434:D030.00{interface}"
        device.mkdir(parents=True)
        (device / "uevent").write_text(
            "HID_ID=0003:00003434:0000D030\nHID_NAME=Keychron Keychron Link\n"
        )
        (device / "report_descriptor").write_bytes(descriptor)
        (device.parent / "bInterfaceNumber").write_text(f"{interface:02x}\n")
        entry = root / "class" / "hidraw" / name
        entry.mkdir(parents=True)
        (entry / "device").symlink_to(device, target_is_directory=True)

    def test_finds_d030_raw_channel_by_descriptor_not_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = bytes.fromhex("06 60 ff 09 61 a1 01 95 20 75 08 81 02 95 20 91 02 c0")
            self.make_hidraw(root, "hidraw42", raw, 3)
            found = discover_keychron_links(root / "class" / "hidraw")
            self.assertEqual(find_raw_channel(found).device_path, Path("/dev/hidraw42"))
            self.assertEqual(find_raw_channel(found).interface_number, 3)

    def test_ignores_non_keychron_and_rejects_missing_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_hidraw(root, "hidraw2", bytes.fromhex("05 01 09 06"), 2)
            found = discover_keychron_links(root / "class" / "hidraw")
            with self.assertRaises(DiscoveryError):
                find_raw_channel(found)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run discovery tests and verify failure**

Run: `python -m unittest tests.test_link_discovery -v`

Expected: import fails because `tools.keychron_link.discovery` does not exist.

- [ ] **Step 3: Implement descriptor-based discovery**

Implement `HidrawInterface` as a frozen dataclass containing `device_path`, `sysfs_path`, `vid`, `pid`, `interface_number`, and `report_descriptor`. Parse `HID_ID` from `uevent`; accept vendor `0x3434` and products `0xd030` or `0xd031`; obtain the USB interface number from the HID device parent's `bInterfaceNumber`.

Use these exact classifier signatures:

```python
RAW_USAGE = bytes.fromhex("06 60 ff 09 61")
STATUS_USAGE = bytes.fromhex("05 8c 09 01")


def is_raw_channel(value: HidrawInterface) -> bool:
    return RAW_USAGE in value.report_descriptor


def is_status_channel(value: HidrawInterface) -> bool:
    return STATUS_USAGE in value.report_descriptor
```

`find_raw_channel()` and `find_status_channel()` must require exactly one match and raise `DiscoveryError` otherwise. Do not fall back to interface numbers.

- [ ] **Step 4: Run discovery and protocol tests**

Run: `python -m unittest tests.test_link_protocol tests.test_link_discovery -v`

Expected: eight tests pass.

- [ ] **Step 5: Commit discovery**

```bash
git add tools/keychron_link/discovery.py tests/test_link_discovery.py
git commit -m "feat: discover Keychron Link HID channels"
```

---

### Task 3: Bounded hidraw transport

**Files:**
- Create: `tools/keychron_link/transport.py`
- Create: `tests/test_link_transport.py`

**Interfaces:**
- Consumes: `build_receiver_request(command)` from Task 1.
- Consumes: `HidrawInterface.device_path` from Task 2.
- Produces: `PacketRecord(timestamp_ns: int, direction: str, channel: str, data: bytes)`
- Produces: `HidrawTransport.exchange_confirmed_read(command: int, timeout: float = 1.0, attempts: int = 3) -> tuple[PacketRecord, ...]`
- Produces: `HidrawTransport.capture(duration: float) -> Iterator[PacketRecord]`

- [ ] **Step 1: Write fake-I/O transport tests**

Tests must assert all of the following:

```python
def test_exchange_prefixes_report_zero_and_matches_command(self):
    # Writer receives b"\x00\xb2" + 31 zero bytes.
    # A queued 0xe2 notification is recorded but skipped.
    # The following 32-byte 0xb2 response completes the exchange.

def test_exchange_times_out_after_exact_attempt_count(self):
    # Fake waiter returns False three times.
    # Exactly three identical writes occur, then TransportTimeout is raised.

def test_transport_rejects_non_allowlisted_command_before_opening_device(self):
    # command 0xba raises ProtocolError and opener call count remains zero.

def test_capture_is_read_only(self):
    # capture() reads fake packets until the injected monotonic deadline.
    # Writer call count remains zero.
```

Use `unittest.mock.Mock` for opener, reader, writer, waiter, and monotonic clock. The transport constructor must accept these dependencies so tests never open real devices.

- [ ] **Step 2: Run transport tests and verify failure**

Run: `python -m unittest tests.test_link_transport -v`

Expected: import fails because `tools.keychron_link.transport` does not exist.

- [ ] **Step 3: Implement nonblocking transport with response filtering**

Open hidraw with `os.O_RDWR | os.O_NONBLOCK` for exchanges and `os.O_RDONLY | os.O_NONBLOCK` for capture. Prefix outgoing 32-byte payloads with report ID zero. Use `select.select([fd], [], [], remaining)` and `time.monotonic_ns()`; never use an unbounded read.

The exchange matcher is exact:

```python
if len(packet) == 32 and packet[0] == command:
    return tuple(records)
```

All other packets remain in the returned transcript and do not reset the attempt deadline. Close every file descriptor in `finally`. Validate the command with `build_receiver_request()` before calling the opener.

- [ ] **Step 4: Run all unit tests**

Run: `python -m unittest discover -s tests -v`

Expected: all protocol, discovery, and transport tests pass.

- [ ] **Step 5: Commit transport**

```bash
git add tools/keychron_link/transport.py tests/test_link_transport.py
git commit -m "feat: add bounded Keychron hidraw transport"
```

---

### Task 4: Safe investigation CLI

**Files:**
- Create: `tools/keychron_link/cli.py`
- Create: `tests/test_link_cli.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: discovery and transport interfaces from Tasks 2 and 3.
- Produces: `python -m tools.keychron_link.cli list`
- Produces: `python -m tools.keychron_link.cli info`
- Produces: `python -m tools.keychron_link.cli capture --seconds N --output FILE`

- [ ] **Step 1: Write CLI tests before implementation**

Test the parser and injected `main(argv, discover, transport_factory, stdout, stderr)` entry point:

```python
def test_parser_exposes_only_list_info_and_capture(self):
    parser = build_parser()
    self.assertEqual(set(parser._subparsers._group_actions[0].choices), {
        "list", "info", "capture"
    })

def test_info_emits_json_with_version_state_and_firmware(self):
    # Inject one raw/status interface and a fake transport transcript.
    # Assert JSON contains receiver 3434:d030, slot 3434:0960 connected,
    # protocol version 1, feature flags [0, 0], and firmware d.3.0.

def test_capture_emits_json_lines_with_hex_not_raw_binary(self):
    # Assert keys are timestamp_ns, direction, channel, and hex.

def test_capture_rejects_nonpositive_or_more_than_300_seconds(self):
    # --seconds 0 and --seconds 301 both exit with argparse error code 2.
```

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `python -m unittest tests.test_link_cli -v`

Expected: import fails because `tools.keychron_link.cli` does not exist.

- [ ] **Step 3: Implement the three-command CLI**

Use `argparse` subcommands only. `info` issues `0xb1`, `0xb2`, and `0xb3` sequentially and decodes only the matching response in each transcript. `capture` opens the raw and status channels read-only and merges records by `timestamp_ns`. Output stable JSON with `sort_keys=True`.

The capture duration validator must be:

```python
def capture_seconds(text: str) -> float:
    value = float(text)
    if not 0 < value <= 300:
        raise argparse.ArgumentTypeError("seconds must be in (0, 300]")
    return value
```

Do not add `send`, `write`, `command`, `report`, `feature`, `control`, `dfu`, or raw hexadecimal CLI options.

- [ ] **Step 4: Exclude local evidence and caches**

Append exactly:

```gitignore

# Python investigation tooling
__pycache__/
*.py[cod]
/artifacts/keychron-link/
```

- [ ] **Step 5: Run safety and regression checks**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `python -m tools.keychron_link.cli --help`

Expected: help lists only `list`, `info`, and `capture`.

Run: `rg -n 'add_parser\(' tools/keychron_link/cli.py`

Expected: exactly three matches, for the three approved subcommands.

- [ ] **Step 6: Commit the CLI**

```bash
git add .gitignore tools/keychron_link/cli.py tests/test_link_cli.py
git commit -m "feat: add safe Keychron Link investigation CLI"
```

---

### Task 5: Live baseline and reconnect evidence

**Files:**
- Create: `docs/protocol/keychron-link-stock.md`
- Runtime-only: `artifacts/keychron-link/baseline.jsonl`
- Runtime-only: `artifacts/keychron-link/reconnect.jsonl`

**Interfaces:**
- Consumes: the CLI from Task 4.
- Produces: a redacted protocol record containing only vendor-channel traffic and device metadata.

- [ ] **Step 1: Verify the full test suite before hardware access**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass with no root privileges.

- [ ] **Step 2: Confirm discovery and known receiver reads on live hardware**

Run: `python -m tools.keychron_link.cli list`

Expected: JSON identifies `3434:d030`, one `0xff60/0x61` raw channel, and one `0x8c/0x01` status channel.

Run: `sudo -n python -m tools.keychron_link.cli info`

Expected: protocol version 1, feature flags `[0, 0]`, firmware beginning `d.3.0`, and connected slot `3434:0960`.

- [ ] **Step 3: Record a passive baseline**

Run: `mkdir -p artifacts/keychron-link && sudo -n python -m tools.keychron_link.cli capture --seconds 20 --output artifacts/keychron-link/baseline.jsonl`

Expected: command performs no writes and exits after 20 seconds.

- [ ] **Step 4: Record one controlled reconnect**

Run: `sudo -n python -m tools.keychron_link.cli capture --seconds 60 --output artifacts/keychron-link/reconnect.jsonl`

During the capture, switch the V6 Max off, switch it back to 2.4 GHz, and press three ordinary keys. Expected vendor-channel evidence includes disconnected and connected state changes; the capture must not contain normal keycodes or typed text.

- [ ] **Step 5: Write the initial protocol record**

Document these exact sections in `docs/protocol/keychron-link-stock.md`:

```markdown
# Stock Keychron Link Protocol Evidence

## Tested Hardware
## HID Channels
## Confirmed Read Commands
## Passive Reconnect Reports
## Battery Data Boundary
## Rejected Hypotheses
## Evidence Files and Redaction
## Kernel Go/No-Go Gate
```

Record packet layouts with byte offsets, state that raw artifacts are gitignored, and cite SHA-256 hashes of both JSONL files. Under `Battery Data Boundary`, record whether any changing byte plausibly represents capacity; do not label a byte as capacity without Fn+B correlation.

- [ ] **Step 6: Check evidence hygiene and commit only the note**

Run: `git status --short --ignored | rg 'artifacts/keychron-link|docs/protocol'`

Expected: the two JSONL files are ignored and only the Markdown protocol note is untracked.

```bash
git add docs/protocol/keychron-link-stock.md
git commit -m "docs: record stock Keychron Link baseline"
```

---

### Task 6: Evidence checkpoint and next-plan decision

**Files:**
- Modify: `docs/protocol/keychron-link-stock.md`
- Do not modify: `keychron_battery.c`

**Interfaces:**
- Consumes: protocol note and hashed evidence from Task 5.
- Produces: one explicit decision: `GO: reproducible stock capacity` or `NO-GO: stock capacity not yet retrievable`.

- [ ] **Step 1: Compare every observed report boundary**

For each unique vendor-channel packet shape, list the channel, length, report/command byte, bytes that changed between baseline/disconnect/connect, and whether the change correlates with connection state. Use the actual JSONL evidence; do not infer omitted bytes.

- [ ] **Step 2: Apply the battery acceptance test**

Mark `GO` only if one field or confirmed read response satisfies all of these:

1. It is an integer in `0-100`.
2. It agrees with the keyboard's Fn+B 10% bucket.
3. It is reproduced after a second reconnect or later charge change.
4. Reading it does not interrupt normal keyboard input.
5. Its request, response matching, timing, and freshness can be specified byte-for-byte.

Otherwise mark `NO-GO` and state the missing evidence precisely.

- [ ] **Step 3: Run final investigation verification**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git diff -- keychron_battery.c`

Expected: no output.

- [ ] **Step 4: Commit the checkpoint**

```bash
git add docs/protocol/keychron-link-stock.md
git commit -m "docs: decide Keychron Link kernel readiness"
```

- [ ] **Step 5: Stop at the protocol gate**

If the decision is `GO`, write a new kernel implementation plan using the confirmed packet fixtures and exact protocol note. If the decision is `NO-GO`, continue source/firmware analysis under a separately reviewed investigation plan. In both cases, do not edit the kernel module as part of this plan.

## Plan Completion Criteria

- The userspace harness has no arbitrary-write interface and all active commands are allowlisted confirmed reads.
- Unit tests cover packet parsing, discovery, timeouts, response matching, passive capture, and the CLI safety surface.
- Live evidence is reproducible and raw captures remain outside git.
- The protocol note distinguishes confirmed facts, rejected hypotheses, and unknowns.
- `keychron_battery.c` is unchanged.
- A documented go/no-go decision determines the next plan.
