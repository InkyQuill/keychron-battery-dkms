from dataclasses import dataclass
import math
import os
import select
import time
from typing import Callable, Iterator

from .discovery import HidrawInterface
from .protocol import build_receiver_request


class TransportError(Exception):
    def __init__(self, message: str, transcript=()) -> None:
        super().__init__(message)
        self.transcript = tuple(transcript)


class TransportTimeout(TimeoutError, TransportError):
    def __init__(self, message: str, transcript=()) -> None:
        TransportError.__init__(self, message, transcript)


class TransportWriteError(TransportError):
    pass


@dataclass(frozen=True)
class PacketRecord:
    timestamp_ns: int
    direction: str
    channel: str
    data: bytes


def _wait_readable(fd: int, timeout: float) -> bool:
    readable, _, _ = select.select([fd], [], [], timeout)
    return bool(readable)


def _duration_ns(value: float, name: str, *, allow_zero: bool = False) -> int:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        relation = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a finite {relation} number")
    return int(value * 1_000_000_000)


class HidrawTransport:
    def __init__(
        self,
        interface: HidrawInterface,
        channel: str = "raw",
        *,
        opener: Callable[[os.PathLike[str], int], int] = os.open,
        reader: Callable[[int, int], bytes] = os.read,
        writer: Callable[[int, bytes], int] = os.write,
        waiter: Callable[[int, float], bool] = _wait_readable,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        closer: Callable[[int], None] = os.close,
    ) -> None:
        self._device_path = interface.device_path
        self._channel = channel
        self._open = opener
        self._read = reader
        self._write = writer
        self._wait = waiter
        self._monotonic_ns = monotonic_ns
        self._close = closer

    def exchange_confirmed_read(
        self,
        command: int,
        timeout: float = 1.0,
        attempts: int = 3,
    ) -> tuple[PacketRecord, ...]:
        payload = build_receiver_request(command)
        timeout_ns = _duration_ns(timeout, "timeout")
        if attempts <= 0:
            raise ValueError("attempts must be positive")

        records = []
        fd = self._open(self._device_path, os.O_RDWR | os.O_NONBLOCK)
        try:
            request = b"\x00" + payload
            for _ in range(attempts):
                deadline_ns = self._monotonic_ns() + timeout_ns
                written = self._write(fd, request)
                recorded_data = (
                    request[:written]
                    if isinstance(written, int) and written >= 0
                    else b""
                )
                records.append(
                    PacketRecord(
                        self._monotonic_ns(), "out", self._channel, recorded_data
                    )
                )
                if written != len(request):
                    raise TransportWriteError(
                        f"short write: expected {len(request)} bytes, wrote {written}",
                        records,
                    )

                while True:
                    remaining_ns = deadline_ns - self._monotonic_ns()
                    if remaining_ns <= 0 or not self._wait(
                        fd, remaining_ns / 1_000_000_000
                    ):
                        break

                    try:
                        packet = self._read(fd, 32)
                    except BlockingIOError:
                        continue
                    if not packet:
                        continue
                    records.append(
                        PacketRecord(
                            self._monotonic_ns(), "in", self._channel, packet
                        )
                    )
                    if len(packet) == 32 and packet[0] == command:
                        return tuple(records)
        finally:
            self._close(fd)

        raise TransportTimeout(
            f"command 0x{command:02x} timed out after {attempts} attempts",
            records,
        )

    def capture(self, duration: float) -> Iterator[PacketRecord]:
        duration_ns = _duration_ns(duration, "duration", allow_zero=True)
        fd = self._open(self._device_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            deadline_ns = self._monotonic_ns() + duration_ns
            while True:
                remaining_ns = deadline_ns - self._monotonic_ns()
                if remaining_ns <= 0 or not self._wait(
                    fd, remaining_ns / 1_000_000_000
                ):
                    return

                try:
                    packet = self._read(fd, 32)
                except BlockingIOError:
                    continue
                if packet:
                    yield PacketRecord(
                        self._monotonic_ns(), "in", self._channel, packet
                    )
        finally:
            self._close(fd)
