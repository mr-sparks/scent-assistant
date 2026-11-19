"""Offline tests for the extra Scent Marketing GW controls.

Covers what was added on top of the DP-11 lamp: the DP 14 fixed gear
table parsing, grade (intensity) writes in DP 4, the program-enabled
flag (DP 4 task bit 7), and the entity registration for the GW family.
Frames are built from the decoded protocol layout and, where noted,
from the PID-13 state push captured in the wild.
"""
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.offline import MODULES, forbidden_io


const = MODULES.const
protocol_ble = MODULES.protocol_ble
Device = MODULES.device.ScentDiffuserDevice
DeviceType = const.DeviceType

# DP 14 fixed gear table as pushed by a PID-13 unit:
# count=5, then (work,pause) u16 BE: (5,240) (15,200) (20,160) (25,100) (25,60)
GRADE_TABLE_PAYLOAD = bytes.fromhex("05000500f0000f00c8001400a0001900640019003c")


def gw_device(address="02:00:00:00:00:01"):
    return Device(ble_address=address, ble_name="Synthetic GW diffuser",
                  device_type=DeviceType.SCENT_MARKETING_GW)


def feed_frame(proto, frame: bytes):
    """Deliver one inner frame through the chunked transport."""
    result = {}
    for chunk in proto.chunk_for_transport(frame):
        result = proto.parse_notification(chunk) or result
    return result


def dp4_task(frame: bytes) -> bytes:
    """Extract the first 7-byte task of a DP 4 frame (FF 01/02 00 04 AF ...)."""
    assert frame[0] == 0xFF and frame[2:4] == b"\x00\x04" and frame[4] == 0xAF
    length = (frame[5] << 8) | frame[6]
    payload = frame[7:7 + length]
    assert payload[0:2] == b"\x01\x00"
    count = (payload[2] << 8) | payload[3]
    assert count >= 1
    return payload[4:11]


class GradeTableParseTests(unittest.TestCase):
    def test_dp14_fixed_gear_table(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        frame = bytes([0xFF, 0x01]) + const.SM_GW_DP_FIXED_GEAR.to_bytes(2, "big") \
            + bytes([0xAF]) + len(GRADE_TABLE_PAYLOAD).to_bytes(2, "big") + GRADE_TABLE_PAYLOAD
        result = feed_frame(proto, frame)
        self.assertEqual(result["grade_table"], [
            (5, 240), (15, 200), (20, 160), (25, 100), (25, 60),
        ])


class BuildScheduleGradeTests(unittest.TestCase):
    def setUp(self):
        self.proto = protocol_ble.ScentMarketingGwProtocol()
        self.slot = protocol_ble.ScheduleSlot(
            start_hour=8, start_minute=0, end_hour=20, end_minute=0,
            enabled=True, work_seconds=10, pause_seconds=120,
        )

    def test_intensity_writes_grade_without_dp15(self):
        frame = self.proto.build_schedule(
            [self.slot], weekday_mask=0x7F, custom_mode=False, intensity=3,
        )
        self.assertEqual(frame[0], 0xFF)
        self.assertEqual(frame[1], 1)  # only DP 4 — no DP 15 block for a fixed grade
        task = dp4_task(frame)
        self.assertEqual(task[0], 0x80 | 0x7F)   # enabled + all days
        self.assertEqual(task[1:5], bytes([8, 0, 20, 0]))
        self.assertEqual(task[5], 0x01)          # spray
        self.assertEqual(task[6], 3)             # grade = intensity

    def test_custom_mode_writes_grade_zero_and_dp15(self):
        frame = self.proto.build_schedule([self.slot], weekday_mask=0x7F, custom_mode=True)
        self.assertEqual(frame[1], 2)            # DP 4 + DP 15
        task = dp4_task(frame)
        self.assertEqual(task[6], 0)             # grade 0 = custom

    def test_keep_reported_grade_when_no_intensity(self):
        # Simulate a read-back task with grade 4, then a plain window edit.
        self.proto._tasks = [bytes([0xFF, 8, 0, 20, 0, 0x01, 4])]
        frame = self.proto.build_schedule([self.slot], weekday_mask=0x7F, custom_mode=None)
        self.assertEqual(dp4_task(frame)[6], 4)

    def test_intensity_is_clamped(self):
        frame = self.proto.build_schedule(
            [self.slot], weekday_mask=0x7F, custom_mode=False, intensity=99,
        )
        self.assertEqual(dp4_task(frame)[6], 20)


class GwDeviceControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("connect", "connect_ex"):
            self.enterContext(patch.object(socket.socket, name, forbidden_io))
        self.enterContext(patch.object(socket, "getaddrinfo", forbidden_io))
        self.enterContext(patch.object(socket, "create_connection", forbidden_io))
        self.enterContext(patch("asyncio.sleep", new=AsyncMock()))
        self.device = gw_device()
        self.sent = []
        self.device._ble_connect = AsyncMock(return_value=True)
        self.device._ble_send = AsyncMock(side_effect=lambda f: self.sent.append(f) or True)
        self.device._teardown_ble_client = AsyncMock()

    async def test_set_intensity_writes_grade(self):
        self.assertTrue(await self.device.set_intensity(3))
        self.assertEqual(self.device.state.intensity, 3)
        self.assertEqual(len(self.sent), 1)
        task = dp4_task(self.sent[0])
        self.assertEqual(task[6], 3)

    async def test_set_schedule_enabled_sets_task_flag(self):
        self.assertTrue(await self.device.set_schedule_enabled(True))
        self.assertIs(self.device.state.schedule_enabled, True)
        self.assertEqual(dp4_task(self.sent[0])[0] & 0x80, 0x80)
        self.assertTrue(await self.device.set_schedule_enabled(False))
        self.assertIs(self.device.state.schedule_enabled, False)
        self.assertEqual(dp4_task(self.sent[1])[0] & 0x80, 0x00)


class GwEntitySetupTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, module, device):
        hass = SimpleNamespace(data={const.DOMAIN: {"entry-1": device}})
        entry = SimpleNamespace(entry_id="entry-1", data={})
        added = []
        er_stub = MODULES.switch.add_when_present.__globals__["er"]
        registry = SimpleNamespace()
        with patch.object(er_stub, "async_get", return_value=registry, create=True), \
             patch.object(er_stub, "async_entries_for_config_entry",
                          return_value=[], create=True):
            await module.async_setup_entry(hass, entry, added.extend)
        return added

    async def test_gw_gets_program_switch(self):
        device = gw_device("02:00:00:00:00:02")
        added = await self._collect(MODULES.switch, device)
        by_name = {e._attr_name: e for e in added}
        self.assertIn("Program", by_name)
        self.assertTrue(by_name["Program"].available)

    async def test_gw_gets_intensity_number(self):
        device = gw_device("02:00:00:00:00:03")
        added = await self._collect(MODULES.number, device)
        by_name = {e._attr_name: e for e in added}
        self.assertIn("Intensity", by_name)
        self.assertEqual(by_name["Intensity"]._attr_native_min_value, 1)
        self.assertEqual(by_name["Intensity"]._attr_native_max_value, 20)
        # Work/Pause durations were already generic — still there.
        self.assertIn("Work Duration", by_name)
        self.assertIn("Pause Duration", by_name)

    async def test_ak_program_switch_still_v3_gated(self):
        device = Device(ble_address="02:00:00:00:00:04", ble_name="AK",
                        device_type=DeviceType.SCENT_MARKETING_AK)
        added = await self._collect(MODULES.switch, device)
        program = next(e for e in added if e._attr_name == "Program")
        # No login parsed yet -> is_v3 False -> unavailable on AK.
        self.assertFalse(program.available)


if __name__ == "__main__":
    unittest.main()
