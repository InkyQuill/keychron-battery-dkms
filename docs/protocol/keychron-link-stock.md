# Stock Keychron Link Protocol Evidence

## Tested Hardware

- Receiver: Keychron Link USB-A `3434:d030`.
- Receiver firmware: `d.3.0 Jun  7 2024 12:32:18`.
- Receiver protocol version: `1`; feature bytes: `[0x00, 0x00]`.
- Paired keyboard: stock-firmware Keychron V6 Max `3434:0960`, connected in
  slot 0 during the information read.
- Live evidence was collected on 2026-07-10 without a keyboard or receiver
  firmware change.

## HID Channels

Discovery found four receiver hidraw interfaces. The evidence tool selected
exactly one vendor raw channel and one status channel by HID descriptor usage,
not by a fixed interface number:

| Channel | Live path | Interface | Usage page / usage | Captured report size |
| --- | --- | ---: | --- | ---: |
| status | `/dev/hidraw7` | 1 | `0x8c / 0x01` | 21 bytes |
| raw | `/dev/hidraw9` | 3 | `0xff60 / 0x61` | 32 bytes |

Interfaces 0 (`/dev/hidraw6`) and 2 (`/dev/hidraw8`) did not match either
vendor-channel descriptor signature and were not captured. In particular, the
capture did not open or record normal keyboard input interfaces.

## Confirmed Read Commands

The only active protocol operations were the three allowlisted receiver reads.
Each request is exactly 32 bytes: the command at offset 0 followed by zeroes at
offsets 1--31.

The live `info --output` evidence records the actual hidraw write as 33 bytes:
report ID `0x00`, followed by that 32-byte receiver request. The receiver input
response is 32 bytes without the leading report-ID byte. The three exchanges
were executed sequentially in `0xb1`, `0xb2`, `0xb3` order and produced these
complete request/response pairs:

| Command | Outbound hidraw bytes | Inbound response bytes |
| --- | --- | --- |
| `0xb1` | `00b100000000000000000000000000000000000000000000000000000000000000` | `b101000000000000000000000000000000000000000000000000000000000000` |
| `0xb2` | `00b200000000000000000000000000000000000000000000000000000000000000` | `b201343460090100000000000000000000000000000000000000000000000000` |
| `0xb3` | `00b300000000000000000000000000000000000000000000000000000000000000` | `b3642e332e30204a756e20203720323032342031323a33323a31380000000000` |

| Command | 32-byte response layout | Live decoded result |
| --- | --- | --- |
| `0xb1` | offset 0 command; offsets 1--2 little-endian protocol version; offsets 3--4 feature bytes; offsets 5--31 reserved/zero in the observed response | version `1`, features `[0x00, 0x00]` |
| `0xb2` | offset 0 command; offset 1 observed as `0x01` (meaning unconfirmed); three five-byte slots at offsets 2--6, 7--11, and 12--16; offsets 17--31 reserved/zero in the observed response | slot 0 `3434:0960`, connected; slots 1 and 2 `0000:0000`, disconnected |
| `0xb3` | offset 0 command; offsets 1--31 NUL-terminated ASCII | `d.3.0 Jun  7 2024 12:32:18` |

Each `0xb2` slot is laid out as little-endian VID at slot offsets +0--1,
little-endian PID at +2--3, and connection flag at +4 (`0x00` disconnected,
`0x01` connected in this capture).

## Passive Reconnect Reports

The 20-second idle baseline produced no vendor-channel reports. The controlled
60-second off, return-to-2.4-GHz, and three-key sequence produced eight inbound
reports: two status-state values, each followed by the corresponding `0xb2`
slot state. Duplicate reports were preserved rather than deduplicated.

The 21-byte status layout was:

| Offset | Observed value | Interpretation |
| ---: | --- | --- |
| 0 | `0x54` | HID report ID |
| 1 | `0xe2` | state-notification kind |
| 2 | `0x01` | work mode |
| 3 | `0x02`, then `0x01` | state; the off/on sequence correlates `0x02` with disconnected and `0x01` with connected |
| 4--20 | all `0x00` | unchanged/reserved in this capture |

The raw reconnect reports used the confirmed 32-byte `0xb2` layout. Slot 0 was
first `34 34 60 09 00` (VID `3434`, PID `0960`, disconnected), then
`34 34 60 09 01` (the same device, connected); all later slot and reserved
bytes were zero. The capture contains no normal keycode reports or typed text:
all eight records are either the 21-byte `54 e2 ...` status form or the 32-byte
`b2 ...` receiver-state form.

### Observed packet-shape comparison

The idle baseline contained no report boundary to compare. The reconnect file
contained exactly the following two packet shapes; the full observed bytes are
shown so that no omitted byte is inferred:

| Channel | Length | Report / command byte | Disconnected observation | Connected observation | Changed byte | Connection-state correlation |
| --- | ---: | --- | --- | --- | --- | --- |
| status | 21 | report ID `0x54` at offset 0; kind `0xe2` at offset 1 | `54e201020000000000000000000000000000000000` (1 record) | `54e201010000000000000000000000000000000000` (3 records) | offset 3: `0x02` to `0x01`; every other byte was unchanged | Yes, for this single controlled off/on sequence |
| raw | 32 | command `0xb2` at offset 0 | `b201343460090000000000000000000000000000000000000000000000000000` (2 records) | `b201343460090100000000000000000000000000000000000000000000000000` (2 records) | offset 6, the slot-0 connection flag: `0x00` to `0x01`; every other byte was unchanged | Yes, for this single controlled off/on sequence |

The capture therefore has four unique byte strings but only two report shapes:
one disconnected and one connected value for each shape. Duplicate boundaries
do not add a second reconnect cycle.

## Battery Data Boundary

No observed byte can be identified plausibly as capacity. The idle capture was
empty. During reconnect, only status offset 3 and raw `0xb2` slot-0 offset 6
changed, and both changes track the binary disconnected/connected transition.
No byte produced a value correlated with the keyboard's Fn+B 10% battery
bucket, so this evidence does not expose or estimate battery capacity.

## Rejected Hypotheses

- Stock Link firmware `d.3.0` did not emit a periodic battery value on either
  captured vendor channel during the 20-second idle baseline.
- The reconnect-only `0xe2` state byte and `0xb2` slot connection flag are not
  capacity: they changed with link state and have no Fn+B correlation.
- Receiver command `0xb3` is not a capacity read on this channel; its confirmed
  response is the receiver firmware ASCII string.
- Ordinary keyboard traffic does not provide a capacity side channel in this
  evidence; the three key presses generated no captured vendor-channel keycode
  or text reports.

## Evidence Files and Redaction

The runtime evidence files are intentionally gitignored and are not committed:

| File | Lines / bytes | SHA-256 |
| --- | ---: | --- |
| `artifacts/keychron-link/info.jsonl` | 6 / 885 | `76fec7d1246c02cea519531d0a8c49fef6964f0ff1339e3b70338fadc8401a87` |
| `artifacts/keychron-link/baseline.jsonl` | 0 / 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `artifacts/keychron-link/reconnect.jsonl` | 8 / 1092 | `5905736482d58029894b804ba627fad490ff5354a3ea3e863774dd10db5cfdf4` |

Each nonempty JSONL record contains only a monotonic timestamp, direction,
channel name, and hex-encoded vendor-channel report. `info.jsonl` is the active,
timestamped B1/B2/B3 transcript shown above; the other two files are passive
captures. Device metadata is recorded above. No normal keyboard interface,
keycode, typed text, or arbitrary binary payload outside the two selected vendor
channels is included.

## Kernel Go/No-Go Gate

**NO-GO: stock capacity not yet retrievable.** No observed field or confirmed
read response is a capacity candidate, so the acceptance test fails as follows:

| Acceptance criterion | Result | Missing evidence |
| --- | --- | --- |
| Integer in `0--100` | Fail | No field was identified as capacity; small connection-state values are not capacity candidates merely because they fall in this numeric range. |
| Agrees with the keyboard's Fn+B 10% bucket | Fail | No observed field was correlated with the Fn+B indication. |
| Reproduced after a second reconnect or later charge change | Fail | The evidence contains one off/on sequence and no later charge-change observation. Duplicate reports within that sequence are not an independent reproduction. |
| Reading it does not interrupt normal keyboard input | Fail / untested | No capacity read exists to exercise while typing; the three ordinary key presses only show that passive vendor-channel capture recorded no keycode or text reports. |
| Request, response matching, timing, and freshness specified byte-for-byte | Fail | There is no confirmed capacity request or response, so none of its matching, timing, or freshness rules can be specified. |

The captures confirm receiver identity, metadata reads, and reconnect state, but
they do not demonstrate a stock battery percentage. The next work is continued
source or firmware analysis under a separately reviewed investigation plan, not
a kernel implementation plan. Do not change the kernel driver until one field
or confirmed read response satisfies all five criteria above.
