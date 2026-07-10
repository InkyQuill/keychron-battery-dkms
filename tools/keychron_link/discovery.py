from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


KEYCHRON_VENDOR_ID = 0x3434
KEYCHRON_LINK_PRODUCT_IDS = frozenset({0xD030, 0xD031})

RAW_USAGE = bytes.fromhex("06 60 ff 09 61")
STATUS_USAGE = bytes.fromhex("05 8c 09 01")


class DiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class HidrawInterface:
    device_path: Path
    sysfs_path: Path
    vid: int
    pid: int
    interface_number: int
    report_descriptor: bytes


def _hid_identity(uevent: str) -> tuple[int, int] | None:
    values = dict(
        line.split("=", 1)
        for line in uevent.splitlines()
        if "=" in line
    )
    try:
        _, vendor, product = values["HID_ID"].split(":")
        return int(vendor, 16), int(product, 16)
    except (KeyError, ValueError):
        return None


def discover_keychron_links(
    sys_class: Path = Path("/sys/class/hidraw"),
) -> tuple[HidrawInterface, ...]:
    interfaces = []
    for entry in sorted(sys_class.iterdir()):
        try:
            hid_device = (entry / "device").resolve()
            identity = _hid_identity((hid_device / "uevent").read_text())
            if identity is None:
                continue

            vid, pid = identity
            if vid != KEYCHRON_VENDOR_ID or pid not in KEYCHRON_LINK_PRODUCT_IDS:
                continue

            interfaces.append(
                HidrawInterface(
                    device_path=Path("/dev") / entry.name,
                    sysfs_path=hid_device,
                    vid=vid,
                    pid=pid,
                    interface_number=int(
                        (hid_device.parent / "bInterfaceNumber").read_text().strip(),
                        16,
                    ),
                    report_descriptor=(hid_device / "report_descriptor").read_bytes(),
                )
            )
        except OSError:
            continue
    return tuple(interfaces)


def is_raw_channel(value: HidrawInterface) -> bool:
    return RAW_USAGE in value.report_descriptor


def is_status_channel(value: HidrawInterface) -> bool:
    return STATUS_USAGE in value.report_descriptor


def _find_channel(
    interfaces: Iterable[HidrawInterface],
    classifier,
    channel_name: str,
) -> HidrawInterface:
    matches = tuple(value for value in interfaces if classifier(value))
    if len(matches) != 1:
        raise DiscoveryError(
            f"expected exactly one {channel_name} channel, found {len(matches)}"
        )
    return matches[0]


def find_raw_channel(interfaces: Iterable[HidrawInterface]) -> HidrawInterface:
    return _find_channel(interfaces, is_raw_channel, "raw")


def find_status_channel(interfaces: Iterable[HidrawInterface]) -> HidrawInterface:
    return _find_channel(interfaces, is_status_channel, "status")
