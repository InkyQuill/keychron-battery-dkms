import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import json
from pathlib import Path
import sys
from typing import Callable, Iterable, TextIO

from .discovery import (
    HidrawInterface,
    discover_keychron_links,
    find_raw_channel,
    find_status_channel,
    is_raw_channel,
    is_status_channel,
)
from .protocol import (
    decode_receiver_firmware,
    decode_receiver_state,
    decode_receiver_version,
)
from .transport import HidrawTransport, PacketRecord


def capture_seconds(text: str) -> float:
    value = float(text)
    if not 0 < value <= 300:
        raise argparse.ArgumentTypeError("seconds must be in (0, 300]")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely inspect supported Keychron Link receivers"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("list", help="list supported receiver interfaces")
    subparsers.add_parser("info", help="read confirmed receiver information")
    capture = subparsers.add_parser(
        "capture", help="passively capture raw and status traffic"
    )
    capture.add_argument("--seconds", required=True, type=capture_seconds)
    capture.add_argument("--output", required=True, type=Path)
    return parser


def _identity(interface: HidrawInterface) -> str:
    return f"{interface.vid:04x}:{interface.pid:04x}"


def _interface_json(interface: HidrawInterface) -> dict:
    channels = []
    if is_raw_channel(interface):
        channels.append("raw")
    if is_status_channel(interface):
        channels.append("status")
    return {
        "channels": channels,
        "device": str(interface.device_path),
        "interface_number": interface.interface_number,
        "receiver": _identity(interface),
    }


def _matching_response(
    transcript: Iterable[PacketRecord], command: int
) -> bytes:
    for record in transcript:
        if (
            record.direction == "in"
            and len(record.data) == 32
            and record.data[0] == command
        ):
            return record.data
    raise RuntimeError(f"transport returned no matching 0x{command:02x} response")


def _info_json(
    interfaces: tuple[HidrawInterface, ...],
    transport_factory: Callable[..., HidrawTransport],
) -> dict:
    raw = find_raw_channel(interfaces)
    transport = transport_factory(raw, channel="raw")

    version = decode_receiver_version(
        _matching_response(transport.exchange_confirmed_read(0xB1), 0xB1)
    )
    state = decode_receiver_state(
        _matching_response(transport.exchange_confirmed_read(0xB2), 0xB2)
    )
    firmware = decode_receiver_firmware(
        _matching_response(transport.exchange_confirmed_read(0xB3), 0xB3)
    )
    return {
        "feature_flags": list(version.feature_flags),
        "firmware": firmware.text,
        "protocol_version": version.version,
        "receiver": _identity(raw),
        "slots": [
            {
                "connected": slot.connected,
                "device": f"{slot.vid:04x}:{slot.pid:04x}",
            }
            for slot in state.slots
        ],
    }


def _capture_records(
    interfaces: tuple[HidrawInterface, ...],
    seconds: float,
    transport_factory: Callable[..., HidrawTransport],
) -> list[PacketRecord]:
    raw = find_raw_channel(interfaces)
    status = find_status_channel(interfaces)
    transports = (
        transport_factory(raw, channel="raw"),
        transport_factory(status, channel="status"),
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        captures = tuple(
            executor.map(lambda transport: tuple(transport.capture(seconds)), transports)
        )
    return sorted(
        (record for capture in captures for record in capture),
        key=lambda record: record.timestamp_ns,
    )


def _write_capture(path: Path, records: Iterable[PacketRecord]) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            value = {
                "channel": record.channel,
                "direction": record.direction,
                "hex": record.data.hex(),
                "timestamp_ns": record.timestamp_ns,
            }
            print(json.dumps(value, sort_keys=True), file=output)


def main(
    argv=None,
    discover=discover_keychron_links,
    transport_factory=HidrawTransport,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = build_parser()
    with redirect_stderr(stderr):
        args = parser.parse_args(argv)

    interfaces = tuple(discover())
    if args.action == "list":
        print(
            json.dumps([_interface_json(value) for value in interfaces], sort_keys=True),
            file=stdout,
        )
    elif args.action == "info":
        print(
            json.dumps(_info_json(interfaces, transport_factory), sort_keys=True),
            file=stdout,
        )
    elif args.action == "capture":
        _write_capture(
            args.output,
            _capture_records(interfaces, args.seconds, transport_factory),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
