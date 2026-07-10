import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tools.keychron_link.cli import build_parser, main
from tools.keychron_link.discovery import HidrawInterface, RAW_USAGE, STATUS_USAGE
from tools.keychron_link.transport import PacketRecord, TransportReadError


class FakeTransport:
    transcripts = {}
    captures = {}
    commands = []

    def __init__(self, interface, channel="raw"):
        self.interface = interface
        self.channel = channel

    def exchange_confirmed_read(self, command):
        self.commands.append(command)
        return self.transcripts[command]

    def capture(self, duration):
        return iter(self.captures[self.channel])


class LinkCliTests(unittest.TestCase):
    def setUp(self):
        FakeTransport.commands = []
        self.raw = HidrawInterface(
            device_path=Path("/dev/hidraw42"),
            sysfs_path=Path("/sys/devices/raw"),
            vid=0x3434,
            pid=0xD030,
            interface_number=3,
            report_descriptor=RAW_USAGE,
        )
        self.status = HidrawInterface(
            device_path=Path("/dev/hidraw43"),
            sysfs_path=Path("/sys/devices/status"),
            vid=0x3434,
            pid=0xD030,
            interface_number=4,
            report_descriptor=STATUS_USAGE,
        )
        self.discover = lambda: (self.raw, self.status)

    def test_parser_exposes_only_list_info_and_capture(self):
        parser = build_parser()
        self.assertEqual(
            set(parser._subparsers._group_actions[0].choices),
            {"list", "info", "capture"},
        )

    def test_info_emits_json_with_version_state_and_firmware(self):
        FakeTransport.transcripts = {
            0xB1: (
                PacketRecord(1, "in", "raw", b"\xe2" + bytes(31)),
                PacketRecord(2, "in", "raw", b"\xb1\x01\x00" + bytes(29)),
            ),
            0xB2: (
                PacketRecord(
                    3,
                    "in",
                    "raw",
                    bytes.fromhex("b2 01 34 34 60 09 01") + bytes(25),
                ),
            ),
            0xB3: (
                PacketRecord(4, "in", "raw", b"\xb3d.3.0\0" + bytes(25)),
            ),
        }
        stdout = io.StringIO()

        self.assertEqual(
            main(
                ["info"],
                discover=self.discover,
                transport_factory=FakeTransport,
                stdout=stdout,
                stderr=io.StringIO(),
            ),
            0,
        )

        value = json.loads(stdout.getvalue())
        self.assertEqual(value["receiver"], "3434:d030")
        self.assertEqual(value["protocol_version"], 1)
        self.assertEqual(value["feature_flags"], [0, 0])
        self.assertEqual(value["firmware"], "d.3.0")
        self.assertEqual(
            value["slots"][0],
            {"connected": True, "device": "3434:0960"},
        )
        self.assertEqual(FakeTransport.commands, [0xB1, 0xB2, 0xB3])

    def test_info_output_preserves_complete_timestamped_transcript(self):
        FakeTransport.transcripts = {
            0xB1: (
                PacketRecord(1, "out", "raw", b"\0\xb1" + bytes(31)),
                PacketRecord(2, "in", "raw", b"\xb1\x01\0" + bytes(29)),
            ),
            0xB2: (
                PacketRecord(3, "out", "raw", b"\0\xb2" + bytes(31)),
                PacketRecord(4, "in", "raw", b"\xb2\x01" + bytes(30)),
            ),
            0xB3: (
                PacketRecord(5, "out", "raw", b"\0\xb3" + bytes(31)),
                PacketRecord(6, "in", "raw", b"\xb3d.3.0\0" + bytes(25)),
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "info.jsonl"
            main(
                ["info", "--output", str(output)],
                discover=self.discover,
                transport_factory=FakeTransport,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
            values = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual([value["timestamp_ns"] for value in values], list(range(1, 7)))
        self.assertEqual([value["direction"] for value in values], ["out", "in"] * 3)
        self.assertEqual(FakeTransport.commands, [0xB1, 0xB2, 0xB3])

    def test_capture_emits_sorted_json_lines_with_hex_not_raw_binary(self):
        FakeTransport.captures = {
            "raw": (PacketRecord(20, "in", "raw", b"\xb2\x00"),),
            "status": (PacketRecord(10, "in", "status", b"\x54\xe2"),),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            self.assertEqual(
                main(
                    ["capture", "--seconds", "1", "--output", str(output)],
                    discover=self.discover,
                    transport_factory=FakeTransport,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                ),
                0,
            )
            values = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual([value["timestamp_ns"] for value in values], [10, 20])
        self.assertEqual(
            set(values[0]), {"timestamp_ns", "direction", "channel", "hex"}
        )
        self.assertEqual(values[0]["hex"], "54e2")
        self.assertNotIn("data", values[0])

    def test_capture_opens_output_before_concurrent_capture_starts(self):
        barrier = threading.Barrier(2)
        output_holder = {}

        class ConcurrentTransport(FakeTransport):
            def capture(inner_self, duration):
                self.assertTrue(output_holder["path"].exists())
                barrier.wait(timeout=1)
                return iter(
                    (PacketRecord(1, "in", inner_self.channel, b"\x01"),)
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            output_holder["path"] = output
            main(
                ["capture", "--seconds", "1", "--output", str(output)],
                discover=self.discover,
                transport_factory=ConcurrentTransport,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
            values = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(barrier.n_waiting, 0)
        self.assertEqual({value["channel"] for value in values}, {"raw", "status"})

    def test_capture_preserves_records_when_worker_fails(self):
        first = PacketRecord(10, "in", "raw", b"\xb2\x00")

        class FailingTransport(FakeTransport):
            def capture(inner_self, duration):
                if inner_self.channel == "raw":
                    yield first
                    raise TransportReadError("synthetic read failure", (first,))
                yield PacketRecord(20, "in", "status", b"\x54\xe2")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            with self.assertRaises(TransportReadError):
                main(
                    ["capture", "--seconds", "1", "--output", str(output)],
                    discover=self.discover,
                    transport_factory=FailingTransport,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            values = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertIn(
            {"channel": "raw", "direction": "in", "hex": "b200", "timestamp_ns": 10},
            values,
        )
        self.assertIn(
            {"channel": "status", "direction": "in", "hex": "54e2", "timestamp_ns": 20},
            values,
        )

    def test_info_preserves_failed_command_transcript_in_output(self):
        first = PacketRecord(1, "out", "raw", b"\0\xb1" + bytes(31))

        class FailingInfoTransport(FakeTransport):
            def exchange_confirmed_read(inner_self, command):
                raise TransportReadError("synthetic info failure", (first,))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "info.jsonl"
            with self.assertRaises(TransportReadError):
                main(
                    ["info", "--output", str(output)],
                    discover=self.discover,
                    transport_factory=FailingInfoTransport,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            values = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(values, [{
            "channel": "raw",
            "direction": "out",
            "hex": first.data.hex(),
            "timestamp_ns": 1,
        }])

    def test_capture_rejects_nonpositive_or_more_than_300_seconds(self):
        for seconds in ("0", "301"):
            with self.subTest(seconds=seconds):
                stderr = io.StringIO()
                with self.assertRaises(SystemExit) as caught:
                    main(
                        ["capture", "--seconds", seconds, "--output", "ignored"],
                        discover=self.discover,
                        transport_factory=FakeTransport,
                        stdout=io.StringIO(),
                        stderr=stderr,
                    )
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("seconds must be in (0, 300]", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
