import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
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
from .transport import HidrawTransport, PacketRecord, TransportError


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
    info = subparsers.add_parser("info", help="read confirmed receiver information")
    info.add_argument(
        "--output",
        type=Path,
        help="write the complete timestamped B1/B2/B3 transcript as JSONL",
    )
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
    record_sink: Callable[[PacketRecord], None] = lambda record: None,
) -> dict:
    raw = find_raw_channel(interfaces)
    transport = transport_factory(raw, channel="raw")

    def confirmed_read(command: int) -> bytes:
        try:
            transcript = transport.exchange_confirmed_read(command)
        except TransportError as error:
            for record in error.transcript:
                record_sink(record)
            raise
        for record in transcript:
            record_sink(record)
        return _matching_response(transcript, command)

    version = decode_receiver_version(confirmed_read(0xB1))
    state = decode_receiver_state(confirmed_read(0xB2))
    firmware = decode_receiver_firmware(confirmed_read(0xB3))
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


def _record_json(record: PacketRecord) -> dict:
    return {
        "channel": record.channel,
        "direction": record.direction,
        "hex": record.data.hex(),
        "timestamp_ns": record.timestamp_ns,
    }


def _write_record(output: TextIO, record: PacketRecord, *, durable: bool) -> None:
    print(json.dumps(_record_json(record), sort_keys=True), file=output)
    output.flush()
    if durable:
        os.fsync(output.fileno())


def _finalize_capture(path: Path) -> None:
    values = [json.loads(line) for line in path.read_text().splitlines()]
    values.sort(key=lambda value: value["timestamp_ns"])
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            for value in values:
                print(json.dumps(value, sort_keys=True), file=output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _capture_to_path(
    interfaces: tuple[HidrawInterface, ...],
    seconds: float,
    transport_factory: Callable[..., HidrawTransport],
    path: Path,
) -> None:
    raw = find_raw_channel(interfaces)
    status = find_status_channel(interfaces)
    transports = (
        transport_factory(raw, channel="raw"),
        transport_factory(status, channel="status"),
    )
    with path.open("w", encoding="utf-8") as output:
        output.flush()
        os.fsync(output.fileno())
        lock = threading.Lock()

        def capture(transport: HidrawTransport) -> None:
            for record in transport.capture(seconds):
                with lock:
                    _write_record(output, record, durable=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = tuple(executor.submit(capture, transport) for transport in transports)
            for future in futures:
                future.result()
    _finalize_capture(path)


def _run_info(
    interfaces: tuple[HidrawInterface, ...],
    transport_factory: Callable[..., HidrawTransport],
    path: Path | None,
) -> dict:
    if path is None:
        return _info_json(interfaces, transport_factory)
    with path.open("w", encoding="utf-8") as output:
        output.flush()
        os.fsync(output.fileno())
        return _info_json(
            interfaces,
            transport_factory,
            lambda record: _write_record(output, record, durable=True),
        )


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
            json.dumps(
                _run_info(interfaces, transport_factory, args.output), sort_keys=True
            ),
            file=stdout,
        )
    elif args.action == "capture":
        _capture_to_path(interfaces, args.seconds, transport_factory, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
