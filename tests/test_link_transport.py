import os
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from tools.keychron_link.discovery import HidrawInterface
from tools.keychron_link.protocol import ProtocolError
from tools.keychron_link.transport import HidrawTransport, TransportTimeout


class LinkTransportTests(unittest.TestCase):
    def setUp(self):
        self.interface = HidrawInterface(
            device_path=Path("/dev/hidraw42"),
            sysfs_path=Path("/sys/devices/fake"),
            vid=0x3434,
            pid=0xD030,
            interface_number=3,
            report_descriptor=b"",
        )
        self.opener = Mock(return_value=42)
        self.reader = Mock()
        self.writer = Mock(return_value=33)
        self.waiter = Mock()
        self.clock = Mock(return_value=100)
        self.closer = Mock()

    def transport(self, channel="raw"):
        return HidrawTransport(
            self.interface,
            channel=channel,
            opener=self.opener,
            reader=self.reader,
            writer=self.writer,
            waiter=self.waiter,
            monotonic_ns=self.clock,
            closer=self.closer,
        )

    def test_exchange_prefixes_report_zero_and_matches_command(self):
        notification = b"\xe2" + bytes(20)
        response = b"\xb2" + bytes(31)
        self.waiter.side_effect = (True, True)
        self.reader.side_effect = (notification, response)

        records = self.transport().exchange_confirmed_read(0xB2)

        request = b"\x00\xb2" + bytes(31)
        self.writer.assert_called_once_with(42, request)
        self.assertEqual(
            [(record.direction, record.channel, record.data) for record in records],
            [
                ("out", "raw", request),
                ("in", "raw", notification),
                ("in", "raw", response),
            ],
        )
        self.closer.assert_called_once_with(42)

    def test_exchange_times_out_after_exact_attempt_count(self):
        self.waiter.side_effect = (False, False, False)

        with self.assertRaises(TransportTimeout):
            self.transport().exchange_confirmed_read(0xB1, attempts=3)

        request = b"\x00\xb1" + bytes(31)
        self.assertEqual(self.writer.call_args_list, [call(42, request)] * 3)
        self.closer.assert_called_once_with(42)

    def test_transport_rejects_non_allowlisted_command_before_opening_device(self):
        with self.assertRaises(ProtocolError):
            self.transport().exchange_confirmed_read(0xBA)

        self.opener.assert_not_called()

    def test_capture_is_read_only(self):
        first = b"\xe2" + bytes(20)
        second = b"\x54\xe2" + bytes(19)
        self.clock.side_effect = (0, 0, 1, 1, 2, 3)
        self.waiter.side_effect = (True, True)
        self.reader.side_effect = (first, second)

        records = tuple(self.transport(channel="status").capture(duration=3e-9))

        self.opener.assert_called_once_with(
            self.interface.device_path,
            os.O_RDONLY | os.O_NONBLOCK,
        )
        self.writer.assert_not_called()
        self.assertEqual([record.timestamp_ns for record in records], [1, 2])
        self.assertEqual([record.data for record in records], [first, second])
        self.assertTrue(all(record.direction == "in" for record in records))
        self.assertTrue(all(record.channel == "status" for record in records))
        self.closer.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
