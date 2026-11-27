"""Offline checks for the Scent Master Pro protocol.

The expected frames are copied from a live iPhone HCI trace of a `-TJ-` unit
(see PROTOCOL.md), so these tests pin the wire format to real hardware rather
than to the implementation.
"""
from datetime import datetime
from types import SimpleNamespace
import unittest

from tests.offline import MODULES

ScentMaster = MODULES.protocol_ble.ScentMasterProtocol
ScheduleSlot = MODULES.protocol_ble.ScheduleSlot
DeviceType = MODULES.const.DeviceType
detect_device_type = MODULES.protocol_ble.detect_device_type

# Enabled, mask 0x52 (Tue+Fri+Sun), 11:00-19:00, work 5 s, pause 900 s.
STATUS_PAYLOAD = bytes([
    0x01, 0x52, 0x0B, 0x00, 0x13, 0x00, 0x00, 0x05, 0x03, 0x84,
]) + bytes(40)


def notification(cmd: int, payload: bytes) -> bytes:
    """Build a device->app frame the way the firmware does."""
    body = bytes([0x11, cmd, 0x05, len(payload)]) + payload
    return bytes([0x55]) + body + bytes([sum(body) & 0xFF])


class ScentMasterFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = ScentMaster()

    def test_reads_match_trace(self) -> None:
        self.assertEqual(self.protocol.build_query(), bytes.fromhex("5510D100E1"))
        self.assertEqual(self.protocol.build_read_queries()[0], bytes.fromhex("5510D100E1"))
        self.assertIn(bytes.fromhex("5510D200E2"), self.protocol.build_read_queries())

    def test_time_sync_matches_trace(self) -> None:
        # Sunday 2026-11-22 14:31:50 -> day flag 0x40, then binary H/M/S.
        frame = self.protocol.build_time_sync(datetime(2026, 11, 22, 14, 31, 50))
        self.assertEqual(frame, bytes.fromhex("5510000504400E 1F32B8".replace(" ", "")))

    def test_time_sync_weekday_flags(self) -> None:
        for weekday, flag in enumerate((0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40)):
            frame = self.protocol.build_time_sync(datetime(2026, 11, 23 + weekday, 8, 0, 0))
            self.assertEqual(frame[5], flag, weekday)

    def test_schedule_write_matches_trace(self) -> None:
        # The captured edit was on schedule 2, so the command byte is 0x02.
        slot = ScheduleSlot(
            start_hour=11, start_minute=0, end_hour=19, end_minute=0,
            work_seconds=5, pause_seconds=900,
        )
        self.assertEqual(
            self.protocol.build_schedule(slot, weekday_mask=0x52, mode=2),
            bytes.fromhex("551002050952 0B001300 0005 0384 1C".replace(" ", "")),
        )
        slot.start_hour = 9
        self.assertEqual(
            self.protocol.build_schedule(slot, weekday_mask=0x52, mode=2),
            bytes.fromhex("551002050952 09001300 0005 0384 1A".replace(" ", "")),
        )

    def test_slot_frames_match_the_app_layout(self) -> None:
        timer = MODULES.protocol_ble.ScentTechTimer(
            enabled=True, serial=2, weekdays=0x52 | 0x80,
            start_minute=9 * 60, stop_minute=19 * 60,
            run_seconds=5, pause_seconds=900, timer_id=2,
        )
        # Same wire frame the trace shows for Setting 2, bit 7 stripped.
        self.assertEqual(
            self.protocol.build_slot_frame(2, timer),
            bytes.fromhex("551002050952 09001300 0005 0384 1A".replace(" ", "")),
        )
        self.assertEqual(
            self.protocol.build_slot_enable(2, True),
            bytes.fromhex("551012050101" + "29"),
        )
        self.assertEqual(
            self.protocol.build_slot_frame(5, timer)[2],
            0x05,
        )

    def test_schedule_defaults_to_the_slot_in_use(self) -> None:
        slot = ScheduleSlot(
            start_hour=11, start_minute=0, end_hour=19, end_minute=0,
            work_seconds=5, pause_seconds=900,
        )
        self.assertEqual(self.protocol.build_schedule(slot, 0x52)[2], 0x01)
        # A status block with only schedule 3 enabled moves the writes there.
        payload = bytearray(50)
        payload[20:30] = bytes([0x01, 0x52, 0x0B, 0x00, 0x13, 0x00, 0x00, 0x05, 0x03, 0x84])
        self.protocol.parse_notification(notification(0xD1, bytes(payload)))
        self.assertEqual(self.protocol.build_schedule(slot, 0x52)[2], 0x03)
        self.assertEqual(self.protocol.build_enable(True)[2], 0x13)

    def test_power_register_is_framed_but_unused(self) -> None:
        # The unit ignores 0x06; the frame still has to be well formed.
        self.assertEqual(self.protocol.build_power(True), bytes.fromhex("551006050101" + "1D"))
        self.assertEqual(self.protocol.build_power(False), bytes.fromhex("551006050100" + "1C"))

    def test_checksum_is_sum_after_header(self) -> None:
        for frame in (
            self.protocol.build_query(),
            self.protocol.build_session_start(),
            self.protocol.build_time_sync(datetime(2026, 1, 5, 7, 8, 9)),
        ):
            self.assertEqual(frame[0], 0x55)
            self.assertEqual(frame[-1], sum(frame[1:-1]) & 0xFF)


class ScentMasterParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = ScentMaster()

    def test_status_block_fills_all_five_schedules(self) -> None:
        for cmd in (0xD1, 0xF2):  # -TJ- firmware answers D1 with F2
            state = self.protocol.parse_notification(notification(cmd, STATUS_PAYLOAD))
            self.assertEqual(sorted(state["timer_slots"]), [1, 2, 3, 4, 5])
            first = state["timer_slots"][1]
            self.assertEqual(first.weekdays, 0x52)
            self.assertEqual((first.start_minute, first.stop_minute), (11 * 60, 19 * 60))
            self.assertEqual((first.run_seconds, first.pause_seconds), (5, 900))
            self.assertEqual(first.enabled, True)
            self.assertEqual(state["timer_slots"][2].enabled, False)
            self.assertEqual(state["schedule_enabled"], True)
            self.assertEqual(state["phase"], "idle")
            self.assertEqual(state["weekday_mask"], 0x52)
            self.assertEqual((state["start_hour"], state["start_minute"]), (11, 0))
            self.assertEqual((state["end_hour"], state["end_minute"]), (19, 0))
            self.assertEqual(state["work_seconds"], 5)
            self.assertEqual(state["pause_seconds"], 900)

    def test_empty_enabled_slot_is_not_the_active_one(self) -> None:
        # Captured block: Setting 1 enabled with an all-zero record, the real
        # program in Setting 2. Edits must not land on the empty slot.
        payload = bytearray(50)
        payload[10:20] = bytes([0x01, 0x52, 0x09, 0x00, 0x13, 0x00, 0x00, 0x05, 0x03, 0x84])
        state = self.protocol.parse_notification(notification(0xF2, bytes(payload)))
        self.assertEqual(self.protocol.slot, 2)
        self.assertEqual(state["start_hour"], 9)
        self.assertEqual(state["weekday_mask"], 0x52)

    def test_disabled_slot(self) -> None:
        payload = bytes([0x00]) + STATUS_PAYLOAD[1:]
        state = self.protocol.parse_notification(notification(0xF2, payload))
        self.assertEqual(state["schedule_enabled"], False)

    def test_unanswered_registers_are_ignored(self) -> None:
        # The unit does not answer 0x06 (level) or 0xD2 (mode): nothing invented.
        self.assertEqual(self.protocol.parse_notification(notification(0x06, b"\x02")), {})
        self.assertEqual(self.protocol.parse_notification(notification(0x26, b"\x5A")), {})

    def test_name_and_note(self) -> None:
        self.assertEqual(
            self.protocol.parse_notification(notification(0xDA, bytes(16))), {},
        )
        note = b"Note".ljust(16, b"\x00")
        self.assertEqual(
            self.protocol.parse_notification(notification(0xDB, note)),
            {"device_label": "Note"},
        )

    def test_ignores_malformed_and_write_echoes(self) -> None:
        self.assertEqual(self.protocol.parse_notification(b""), {})
        self.assertEqual(self.protocol.parse_notification(b"\x55\x11\xD1\x05"), {})
        self.assertEqual(self.protocol.parse_notification(bytes.fromhex("5510D100E1")), {})
        # A short status payload must not raise or invent values.
        self.assertEqual(self.protocol.parse_notification(notification(0xD1, b"\x01")), {})


class ScentMasterDetectionTests(unittest.TestCase):
    def test_name_marker_wins_over_aromey_service(self) -> None:
        adv = SimpleNamespace(
            manufacturer_data={},
            service_uuids=["0000af30-0000-1000-8000-00805f9b34fb"],
            local_name="Lobby-TJ-0001",
        )
        self.assertEqual(
            detect_device_type("Lobby-TJ-0001", adv), DeviceType.SCENT_MASTER,
        )

    def test_oem_company_id_detects_the_unit(self) -> None:
        # Real advertisement: company ID 0xED9D, payload = MAC[2:6] + zeros.
        payload = bytes.fromhex("F1D5B9BD") + bytes(21)
        adv = SimpleNamespace(manufacturer_data={0xED9D: payload}, service_uuids=[], local_name="")
        self.assertEqual(detect_device_type("", adv), DeviceType.SCENT_MASTER)

    def test_aro_max_keeps_its_family(self) -> None:
        payload = b"DiffuserAroMax" + bytes(12)
        adv = SimpleNamespace(
            manufacturer_data={0x1234: payload},
            service_uuids=["0000af30-0000-1000-8000-00805f9b34fb"],
            local_name="Serial 0001",
        )
        self.assertEqual(
            detect_device_type("Serial 0001", adv), DeviceType.AROMELY_ARO_MAX,
        )


if __name__ == "__main__":
    unittest.main()


# The `D1` reply captured on hardware: Setting 1 ON with an all-zero record,
# Setting 2 ON 09:00-19:00 work 5 s / pause 900 s (Tue, Fri, Sun), rest empty.'
LIVE_BLOCK = bytes(
    [0x01, 0x1F] + [0x00] * 8
    + [0x01, 0x52, 0x09, 0x00, 0x13, 0x00, 0x00, 0x05, 0x03, 0x84]
    + [0x00] * 30
)


class ScentMasterSprayTests(unittest.TestCase):
    """Manual spray: pulse the enable register of a slot with no program."""

    def setUp(self) -> None:
        self.protocol = ScentMaster()

    def test_pulse_frames_match_the_trace(self) -> None:
        self.assertEqual(
            self.protocol.build_slot_enable(3, True), bytes.fromhex("5510130501012A")
        )
        self.assertEqual(
            self.protocol.build_slot_enable(3, False), bytes.fromhex("55101305010029")
        )

    def test_free_slot_skips_enabled_and_configured_schedules(self) -> None:
        device = MODULES.device.ScentDiffuserDevice(
            ble_address="11:22:33:44:55:66",
            ble_name="YGZK-XXJ-TJ-test",
            device_type=DeviceType.SCENT_MASTER,
        )
        device._on_ble_notification(0, bytearray(notification(0xF2, LIVE_BLOCK)))
        self.assertEqual(device._free_scent_master_slot(), 3)

    def test_no_free_slot_when_every_schedule_is_configured(self) -> None:
        block = b"".join(
            bytes([0x00, 0x52, 0x09, 0x00, 0x13, 0x00, 0x00, 0x05, 0x03, 0x84])
            for _ in range(5)
        )
        device = MODULES.device.ScentDiffuserDevice(
            ble_address="11:22:33:44:55:66",
            ble_name="YGZK-XXJ-TJ-test",
            device_type=DeviceType.SCENT_MASTER,
        )
        device._on_ble_notification(0, bytearray(notification(0xF2, block)))
        self.assertIsNone(device._free_scent_master_slot())
