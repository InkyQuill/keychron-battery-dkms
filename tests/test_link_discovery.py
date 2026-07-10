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
