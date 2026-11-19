"""Offline tests for the extra Scent Marketing GW controls.

Covers what was added on top of the DP-11 lamp: the DP 14 fixed gear
table parsing, grade (intensity) writes in DP 4, the program-enabled
flag (DP 4 task bit 7), and the entity registration for the GW family.
Frames are built from the decoded protocol layout and, where noted,
from the PID-13 state push captured in the wild.
"""
import json
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


# The full state push of a real PID-13 unit (from its diagnostics dump;
# addresses are redacted upstream of these frames). Sent after the DP 10
# time-sync; the final 3b chunk is the lamp-on echo.
PID13_PUSH = [
    "3a01000b000101010005bf001230302e31332e30",
    "3a0230203031332e30302e30350006bf00134c69",
    "3a0376696e6720526f6f6d000000000000000000",
    "3a0407af000501000000000008af000d71007800",
    "3a0578007806a400000000000baf00020100000c",
    "3a06af00020064000eaf001505000500f0000f00",
    "3a07c8001400a0001900640019003c0014af0010",
    "3a08000000000000000000000000000000000017",
    "3a09af001601010002c10900173b02be08001600",
    "3a0a0201008001012c0018af0016010005002300",
    "3a0b3c012c020005003c0005003c0005003c",
    "3b010001000baf00020101",
]


class Pid13StatePushTests(unittest.TestCase):
    """Regression: parse the exact state push a PID-13 unit emits."""

    def test_full_push_parses(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        result = {}
        for chunk_hex in PID13_PUSH:
            result.update(proto.parse_notification(bytes.fromhex(chunk_hex)))
        self.assertIs(result["power"], True)
        self.assertEqual(result["firmware_version"], "00.13.00 013.00.05")
        self.assertEqual(result["device_name"], "Living Room")
        self.assertEqual(result["battery"], 100)
        # 71 0078 0078 … = 120 g of 120 g -> full -> 100 % (app says "vol")
        self.assertEqual(result["oil_remaining"], 100)
        self.assertEqual(result["grade_table"], [
            (5, 240), (15, 200), (20, 160), (25, 100), (25, 60),
        ])
        self.assertIs(result["light_on"], True)  # last frame = lamp-on echo

    def test_oil_weight_ratio(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        # 60 g of 120 g -> 50 %
        payload = bytes.fromhex("7100780 03c0078 06a4 0000 0000".replace(" ", ""))
        frame = bytes([0xFF, 0x01]) + (8).to_bytes(2, "big") + bytes([0xAF]) \
            + len(payload).to_bytes(2, "big") + payload
        result = feed_frame(proto, frame)
        self.assertEqual(result["oil_remaining"], 50)


class NozzleBlockLevelTests(unittest.TestCase):
    """DP 23 (multi-nozzle) byte 9 carries the active level on PID-13."""

    DP23_LOW = bytes.fromhex("01010002c10900173b01be080016000201008001012c")  # byte9=01, app set Low
    DP23_MED = bytes.fromhex("01010002c10900173b02be080016000201008001012c")  # byte9=02

    def _feed_dp23(self, payload: bytes):
        proto = protocol_ble.ScentMarketingGwProtocol()
        frame = bytes([0xFF, 0x01]) + (23).to_bytes(2, "big") + bytes([0xAF]) \
            + len(payload).to_bytes(2, "big") + payload
        return proto, feed_frame(proto, frame)

    def test_dp23_byte9_is_intensity(self):
        _, result = self._feed_dp23(self.DP23_LOW)
        self.assertEqual(result["intensity"], 1)
        self.assertIs(result["schedule_custom_mode"], False)
        _, result = self._feed_dp23(self.DP23_MED)
        self.assertEqual(result["intensity"], 2)

    def test_dp23_byte9_zero_is_custom(self):
        block = bytearray(self.DP23_MED)
        block[9] = 0
        _, result = self._feed_dp23(bytes(block))
        self.assertIs(result["schedule_custom_mode"], True)
        self.assertNotIn("intensity", result)

    def test_build_nozzle_level_sends_both_app_frames(self):
        # The app sends two DP-23 frames per change and the unit beeps once
        # per frame (confirmed: the official app also beeps twice) — we
        # replicate the full sequence: transition frame, then commit frame.
        proto, _ = self._feed_dp23(self.DP23_LOW)  # byte9 = 01 (prev level)
        frames = proto.build_nozzle_level_frames(5)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].hex(),
                         "ff010017af00" "1601010002c10900173b05be0800160001" "01008001012c")
        self.assertEqual(frames[1].hex(),
                         "ff010017af00" "1601010002be0800160005c10900173b05" "01008001012c")
        self.assertEqual(proto.build_nozzle_level(5), frames[0])

    # Real frames captured from the official Scent Marketing app
    # (Android btsnoop_hci.log, PID-13, 2026-11-19 20:13): setting the
    # level to Low (1) while the device was on 5, then to Max (5).
    DP23_PUSH_MAX = bytes.fromhex("01010002be0800160005c10900173b0501008003012c")
    DP23_ECHO_LOW = bytes.fromhex("01010002be0800160001c10900173b0101008003012c")
    APP_SET_LOW = [
        "ff010017af001601010002c10900173b01be080016000501008003012c",
        "ff010017af001601010002be0800160001c10900173b0101008003012c",
    ]
    APP_SET_MAX = [
        "ff010017af001601010002c10900173b05be080016000101008003012c",
        "ff010017af001601010002be0800160005c10900173b0501008003012c",
    ]

    def test_build_nozzle_level_matches_app_snoop_low(self):
        proto, _ = self._feed_dp23(self.DP23_PUSH_MAX)  # current level 5
        frames = proto.build_nozzle_level_frames(1)
        self.assertEqual([f.hex() for f in frames], self.APP_SET_LOW)

    def test_build_nozzle_level_matches_app_snoop_max(self):
        proto, _ = self._feed_dp23(self.DP23_ECHO_LOW)  # current level 1
        frames = proto.build_nozzle_level_frames(5)
        self.assertEqual([f.hex() for f in frames], self.APP_SET_MAX)

    def test_nozzle_frames_chunk_like_the_app(self):
        # The app writes each 29-byte frame as [nonce,1]+18B then
        # [nonce,2]+11B transport chunks.
        proto, _ = self._feed_dp23(self.DP23_PUSH_MAX)
        frame = proto.build_nozzle_level_frames(1)[0]
        chunks = protocol_ble.ScentMarketingGwProtocol.chunk_for_transport(frame, nonce=0x42)
        self.assertEqual([len(c) for c in chunks], [20, 13])
        self.assertEqual(chunks[0][:2], b"\x42\x01")
        self.assertEqual(chunks[1][:2], b"\x42\x02")
        self.assertEqual(b"".join(c[2:] for c in chunks), frame)

    def test_build_nozzle_level_none_without_block(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        self.assertIsNone(proto.build_nozzle_level(3))

    def test_xor_build_nozzle_level_encrypted(self):
        proto = protocol_ble.ScentMarketingGwXorProtocol(mac="A1B2C3D4E5F6")
        proto._nozzle_block = self.DP23_LOW
        frame = proto.build_nozzle_level(4)
        obj = json.loads(proto.decrypt(frame).decode("utf-8"))
        dp = obj["data"]["23"]
        self.assertEqual(dp["type"], const.SM_GW_TYPE_BINARY)
        self.assertEqual(bytes.fromhex(dp["value"])[9], 4)


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

    async def test_set_intensity_clamped_to_gear_table(self):
        # PID-13 reports a 5-row DP 14 gear table (Low..Max).
        self.device.state.grade_table = [(5, 240), (15, 200), (20, 160), (25, 100), (25, 60)]
        self.assertTrue(await self.device.set_intensity(9))
        self.assertEqual(self.device.state.intensity, 5)
        self.assertEqual(dp4_task(self.sent[0])[6], 5)

    async def test_set_schedule_mode_custom_writes_grade_zero(self):
        self.assertTrue(await self.device.set_schedule_mode(True))
        self.assertIs(self.device.state.schedule_custom_mode, True)
        task = dp4_task(self.sent[0])
        self.assertEqual(task[6], 0)  # custom grade

    async def test_intensity_clears_stale_custom_mode(self):
        # Device reported custom mode earlier (grade 0 read-back): picking
        # a level must flip the mode flag, or the Level select snaps back.
        self.device.state.schedule_custom_mode = True
        self.assertTrue(await self.device.set_intensity(5))
        self.assertIs(self.device.state.schedule_custom_mode, False)
        self.assertEqual(self.device.state.intensity, 5)
        self.assertEqual(dp4_task(self.sent[0])[6], 5)

    async def test_work_duration_sets_custom_mode(self):
        self.device.state.schedule_custom_mode = False
        self.device.state.intensity = 4
        self.assertTrue(await self.device.set_work_duration(30))
        self.assertIs(self.device.state.schedule_custom_mode, True)
        self.assertEqual(dp4_task(self.sent[0])[6], 0)

    async def test_set_intensity_also_writes_dp23_nozzle_level(self):
        # Simulate the DP 23 block the device pushed (byte 9 = current level).
        proto = self.device._protocol
        proto._nozzle_block = bytes.fromhex(
            "01010002c10900173b02be080016000201008001012c"
        )
        self.assertTrue(await self.device.set_intensity(5))
        self.assertEqual(self.device.state.intensity, 5)
        # DP 23 unit: the app's two-frame DP 23 sequence is sent — no DP 4
        # schedule write alongside (snoop-verified app behaviour).
        self.assertEqual(len(self.sent), 2)
        for dp23_frame in self.sent:
            self.assertEqual(dp23_frame[2:4], b"\x00\x17")
            self.assertEqual(dp23_frame[7 + 9], 5)
        # Frame 1: rec_c1=new(05), rec_be=prev(02); frame 2: both new.
        self.assertEqual(self.sent[0][7:7 + 22].hex(),
                         "01010002c10900173b05be0800160002" "01008001012c")
        self.assertEqual(self.sent[1][7:7 + 22].hex(),
                         "01010002be0800160005c10900173b05" "01008001012c")

    async def test_set_schedule_mode_multi_nozzle_skips_dp4(self):
        proto = self.device._protocol
        proto._nozzle_block = bytes.fromhex(
            "01010002c10900173b02be080016000201008001012c"
        )
        self.device.state.intensity = 2
        self.assertTrue(await self.device.set_schedule_mode(False))
        self.assertEqual(len(self.sent), 2)  # two DP 23 frames, no DP 4
        self.assertTrue(all(f[2:4] == b"\x00\x17" for f in self.sent))
        self.assertTrue(await self.device.set_schedule_mode(True))
        # Custom mode: DP 23 byte 9 = 0 in both frames.
        self.assertEqual(len(self.sent), 4)
        for f in self.sent[2:4]:
            self.assertEqual(f[2:4], b"\x00\x17")
            self.assertEqual(f[7 + 9], 0)

    async def test_set_intensity_without_nozzle_block_sends_dp4_only(self):
        self.assertTrue(await self.device.set_intensity(3))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(dp4_task(self.sent[0])[6], 3)

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

    async def test_gw_gets_level_select_not_intensity_number(self):
        device = gw_device("02:00:00:00:00:03")
        added = await self._collect(MODULES.select, device)
        by_name = {e._attr_name: e for e in added}
        self.assertIn("Level", by_name)
        level = by_name["Level"]
        self.assertEqual(level._attr_options,
                         ["Custom", "Low", "Mild", "Medium", "High", "Max"])
        # No raw intensity number for GW anymore.
        numbers = await self._collect(MODULES.number, device)
        self.assertNotIn("Intensity", {e._attr_name for e in numbers})
        # Work/Pause durations were already generic — still there.
        self.assertIn("Work Duration", {e._attr_name for e in numbers})
        self.assertIn("Pause Duration", {e._attr_name for e in numbers})

    async def test_level_select_maps_names_to_grades(self):
        device = gw_device("02:00:00:00:00:05")
        added = await self._collect(MODULES.select, device)
        level = next(e for e in added if e._attr_name == "Level")
        device.set_intensity = AsyncMock(return_value=True)
        device.set_schedule_mode = AsyncMock(return_value=True)
        await level.async_select_option("High")
        device.set_intensity.assert_awaited_with(4)
        await level.async_select_option("Custom")
        device.set_schedule_mode.assert_awaited_with(True)
        # current_option follows the reported state
        device.state.intensity = 2
        device.state.schedule_custom_mode = False
        self.assertEqual(level.current_option, "Mild")
        device.state.schedule_custom_mode = True
        self.assertEqual(level.current_option, "Custom")

    async def test_ak_program_switch_still_v3_gated(self):
        device = Device(ble_address="02:00:00:00:00:04", ble_name="AK",
                        device_type=DeviceType.SCENT_MARKETING_AK)
        added = await self._collect(MODULES.switch, device)
        program = next(e for e in added if e._attr_name == "Program")
        # No login parsed yet -> is_v3 False -> unavailable on AK.
        self.assertFalse(program.available)


if __name__ == "__main__":
    unittest.main()
