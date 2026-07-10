import tempfile
import unittest
from pathlib import Path

from tools.keychron_link.discovery import (
    DiscoveryError,
    STATUS_USAGE,
    discover_keychron_links,
    find_raw_channel,
    find_status_channel,
)


class LinkDiscoveryTests(unittest.TestCase):
    def make_hidraw(
        self,
        root: Path,
        name: str,
        descriptor: bytes,
        interface: int,
        vid: int = 0x3434,
        pid: int = 0xD030,
    ):
        device = (
            root
            / "devices"
            / f"1-10:1.{interface}"
            / f"0003:{vid:04X}:{pid:04X}.00{interface}"
        )
        device.mkdir(parents=True)
        (device / "uevent").write_text(
            f"HID_ID=0003:{vid:08X}:{pid:08X}\nHID_NAME=Keychron Keychron Link\n"
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

    def test_rejects_missing_raw_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_hidraw(root, "hidraw2", bytes.fromhex("05 01 09 06"), 2)
            found = discover_keychron_links(root / "class" / "hidraw")
            with self.assertRaises(DiscoveryError):
                find_raw_channel(found)

    def test_rejects_non_keychron_vid_and_unsupported_keychron_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = bytes.fromhex("06 60 ff 09 61")
            self.make_hidraw(root, "hidraw1", raw, 1, vid=0x1234)
            self.make_hidraw(root, "hidraw2", raw, 2, pid=0xD032)
            self.assertEqual(
                discover_keychron_links(root / "class" / "hidraw"),
                (),
            )

    def test_finds_d031_status_channel_by_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_hidraw(root, "hidraw4", STATUS_USAGE, 4, pid=0xD031)
            found = find_status_channel(
                discover_keychron_links(root / "class" / "hidraw")
            )
            self.assertEqual(found.device_path, Path("/dev/hidraw4"))
            self.assertEqual(found.pid, 0xD031)

    def test_status_channel_requires_exactly_one_match(self):
        with self.assertRaises(DiscoveryError):
            find_status_channel(())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_hidraw(root, "hidraw4", STATUS_USAGE, 4)
            self.make_hidraw(root, "hidraw5", STATUS_USAGE, 5)
            found = discover_keychron_links(root / "class" / "hidraw")
            with self.assertRaises(DiscoveryError):
                find_status_channel(found)


if __name__ == "__main__":
    unittest.main()
