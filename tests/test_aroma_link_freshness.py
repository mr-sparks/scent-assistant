"""Aroma-Link freshness using real notification parsing, without BLE I/O."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.offline import MODULES, forbidden_io


class AromaLinkFreshnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 10_000.0
        self.clock = SimpleNamespace(monotonic=lambda: self.now)
        self.enterContext(patch.object(MODULES.device, "time", self.clock))
        self.device = MODULES.device.ScentDiffuserDevice(
            ble_address="02:00:00:00:00:01", ble_name="Synthetic Aroma-Link",
            device_type=MODULES.const.DeviceType.AROMA_LINK,
        )
        self.device._ble_send = forbidden_io
        self.device._ble_connect = forbidden_io
        self.status = MODULES.sensor.DiffuserStatusSensor(self.device, None)
        self.work = MODULES.sensor._FreshDiffuserWorkRemainSensor(self.device, None)
        self.pause = MODULES.sensor._FreshDiffuserPauseRemainSensor(self.device, None)
        for sensor in (self.status, self.work, self.pause):
            sensor.hass = object()
            sensor.async_write_ha_state = Mock()

    async def asyncTearDown(self):
        await self.device.async_shutdown()

    def send(self, payload, fragment=False):
        wire = self.device._protocol._build_packet(bytes(payload))
        if fragment:
            self.device._on_ble_notification(0, bytearray(wire[:8]))
            self.device._on_ble_notification(0, bytearray(wire[8:]))
        else:
            self.device._on_ble_notification(0, bytearray(wire))

    def status_frame(self, *, power=1, phase=1, work=5, pause=8, short=False,
                     fragment=False):
        payload = [0x52, 0x0A, 0x07, 0xEA, 1, 1, 8, 5, 0, 1,
                   0, power, phase, 0, work, 0, pause]
        self.send(payload[:13] if short else payload, fragment=fragment)

    async def test_default_exact_boundaries_and_irrelevant_oil(self):
        self.assertFalse(self.status.available)
        self.status_frame(fragment=True)
        self.assertEqual(self.status.native_value, "spraying")
        self.assertEqual(self.work.native_value, 5)
        self.assertIsNone(self.pause.native_value)
        self.now += 9.999
        self.send((0x52, 0x1E, 44))
        self.assertEqual(self.work.native_value, 5)
        self.now = 10010.0
        self.assertIsNone(self.work.native_value)
        self.assertTrue(self.status.available)
        self.now = 10660.0
        self.assertFalse(self.status.available)
        self.assertEqual(self.device._state.work_remaining, 5)

    async def test_short_phase_packet_never_renews_old_seconds(self):
        self.status_frame()
        self.now += 8
        self.status_frame(short=True)
        self.assertEqual(self.status.native_value, "spraying")
        self.assertIsNone(self.work.native_value)
        self.assertEqual(self.device.state.work_remaining, 5)

    async def test_alternate_phase_exact_zero_and_off(self):
        self.status_frame(phase=2, pause=0)
        self.assertEqual(self.pause.native_value, 0)
        self.assertTrue(self.pause.available)
        self.assertIsNone(self.work.native_value)
        self.status_frame(power=0, phase=1)
        self.assertEqual(self.status.native_value, "off")
        self.assertIsNone(self.pause.native_value)
        self.assertIsNone(self.work.native_value)

    async def test_work_push_requires_recent_reported_power(self):
        def push():
            self.send((0x53, 0x09, 1, 0, 7, 0, 2, 7, 0, 21, 0))
        self.device.state.power = True  # a command guess is not a report
        push()
        self.assertIsNone(self.status.native_value)
        self.status_frame(phase=0)
        self.now += 1
        push()
        self.assertEqual(self.work.native_value, 7)
        self.now += 660
        push()
        self.assertIsNone(self.status.native_value)

    async def test_optimistic_off_or_other_phase_is_not_genuine_report(self):
        self.status_frame()
        self.device.state.power = False
        self.device.state.phase = "off"
        self.assertIsNone(self.status.native_value)
        self.assertIsNone(self.work.native_value)
        self.status_frame(power=0, phase=0)
        self.assertEqual(self.status.native_value, "off")

    async def test_two_optimistic_toggles_cannot_resurrect_a_snapshot(self):
        self.status_frame()
        self.device.state.power = False
        self.device._notify_state_changed()
        self.device.state.power = True
        self.device._notify_state_changed()
        self.assertIsNone(self.work.native_value)
        self.assertIsNone(self.status.native_value)
        self.status_frame(work=9)
        self.assertEqual(self.work.native_value, 9)

    async def test_truncated_and_bad_checksum_never_create_receipt(self):
        payload = bytes((0x52, 0x0A, 0x07, 0xEA, 1, 1, 8, 5, 0, 1,
                         0, 1, 1, 0, 5, 0, 8))
        wire = self.device._protocol._build_packet(payload)
        self.device._on_ble_notification(0, bytearray(wire[:-5]))
        self.assertIsNone(self.status.native_value)
        bad = wire[:3] + bytes((wire[3] ^ 1,)) + wire[4:]
        self.device._on_ble_notification(0, bytearray(bad))
        self.assertIsNone(self.status.native_value)

    async def test_timer_republishes_and_shutdown_clears_pending_handle(self):
        self.status_frame()
        old = self.device._aroma_expiry
        self.assertIsNotNone(old)
        for sensor in (self.status, self.work, self.pause):
            sensor.async_write_ha_state.reset_mock()
        self.now += 10
        old.cancel()
        self.device._expire_aroma_report()
        self.work.async_write_ha_state.assert_called_once()
        self.assertIsNone(self.work.native_value)
        self.assertTrue(self.status.available)
        handle = self.device._aroma_expiry
        self.assertIsNotNone(handle)
        await self.device.async_shutdown()
        self.assertTrue(handle.cancelled())
        self.assertIsNone(self.device._aroma_expiry)
        self.status_frame()
        self.assertIsNone(self.device._aroma_expiry)

    async def test_hourly_options_keep_status_and_not_countdown(self):
        self.device.refresh_interval = 3600
        self.status_frame()
        self.now += 3600
        self.assertTrue(self.status.available)
        self.assertFalse(self.work.available)
        self.now += 3659
        self.assertTrue(self.status.available)
        self.now += 1
        self.assertFalse(self.status.available)

    async def test_live_option_change_shortens_timer_without_reviving_old_data(self):
        self.device.refresh_interval = 3600
        self.status_frame()
        old = self.device._aroma_expiry
        self.now += 700
        self.device.refresh_interval = 300
        self.assertTrue(old.cancelled())
        self.assertFalse(self.status.available)
        self.device.refresh_interval = 3600
        self.assertFalse(self.status.available)
        self.status_frame()
        self.assertTrue(self.status.available)
        self.assertEqual(self.device.refresh_interval, 3600)

    async def test_reload_does_not_inherit_old_snapshot(self):
        self.device.refresh_interval = 1800
        self.status_frame()
        handle = self.device._aroma_expiry
        await self.device.async_shutdown()
        self.assertTrue(handle.cancelled())
        new = MODULES.device.ScentDiffuserDevice(
            ble_address="02:00:00:00:00:01",
            device_type=MODULES.const.DeviceType.AROMA_LINK,
        )
        new.refresh_interval = 1800
        self.assertEqual(new._aroma_report.phase_max_age, 3660)
        self.assertIsNone(new.aroma_reported_phase)
        self.assertIsNone(new._aroma_expiry)
        await new.async_shutdown()

    async def test_real_event_loop_timer_expires_without_reads(self):
        with patch.object(MODULES.aroma_report, "COUNTDOWN_MAX_AGE", 0.01):
            self.status_frame()
            self.assertTrue(self.work.available)
            self.work.async_write_ha_state.reset_mock()
            self.now += 0.02
            await asyncio.sleep(0.04)
            self.work.async_write_ha_state.assert_called_once()
            self.assertFalse(self.work.available)

    async def test_other_families_and_ble_less_aroma_keep_legacy_path(self):
        for kind in (MODULES.const.DeviceType.AROMA_LINK, MODULES.const.DeviceType.SCENT_TECH):
            dev = MODULES.device.ScentDiffuserDevice(device_type=kind)
            dev.state.phase = "spraying"
            self.assertFalse(dev.uses_aroma_report_expiry)
            self.assertEqual(MODULES.sensor.DiffuserStatusSensor(dev, None).native_value, "spraying")
