"""Offline Aroma-Link week verification using synthetic protocol reports."""
import unittest

from tests.offline import MODULES


Protocol = MODULES.protocol_ble.AromaLinkBleProtocol
QUERY = bytes((MODULES.const.AL_CMD_QUERY, MODULES.const.AL_SUB_QUERY_SCHEDULES))


def synthetic_week():
    """Seven distinct days of five 9-byte slots, including disabled raw values."""
    return b"".join(
        bytes((8 + slot, day, 15 + slot, day, 0x32 if slot < 2 else 0x70,
               0, 10 + day + slot, 0, 130 + day + slot))
        for day in range(7) for slot in range(5)
    )


def read_packet(protocol, packet):
    """Deliver fragmented reports through the real parser."""
    outputs = [protocol.parse_notification(packet[i:i + 20])
               for i in range(0, len(packet), 20)]
    return outputs[-1]


class AromaLinkWeekTests(unittest.TestCase):
    def setUp(self):
        self.protocol = Protocol()
        self.expected = synthetic_week()

    def fresh_report(self, week):
        return read_packet(self.protocol, self.protocol._build_packet(QUERY + week))

    def test_complete_fragmented_report_preserves_all_35_slots(self):
        report = self.fresh_report(self.expected)
        self.assertEqual(report["week_schedule_bytes"], self.expected)
        self.assertTrue(Protocol.week_schedule_matches(report, self.expected))
        self.assertEqual([len(day) for day in report["week_slots"]], [5] * 7)

    def test_changed_target_and_untouched_records_cannot_confirm(self):
        for offset in (0, 5, 9 + 7, 45 * 6 + 8):
            with self.subTest(offset=offset):
                actual = bytearray(self.expected)
                actual[offset] ^= 1
                self.assertFalse(Protocol.week_schedule_matches(
                    self.fresh_report(bytes(actual)), self.expected,
                ))

    def test_flag_bits_are_compared_without_normalization(self):
        for offset, bit in ((4, 0x01), (4, 0x10), (9 * 3 + 4, 0x08)):
            with self.subTest(offset=offset, bit=bit):
                actual = bytearray(self.expected)
                actual[offset] ^= bit
                self.assertFalse(Protocol.week_schedule_matches(
                    self.fresh_report(bytes(actual)), self.expected,
                ))

    def test_length_must_be_exact(self):
        for week in (self.expected[:-1], self.expected + b"|"):
            with self.subTest(length=len(week)):
                self.assertFalse(Protocol.week_schedule_matches(
                    self.fresh_report(week), self.expected,
                ))
        good = self.fresh_report(self.expected)
        self.assertFalse(Protocol.week_schedule_matches(good, self.expected[:-1]))
        self.assertFalse(Protocol.week_schedule_matches(good, self.expected + b"|"))

    def test_bad_checksum_truncated_frame_and_other_register_never_confirm(self):
        good = self.protocol._build_packet(QUERY + self.expected)
        bad_checksum = good[:3] + bytes((good[3] ^ 1,)) + good[4:]
        other = self.protocol._build_packet(
            bytes((MODULES.const.AL_CMD_QUERY, MODULES.const.AL_SUB_WORK_FREQUENCY,
                   0, 0, 10, 0, 120, 0x31)))
        for bad in (bad_checksum, good[:-20], other):
            with self.subTest(length=len(bad)):
                self.assertFalse(Protocol.week_schedule_matches(
                    read_packet(self.protocol, bad), self.expected,
                ))

    def test_legacy_bare_report_remains_visible_but_unverified(self):
        report = self.protocol.parse_notification(QUERY + self.expected)
        self.assertIn("week_slots", report)
        self.assertNotIn("week_schedule_bytes", report)
        self.assertFalse(Protocol.week_schedule_matches(report, self.expected))
