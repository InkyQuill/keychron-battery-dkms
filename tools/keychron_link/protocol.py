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
