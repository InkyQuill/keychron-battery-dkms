import os
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from tools.keychron_link.discovery import HidrawInterface
from tools.keychron_link.protocol import ProtocolError
from tools.keychron_link.transport import (
    HidrawTransport,
    TransportOpenError,
    TransportReadError,
    TransportTimeout,
    TransportWaitError,
    TransportWriteError,
)


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

    def test_timeout_preserves_outgoing_and_unmatched_packets(self):
        notification = b"\xe2" + bytes(20)
        self.waiter.side_effect = (True, False)
        self.reader.return_value = notification

        with self.assertRaises(TransportTimeout) as caught:
            self.transport().exchange_confirmed_read(0xB2, attempts=1)

        request = b"\x00\xb2" + bytes(31)
        transcript = getattr(caught.exception, "transcript", None)
        self.assertIsInstance(transcript, tuple)
        self.assertEqual(
            [record.data for record in transcript],
            [request, notification],
        )

    def test_short_write_fails_immediately_and_preserves_transcript(self):
        self.writer.return_value = 12
        self.waiter.return_value = False

        with self.assertRaises(TransportWriteError) as caught:
            self.transport().exchange_confirmed_read(0xB3)

        request = b"\x00\xb3" + bytes(31)
        self.assertIsInstance(caught.exception.transcript, tuple)
        self.assertEqual(
            [record.data for record in caught.exception.transcript],
            [request[:12]],
        )
        self.waiter.assert_not_called()
        self.reader.assert_not_called()
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

    def test_exchange_wraps_open_oserror_with_empty_immutable_transcript(self):
        self.opener.side_effect = PermissionError("denied")

        with self.assertRaises(TransportOpenError) as caught:
            self.transport().exchange_confirmed_read(0xB1)

        self.assertEqual(caught.exception.transcript, ())
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_exchange_wraps_write_wait_and_read_oserrors_with_transcript(self):
        request = b"\x00\xb1" + bytes(31)
        cases = (
            (self.writer, PermissionError("write denied"), TransportWriteError, []),
            (self.waiter, OSError("wait failed"), TransportWaitError, [request]),
            (self.reader, OSError("read failed"), TransportReadError, [request]),
        )
        for failing_mock, error, error_type, expected in cases:
            with self.subTest(error_type=error_type.__name__):
                self.setUp()
                failing_mock = {
                    TransportWriteError: self.writer,
                    TransportWaitError: self.waiter,
                    TransportReadError: self.reader,
                }[error_type]
                if error_type is TransportReadError:
                    self.waiter.return_value = True
                failing_mock.side_effect = error

                with self.assertRaises(error_type) as caught:
                    self.transport().exchange_confirmed_read(0xB1)

                self.assertEqual(
                    [record.data for record in caught.exception.transcript], expected
                )
                self.assertIsInstance(caught.exception.transcript, tuple)
                self.assertIs(caught.exception.__cause__, error)

    def test_capture_error_exposes_every_record_yielded_before_failure(self):
        first = b"\x54\xe2" + bytes(19)
        self.clock.side_effect = (0, 0, 1, 1)
        self.waiter.side_effect = (True, True)
        self.reader.side_effect = (first, OSError("device disappeared"))
        capture = self.transport(channel="status").capture(duration=3e-9)

        self.assertEqual(next(capture).data, first)
        with self.assertRaises(TransportReadError) as caught:
            next(capture)

        self.assertEqual(
            [record.data for record in caught.exception.transcript], [first]
        )
        self.assertIsInstance(caught.exception.transcript, tuple)


if __name__ == "__main__":
    unittest.main()
