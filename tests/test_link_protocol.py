import unittest

from tools.keychron_link.protocol import (
    ProtocolError,
    build_receiver_request,
    decode_receiver_firmware,
    decode_receiver_state,
    decode_receiver_version,
    decode_state_notification,
)


class LinkProtocolTests(unittest.TestCase):
    def test_builds_only_32_byte_confirmed_reads(self):
        self.assertEqual(build_receiver_request(0xB1), bytes([0xB1]) + bytes(31))
        self.assertEqual(build_receiver_request(0xB2), bytes([0xB2]) + bytes(31))
        self.assertEqual(build_receiver_request(0xB3), bytes([0xB3]) + bytes(31))
        with self.assertRaises(ProtocolError):
            build_receiver_request(0xBA)

    def test_decodes_live_receiver_version(self):
        value = decode_receiver_version(bytes.fromhex("b1 01 00") + bytes(29))
        self.assertEqual(value.version, 1)
        self.assertEqual(value.feature_flags, (0, 0))

    def test_decodes_live_v6_max_slot_as_little_endian(self):
        packet = bytes.fromhex("b2 01 34 34 60 09 01") + bytes(25)
        value = decode_receiver_state(packet)
        self.assertEqual((value.slots[0].vid, value.slots[0].pid), (0x3434, 0x0960))
        self.assertTrue(value.slots[0].connected)

    def test_decodes_live_receiver_firmware(self):
        packet = bytes.fromhex(
            "b3 64 2e 33 2e 30 20 4a 75 6e 20 20 37 20 32 30 "
            "32 34 20 31 32 3a 33 32 3a 31 38 00 00 00 00 00"
        )
        self.assertEqual(
            decode_receiver_firmware(packet).text,
            "d.3.0 Jun  7 2024 12:32:18",
        )

    def test_decodes_status_channel_notification_after_report_id(self):
        packet = bytes.fromhex("54 e2 01 02") + bytes(17)
        value = decode_state_notification(packet)
        self.assertEqual(value.report_id, 0x54)
        self.assertEqual((value.kind, value.work_mode, value.state), (0xE2, 1, 2))

    def test_rejects_truncated_wrong_command_and_trailing_garbage(self):
        for decoder, packet in (
            (decode_receiver_version, b"\xb1"),
            (decode_receiver_state, bytes(31)),
            (decode_receiver_firmware, b"\xb2" + bytes(31)),
            (decode_state_notification, b"\x54\xe2\x01"),
        ):
            with self.subTest(decoder=decoder.__name__):
                with self.assertRaises(ProtocolError):
                    decoder(packet)


if __name__ == "__main__":
    unittest.main()
