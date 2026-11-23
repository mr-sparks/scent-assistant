"""Offline timed-stop readback tests through the real Aroma-Link BLE path."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.offline import MODULES

D = MODULES.device
C = MODULES.const
LOG = D._LOGGER.name


class _FastSettle:
    """Keep real scheduling and shorten only the existing one-second wait."""

    @staticmethod
    async def sleep(delay):
        await asyncio.sleep(0.005 if delay == 1.0 else delay)

    def __getattr__(self, name):
        return getattr(asyncio, name)


class _Link:
    def __init__(self):
        self.is_connected = True
        self.callback = None
        self.writes = []
        self.on_write = None
        self.on_subscribe = None
        self.services = SimpleNamespace(get_characteristic=lambda _: SimpleNamespace(
            properties=["write"],
        ))

    async def start_notify(self, _characteristic, callback):
        self.callback = callback
        if self.on_subscribe:
            self.on_subscribe(self)

    async def stop_notify(self, _characteristic):
        pass

    async def disconnect(self):
        self.is_connected = False

    async def write_gatt_char(self, _characteristic, frame, **_):
        parts = frame[4:-3]
        self.writes.append(parts)
        if self.on_write:
            self.on_write(self, parts)

    def receive(self, frame):
        self.callback(0, bytearray(frame))


class TimedAromaLinkStopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device = D.ScentDiffuserDevice(
            ble_address="02:00:00:00:00:01", ble_name="Synthetic Aroma-Link",
            device_type=C.DeviceType.AROMA_LINK,
        )
        self.device._ble_has_synced_time = True
        self.device.state.power = True
        self.device.state.phase = "spraying"
        self.link = _Link()
        self.connect = AsyncMock(side_effect=lambda *_args, **_kwargs: self.link)
        self.enterContext(patch.object(D, "establish_connection", self.connect))
        self.enterContext(patch.object(D, "MOMENTARY_STOP_CONFIRM_SECONDS", 0.06))
        self.enterContext(patch.object(D, "asyncio", _FastSettle()))
        self._tasks = []

    async def asyncTearDown(self):
        await self.device.async_shutdown()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def frame(self, *payload):
        return self.device._protocol._build_packet(bytes(payload))

    def power(self, binary):
        return self.frame(0x53, 0x08, binary)

    def start(self):
        task = asyncio.create_task(self.device._momentary_off_later(0))
        self._tasks.append(task)
        self.device._momentary_task = task
        return task

    def assert_only_original_stop(self):
        self.assertEqual(self.link.writes, [bytes((0x57, 0x08, 0))])
        self.assertIsNone(self.device._momentary_stop_readback)

    async def test_direct_zero_off_after_command_confirms(self):
        self.link.on_write = lambda link, _: link.receive(self.power(0))
        with self.assertLogs(LOG, "DEBUG") as seen:
            await self.start()
        self.assertTrue(any("device reported power off" in x for x in seen.output))
        self.assertFalse(self.device.state.power)
        self.assert_only_original_stop()

    async def test_fragmented_all_work_report_confirms_without_extra_queries(self):
        frame = self.frame(0x53, 0x0A, 0x07, 0xEA, 1, 1, 8, 5, 0, 1,
                           0, 0, 0, 0, 0, 0, 0)
        def finish(link, _):
            link.receive(frame[:10])
            asyncio.get_running_loop().call_later(0.02, link.receive, frame[10:])
        self.link.on_write = finish
        with self.assertLogs(LOG, "DEBUG") as seen:
            await self.start()
        self.assertTrue(any("device reported power off" in x for x in seen.output))
        self.assert_only_original_stop()

    async def test_ack_and_cached_off_but_no_new_report_are_unconfirmed(self):
        self.device.state.power = False
        self.link.on_write = lambda link, _: link.receive(self.frame(0x57, 0x08))
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assert_only_original_stop()

    async def test_missing_report_does_not_publish_optimistic_off(self):
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assertTrue(self.device.state.power)
        self.assertEqual(self.device.state.phase, "spraying")
        self.assert_only_original_stop()

    async def test_off_during_initial_subscription_cannot_confirm_later_stop(self):
        self.link.on_subscribe = lambda link: link.receive(self.power(0))
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assert_only_original_stop()

    async def test_noncanonical_zero_like_bare_unrelated_and_bad_checksum(self):
        bad = self.power(0)
        def give_noise(link, _):
            link.receive(self.power(2))
            link.receive(bytes((0x53, 0x08, 0)))
            link.receive(self.frame(0x53, 0x03, 0))
            link.receive(self.frame(0x52, 0x0A, 0x07, 0xEA, 1, 1, 8, 5, 0, 1,
                                    0, 0, 0, 0, 0, 0, 0))
            link.receive(bad[:3] + bytes((bad[3] ^ 1,)) + bad[4:])
        self.link.on_write = give_noise
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assert_only_original_stop()

    async def test_unknown_power_does_not_publish_off_or_drop_other_fields(self):
        def invalid(link, _):
            link.receive(self.power(2))
            link.receive(self.frame(0x53, 0x0A, 0x07, 0xEA, 1, 1, 8, 5, 0, 1,
                                    0, 2, 0, 0, 17, 0, 21))
        self.link.on_write = invalid
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assertTrue(self.device.state.power)
        self.assertEqual(self.device.state.phase, "spraying")
        self.assertEqual(self.device.state.work_remaining, 17)
        self.assert_only_original_stop()

    async def test_part_of_old_off_frame_cannot_complete_as_new_evidence(self):
        frame = self.power(0)
        self.link.on_subscribe = lambda link: link.receive(frame[:5])
        self.link.on_write = lambda link, _: link.receive(frame[5:])
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assert_only_original_stop()

    async def test_latest_on_contradicts_earlier_off(self):
        def contradict(link, _):
            link.receive(self.power(0))
            link.receive(self.power(1))
        self.link.on_write = contradict
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assertTrue(self.device.state.power)
        self.assert_only_original_stop()

    async def test_replaced_link_and_old_callback_cannot_confirm(self):
        retired = self.link
        self.assertTrue(await self.device._ble_connect())
        self.link = _Link()
        await self.device._teardown_ble_client(reason="test")
        self.link.on_write = lambda _link, _: retired.receive(self.power(0))
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        self.assertTrue(self.device.state.power)
        self.assert_only_original_stop()

    async def test_negative_ack_not_relabelled_confirmed(self):
        def nack(link, _):
            link.receive(self.power(0))
            link.receive(self.frame(0x57, 0x08, *b"NACK"))
        self.link.on_write = nack
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("auto power-off failed", seen.output[-1])
        self.assert_only_original_stop()

    async def test_unload_cancels_wait_and_releases_scope(self):
        wrote = asyncio.Event()
        self.link.on_write = lambda _link, _: wrote.set()
        task = self.start()
        await wrote.wait()
        await self.device.async_shutdown()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertFalse(self.link.is_connected)
        self.assert_only_original_stop()
        self.link.receive(self.power(0))  # late backend callback after unload
        self.assertTrue(self.device.state.power)

    async def test_restart_supersedes_old_wait_and_preserves_duration_zero(self):
        wrote = asyncio.Event()
        self.link.on_write = lambda _link, parts: (
            wrote.set() if parts == bytes((0x57, 0x08, 0)) else None
        )
        first = self.start()
        await wrote.wait()
        self.device.momentary_seconds = 0
        with self.assertNoLogs(LOG, "WARNING"):
            self.assertTrue(await self.device.momentary_diffuse())
            await asyncio.gather(first, return_exceptions=True)
        self.assertTrue(first.cancelled())
        self.assertEqual(self.link.writes, [bytes((0x57, 0x08, 0)), bytes((0x57, 0x08, 1))])
        self.assertIsNone(self.device._momentary_task)
        self.assertIsNone(self.device._momentary_stop_readback)

    async def test_manual_power_keeps_existing_optimistic_result(self):
        with self.assertNoLogs(LOG, "WARNING"):
            self.assertTrue(await self.device.set_power(False))
        self.assertFalse(self.device.state.power)
        self.assert_only_original_stop()

    async def test_prior_manual_write_in_flight_cannot_confirm_timed_stop(self):
        manual_wrote = asyncio.Event()
        manual_release = asyncio.Event()
        original = self.link.write_gatt_char
        async def send(characteristic, frame, **kwargs):
            if frame[4:-3] == bytes((0x57, 0x08, 1)):
                await original(characteristic, frame, **kwargs)
                manual_wrote.set()
                await manual_release.wait()
                return
            await original(characteristic, frame, **kwargs)
            self.link.receive(self.power(0))
        self.link.write_gatt_char = send
        manual = asyncio.create_task(self.device.set_power(True))
        self._tasks.append(manual)
        await manual_wrote.wait()
        with self.assertLogs(LOG, "WARNING") as seen:
            await self.start()
        self.assertIn("was not confirmed", seen.output[-1])
        manual_release.set()
        self.assertTrue(await manual)
        self.assertEqual(self.link.writes, [bytes((0x57, 0x08, 1)), bytes((0x57, 0x08, 0))])
        self.assertEqual(self.device._aroma_power_in_flight, 0)

    async def test_cloud_timer_keeps_legacy_path(self):
        self.device._ble_address = None
        self.device.set_power = AsyncMock(return_value=True)
        await self.start()
        self.device.set_power.assert_awaited_once_with(False)
        self.assertEqual(self.link.writes, [])

    async def test_late_connect_after_shutdown_does_not_write_off(self):
        connecting = asyncio.Event()

        async def delayed(*_args, **_kwargs):
            connecting.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:  # simulate a late cooperative return
                return self.link

        self.connect.side_effect = delayed
        task = self.start()
        await connecting.wait()
        await self.device.async_shutdown()
        await task
        self.assertEqual(self.link.writes, [])
        self.assertIsNone(self.device._momentary_stop_readback)
