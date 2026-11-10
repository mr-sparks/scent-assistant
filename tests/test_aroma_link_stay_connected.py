"""Offline Aroma-Link reader, recorder-facing sensors and lifecycle tests.

All notifications are synthetic framed bytes through the production parser. The
inert peer is injected at GATT, not by copying/replacing device implementations.
"""
import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.offline import BleakError, MODULES, forbidden_io


Device = MODULES.device.ScentDiffuserDevice
Types = MODULES.const.DeviceType
Protocol = MODULES.protocol_ble.AromaLinkBleProtocol
REAL_ASYNCIO = asyncio


async def eventually(predicate):
    async def wait():
        while not predicate():
            await REAL_ASYNCIO.sleep(0)
    await REAL_ASYNCIO.wait_for(wait(), 1.0)


def status_payload(second=0, work=30, pause=90):
    """0A carries clock/countdowns/capabilities, NOT oil measurements."""
    payload = bytearray(49)
    payload[:10] = bytes([0x52, 0x0A, 0x07, 0xEA, 11, 1, 12, 0, second, 7])
    payload[11:17] = bytes([1, 1]) + work.to_bytes(2, "big") + pause.to_bytes(2, "big")
    payload[17:22] = bytes([8, 0, 20, 0, 0])
    payload[41:43] = bytes([0, 1])
    return bytes(payload)


def week_payload():
    slot = bytes([8, 0, 20, 0, 0x11, 0, 30, 0, 90])
    return bytes([0x52, 0x15]) + slot * 35


class ManualSleeps:
    """Only reader sleeps are stepped; refresh settle sleeps yield normally.

    Does not patch asyncio globally, alter event timeouts, or synthesize parser
    completion. Requested production delays remain available for assertions.
    """

    def __init__(self):
        self.waiters = deque()
        self.delays = []
        self.idle_waiters = deque()
        self.idle_delays = []

    def __getattr__(self, name):
        return getattr(REAL_ASYNCIO, name)

    async def sleep(self, delay):
        task = asyncio.current_task()
        assert task is not None
        coro = task.get_coro()
        assert coro is not None
        if coro.__name__ == "_status_reads":
            waiter = asyncio.get_running_loop().create_future()
            self.waiters.append(waiter)
            self.delays.append(delay)
            await waiter
        elif coro.__name__ == "_delayed_disconnect":
            waiter = asyncio.get_running_loop().create_future()
            self.idle_waiters.append(waiter)
            self.idle_delays.append(delay)
            await waiter
        else:
            await REAL_ASYNCIO.sleep(0)

    async def tick(self):
        await eventually(lambda: any(not item.done() for item in self.waiters))
        while self.waiters:
            waiter = self.waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return

    async def cycle(self, device):
        before = len(self.delays)
        task = device._status_task
        await self.tick()
        await eventually(lambda: len(self.delays) > before or task.done())


class AromaPeer:
    """No BLE client construction; implements only an inert GATT boundary."""

    def __init__(self, device=None):
        self.device = device or Device(
            ble_address="02:00:00:00:00:02", ble_name="Synthetic Aroma-Link",
            device_type=Types.AROMA_LINK,
        )
        self.is_connected = True
        characteristic = SimpleNamespace(properties=["write"])
        self.services = SimpleNamespace(get_characteristic=lambda uuid: characteristic)
        self.events = []
        self.writes = []
        self.before_write: Callable[[bytes], Awaitable[None]] | None = None
        self.before_stop: Callable[[], Awaitable[None]] | None = None
        self.error: Exception | None = None
        self.reply_status = True
        self.reply_frequency = False
        self.defer_week = False
        self.week_header = True
        self.week_sent = asyncio.Event()
        self.week_tail = b""
        self.second = 0
        self.install()

    def install(self):
        self.device._ble_client = self
        self.device._ble_connected = True
        self.device._ble_notify_subscribed = True
        self.device._ble_has_synced_time = True  # Never send an implicit clock write.

    def frame(self, payload):
        return Protocol._build_packet(bytes(payload))

    def notify(self, payload, chunk_size=20):
        self.deliver(self.frame(payload), chunk_size)

    def deliver(self, frame, chunk_size=20):
        for start in range(0, len(frame), chunk_size):
            self.device._on_ble_notification(0, bytearray(frame[start:start + chunk_size]))

    async def write_gatt_char(self, uuid, data, *, response):
        frame = bytes(data)
        assert frame[:3] == MODULES.const.AL_HEADER
        payload = frame[4:-3]
        self.writes.append(bytes(payload))
        self.events.append(("write", payload[:2]))
        if self.before_write:
            await self.before_write(payload)
        if self.error:
            raise self.error
        if payload[:2] == b"\x52\x0a" and self.reply_status:
            self.notify(status_payload(self.second, 30 - self.second, 90 - self.second))
            self.second = (self.second + 1) % 30
        elif payload[:2] == b"\x52\x1e":
            self.notify([0x52, 0x1E, 80])
        elif payload[:2] == b"\x52\x1d":
            self.notify([0x52, 0x1D, 0])
        elif payload[:2] == b"\x52\x06" and self.reply_frequency:
            self.notify(bytes([0x52, 0x06, 1, 0, 30, 0, 90, 0x11]))
        elif payload[:2] == b"\x52\x15":
            frame = self.frame(week_payload())
            if self.defer_week:
                self.week_tail = frame
                if self.week_header:
                    self.deliver(frame[:20])
                    self.week_tail = frame[20:]
            else:
                self.deliver(frame)
            self.week_sent.set()
        else:
            assert payload[0] == 0x52, "Physical commands are forbidden in this peer"

    def finish_week(self):
        self.deliver(self.week_tail)
        self.week_tail = b""

    async def start_notify(self, uuid, callback):
        self.events.append(("start_notify", uuid))

    async def stop_notify(self, uuid):
        self.events.append(("stop_notify", uuid))
        if self.before_stop:
            await self.before_stop()

    async def disconnect(self):
        self.events.append(("disconnect", None))
        self.is_connected = False


class RecorderView:
    """Observe actual sensor callbacks/attributes, not HA Recorder internals."""

    def __init__(self, sensor):
        self.sensor = sensor
        self.attempts = 0
        self.rows = []
        sensor.hass = object()
        sensor.async_write_ha_state = self.write

    def write(self):
        self.attempts += 1
        state = (self.sensor.native_value, self.sensor.extra_state_attributes, self.sensor.available)
        if not self.rows or self.rows[-1] != state:
            self.rows.append(state)


class AromaTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("connect", "connect_ex"):
            self.enterContext(patch.object(socket.socket, name, forbidden_io))
        self.enterContext(patch.object(socket, "getaddrinfo", forbidden_io))
        self.enterContext(patch.object(socket, "create_connection", forbidden_io))
        self.clock = ManualSleeps()
        self.enterContext(patch.object(MODULES.device, "asyncio", self.clock))
        self.enterContext(patch.object(MODULES.device, "AL_REPLY_TIMEOUT", 0.06, create=True))
        self.peer = AromaPeer()
        self.device = self.peer.device
        self.addAsyncCleanup(self.close_device, self.device)

    async def close_device(self, device):
        # Also observe baseline task exceptions so negative controls don't
        # generate unrelated "Task exception was never retrieved" failures.
        task = device._status_task
        await device.async_shutdown()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await REAL_ASYNCIO.sleep(0)

    async def start_reader(self):
        self.device.set_stay_connected(True)
        await eventually(lambda: bool(self.clock.delays))
        return self.device._status_task

    def status_writes(self, peer=None):
        return sum(frame[:2] == b"\x52\x0a" for frame in (peer or self.peer).writes)


class OilRecorderTests(AromaTestCase):
    async def test_clock_countdowns_keep_both_oil_attributes_stable(self):
        remaining = RecorderView(MODULES.sensor.DiffuserOilSensor(self.device, None))
        level = RecorderView(MODULES.sensor.DiffuserOilLevelSensor(self.device, None))
        work = MODULES.sensor.DiffuserWorkRemainSensor(self.device, None)
        self.peer.notify([0x52, 0x1E, 80])
        self.peer.notify([0x52, 0x1D, 0])
        initial = [dict(view.sensor.extra_state_attributes) for view in (remaining, level)]
        counts = [len(view.rows) for view in (remaining, level)]
        before = self.device.ble_last_update
        for second in range(1, 7):
            self.peer.notify(status_payload(second, 30 - second, 90 - second))
        self.assertEqual([len(view.rows) - count for view, count in zip((remaining, level), counts)], [0, 0])
        self.assertEqual([view.sensor.extra_state_attributes for view in (remaining, level)], initial)
        self.assertGreater(self.device.ble_last_update, before)
        self.assertGreaterEqual(remaining.attempts, 8)
        self.assertEqual(work.native_value, 24)
        self.assertIn("520a", "".join(self.device.recent_notifications))

    async def test_oil_reply_refreshes_only_its_measurement_even_if_unchanged(self):
        remaining = RecorderView(MODULES.sensor.DiffuserOilSensor(self.device, None))
        level = RecorderView(MODULES.sensor.DiffuserOilLevelSensor(self.device, None))
        self.peer.notify([0x52, 0x1E, 80])
        self.peer.notify([0x52, 0x1D, 0])
        old_level = dict(level.sensor.extra_state_attributes)
        old_remaining = dict(remaining.sensor.extra_state_attributes)
        self.peer.notify([0x52, 0x1E, 80])
        self.assertNotEqual(remaining.sensor.extra_state_attributes, old_remaining)
        self.assertEqual(level.sensor.extra_state_attributes, old_level)
        remaining_stamp = dict(remaining.sensor.extra_state_attributes)
        self.peer.notify([0x52, 0x1D, 1])
        self.assertEqual(level.sensor.native_value, "low")
        self.assertEqual(remaining.sensor.extra_state_attributes, remaining_stamp)
        self.peer.notify([0x52, 0x1E, 79])
        self.assertEqual(remaining.sensor.native_value, 79)
        self.assertEqual(remaining.rows[-1][0], 79)
        self.assertEqual(len(remaining.rows), 3)

    async def test_status_before_oil_does_not_invent_measurement_freshness(self):
        remaining = MODULES.sensor.DiffuserOilSensor(self.device, None)
        level = MODULES.sensor.DiffuserOilLevelSensor(self.device, None)
        self.peer.notify(status_payload())
        self.assertIsNotNone(self.device.ble_last_update)
        for sensor in (remaining, level):
            self.assertIsNone(sensor.extra_state_attributes)
            self.assertFalse(sensor.available)
        # A partial oil reply is not a measurement either.
        frame = self.peer.frame([0x52, 0x1E, 80])
        self.peer.deliver(frame[:5])
        self.assertIsNone(remaining.extra_state_attributes)
        self.peer.deliver(frame[5:])
        self.assertEqual(remaining.native_value, 80)
        self.assertIsNotNone(remaining.extra_state_attributes)

    async def test_other_family_oil_timestamp_contract_is_unchanged(self):
        for family in (Types.SCENT_MARKETING_AK, Types.SCENT_MARKETING_GW, Types.AI_DIFFUSER):
            with self.subTest(family=family):
                device = Device(device_type=family, ble_address="02:00:00:00:00:03")
                sensor = MODULES.sensor.DiffuserOilSensor(device, None)
                self.assertIsNone(sensor.extra_state_attributes)
                stamp = datetime(2026, 11, 1, tzinfo=timezone.utc)
                device._ble_last_update = stamp
                self.assertEqual(sensor.extra_state_attributes, {"last_device_update": stamp.isoformat()})


class StatusReaderTests(AromaTestCase):
    async def test_failure_diagnostics_separate_preference_worker_and_live_link(self):
        self.peer.notify(status_payload())
        stamp = self.device.ble_last_update
        self.peer.error = EOFError("synthetic backend EOF")
        await self.start_reader()
        await self.clock.cycle(self.device)
        entry = SimpleNamespace(entry_id="synthetic", title="Synthetic", data={}, options={"stay_connected": True})
        hass = SimpleNamespace(data={MODULES.const.DOMAIN: {entry.entry_id: self.device}})
        result = await MODULES.diagnostics.async_get_config_entry_diagnostics(hass, entry)
        self.assertTrue(result["device"]["stay_connected"])
        self.assertTrue(result["device"]["status_reader_running"])
        self.assertFalse(result["device"]["ble_connected"])
        self.assertFalse(result["device"]["ble_notify_subscribed"])
        self.assertEqual(result["device"]["ble_last_update"], stamp.isoformat())

    async def test_default_off_uses_ten_second_idle_disconnect(self):
        self.device._schedule_disconnect()
        task = self.device._ble_disconnect_task
        await eventually(lambda: bool(self.clock.idle_delays))
        self.assertEqual(self.clock.idle_delays, [10])
        self.assertTrue(self.peer.is_connected)
        self.clock.idle_waiters.popleft().set_result(None)
        await task
        self.assertFalse(self.peer.is_connected)
        self.assertFalse(self.device._ble_connected)
        self.assertEqual(self.peer.writes, [])

    async def test_unexpected_eof_does_not_kill_reader_and_hold_link(self):
        self.peer.error = EOFError("synthetic backend EOF")
        task = await self.start_reader()
        await self.clock.cycle(self.device)
        error = task.exception() if task.done() and not task.cancelled() else None
        self.assertFalse(
            task.done(),
            f"Reader stopped with {type(error).__name__} while BLE connected={self.device._ble_connected}",
        )
        self.assertFalse(self.device._ble_connected)
        self.assertFalse(self.peer.is_connected)
        self.assertFalse(task.done())
        self.assertTrue(self.device._stay_connected)
        self.assertEqual([name for name, _ in self.peer.events], ["write", "stop_notify", "disconnect"])

    async def test_fixed_twenty_second_cadence_and_no_connect_or_commands(self):
        self.enterContext(patch.object(MODULES.device, "establish_connection", forbidden_io))
        await self.start_reader()
        self.assertEqual(self.peer.writes, [])
        for _ in range(3):
            await self.clock.cycle(self.device)
        self.assertEqual(self.clock.delays, [20, 20, 20, 20])
        self.assertEqual(self.status_writes(), 3)
        self.assertTrue(all(frame == b"\x52\x0a" for frame in self.peer.writes))

    async def test_expected_and_unexpected_exceptions_teardown_without_retries(self):
        for error in (BleakError, TimeoutError, OSError, EOFError, RuntimeError):
            with self.subTest(error=error.__name__):
                peer = AromaPeer()
                device = peer.device
                self.addAsyncCleanup(self.close_device, device)
                peer.error = error("synthetic status failure")
                snapshots = []
                device.register_state_callback(lambda: snapshots.append((device._ble_connected, device._ble_client)))
                device.set_stay_connected(True)
                await eventually(lambda: device._status_task is not None and any(
                    not f.done() for f in self.clock.waiters
                ))
                await self.clock.cycle(device)
                self.assertFalse(device._ble_connected)
                self.assertFalse(device._ble_notify_subscribed)
                self.assertIsNone(device._ble_client)
                self.assertFalse(device._ble_lock.locked())
                self.assertEqual([event[0] for event in peer.events], ["write", "stop_notify", "disconnect"])
                self.assertEqual(snapshots[-1], (False, None))
                self.assertTrue(device._stay_connected)  # Preference, not health.
                self.assertFalse(device._status_task.done())
                self.assertGreater(device._ble_last_failure_ts, 0)
                await self.clock.cycle(device)
                self.assertEqual(len(peer.writes), 1)
                await self.close_device(device)

    async def test_dropped_link_clears_connected_flags_and_callback(self):
        await self.start_reader()
        self.peer.is_connected = False
        snapshots = []
        self.device.register_state_callback(lambda: snapshots.append(self.device._ble_connected))
        await self.clock.cycle(self.device)
        self.assertFalse(self.device._ble_connected)
        self.assertIsNone(self.device._ble_client)
        self.assertEqual(snapshots, [False])

    async def test_no_notification_subscription_does_not_hold_or_read_link(self):
        self.device._ble_notify_subscribed = False
        await self.start_reader()
        self.assertIsNotNone(self.device._ble_disconnect_task)
        await self.clock.cycle(self.device)
        self.assertEqual(self.peer.writes, [])

    async def test_off_restores_idle_and_rapid_toggles_keep_one_observed_worker(self):
        task = await self.start_reader()
        for _ in range(12):
            self.device.set_stay_connected(False)
            self.device.set_stay_connected(True)
        self.assertIs(self.device._status_task, task)
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(), 1)
        self.device.set_stay_connected(False)
        self.assertIsNotNone(self.device._ble_disconnect_task)
        await self.clock.tick()
        await REAL_ASYNCIO.sleep(0.01)
        self.assertEqual(self.status_writes(), 1)
        await self.device.async_shutdown()
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertFalse(self.peer.is_connected)
        self.device.set_stay_connected(True)
        self.assertIsNone(self.device._status_task)

    async def test_external_cancellation_propagates_and_releases_idle_link(self):
        task = await self.start_reader()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await eventually(lambda: self.device._status_task is None)
        self.assertTrue(self.device._stay_connected)
        self.assertIsNotNone(self.device._ble_disconnect_task)
        self.assertFalse(self.device._ble_lock.locked())

    async def test_cancellation_during_send_and_reply_wait_releases_gates(self):
        for stage in ("send", "reply"):
            with self.subTest(stage=stage):
                peer = AromaPeer()
                device = peer.device
                self.addAsyncCleanup(self.close_device, device)
                entered = asyncio.Event()
                if stage == "send":
                    async def block(payload):
                        entered.set()
                        await asyncio.Event().wait()
                    peer.before_write = block
                else:
                    peer.reply_status = False
                    entered = asyncio.Event()
                    async def mark(payload):
                        entered.set()
                    peer.before_write = mark
                device.set_stay_connected(True)
                task = device._status_task
                await self.clock.tick()
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await REAL_ASYNCIO.sleep(0)
                self.assertFalse(device._ble_lock.locked())
                self.assertFalse(device._al_refresh_lock.locked())
                await device.async_shutdown()
                self.assertFalse(peer.is_connected)

    async def test_default_off_and_unsupported_families_never_start_reader(self):
        self.assertFalse(self.device._stay_connected)
        self.assertIsNone(self.device._status_task)
        self.device._schedule_disconnect()
        self.assertIsNotNone(self.device._ble_disconnect_task)
        for family in Types:
            if family == Types.AROMA_LINK:
                continue
            with self.subTest(family=family):
                device = Device(ble_address="02:00:00:00:00:03", device_type=family)
                device.set_stay_connected(True)
                self.assertFalse(device._stay_connected)
                self.assertIsNone(device._status_task)
        cloud = Device(cloud_device_id="synthetic")
        cloud.set_stay_connected(True)
        self.assertFalse(cloud._stay_connected)


class RefreshExclusionTests(AromaTestCase):
    async def test_setup_week_tail_survives_status_tick_after_final_settle(self):
        self.peer.defer_week = True
        await self.start_reader()
        refresh = asyncio.create_task(self.device.async_setup())
        self.addAsyncCleanup(self.cancel, refresh)
        await self.peer.week_sent.wait()
        await REAL_ASYNCIO.sleep(0.01)
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(), 1)
        self.assertFalse(refresh.done())
        self.peer.finish_week()
        await refresh
        self.assertIsNotNone(self.device.state.week_slots)

    async def test_every_full_read_caller_excludes_short_reads_until_delayed_week_complete(self):
        for caller in ("setup", "retry", "manual", "timed", "full_button"):
            with self.subTest(caller=caller):
                peer = AromaPeer()
                peer.defer_week = True
                device = peer.device
                self.addAsyncCleanup(self.close_device, device)
                device.set_stay_connected(True)
                call = {
                    "setup": device.async_setup,
                    "retry": device.async_retry_initial_refresh,
                    "manual": device.refresh_state,
                    "timed": device.async_periodic_refresh,
                    "full_button": device.async_full_refresh,
                }[caller]
                refresh = asyncio.create_task(call())
                self.addAsyncCleanup(self.cancel, refresh)
                await peer.week_sent.wait()
                before = self.status_writes(peer)
                # Step past the original final settle yield while RX is
                # incomplete. A completed send is not a completed reply.
                for _ in range(2):
                    await self.clock.cycle(device)
                self.assertEqual(self.status_writes(peer), before)
                self.assertFalse(refresh.done())
                self.assertIsNone(device.state.week_slots)
                peer.finish_week()
                await refresh
                self.assertIsNotNone(device.state.week_slots)
                self.assertEqual(device.state.work_seconds, 30)
                await self.clock.cycle(device)
                self.assertEqual(self.status_writes(peer), before + 1)
                self.assertFalse(device._al_refresh_lock.locked())
                await self.close_device(device)

    async def test_week_without_first_fragment_still_excludes_short_read(self):
        self.peer.defer_week = True
        self.peer.week_header = False
        await self.start_reader()
        refresh = asyncio.create_task(self.device.refresh_state())
        self.addAsyncCleanup(self.cancel, refresh)
        await self.peer.week_sent.wait()
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(), 1)
        self.assertFalse(refresh.done())
        self.peer.finish_week()
        await refresh
        self.assertIsNotNone(self.device.state.week_slots)

    async def test_timeout_and_cancel_discard_partial_week_and_release_exclusion(self):
        for outcome in ("timeout", "cancel"):
            with self.subTest(outcome=outcome):
                peer = AromaPeer()
                peer.defer_week = True
                device = peer.device
                self.addAsyncCleanup(self.close_device, device)
                device.set_stay_connected(True)
                refresh = asyncio.create_task(device.refresh_state())
                self.addAsyncCleanup(self.cancel, refresh)
                await peer.week_sent.wait()
                if outcome == "cancel":
                    refresh.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await refresh
                else:
                    await asyncio.wait_for(refresh, 0.5)
                self.assertFalse(device._al_refresh_lock.locked())
                self.assertFalse(device._protocol.rx_pending)
                # Orphan bare tails after abandonment must not update state.
                peer.finish_week()
                self.assertIsNone(device.state.week_slots)
                before = self.status_writes(peer)
                await self.clock.cycle(device)
                self.assertEqual(self.status_writes(peer), before + 1)
                self.assertFalse(device._al_refresh_lock.locked())
                await self.close_device(device)

    async def test_overlapping_full_reads_are_serialized_and_queued_cancel_is_safe(self):
        self.peer.defer_week = True
        first = asyncio.create_task(self.device.refresh_state())
        self.addAsyncCleanup(self.cancel, first)
        await self.peer.week_sent.wait()
        second = asyncio.create_task(self.device.refresh_state())
        queued = asyncio.create_task(self.device.async_retry_initial_refresh())
        self.addAsyncCleanup(self.cancel, second)
        self.addAsyncCleanup(self.cancel, queued)
        await REAL_ASYNCIO.sleep(0.01)
        self.assertEqual(self.status_writes(), 1)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertTrue(self.device._al_refresh_lock.locked())
        self.peer.finish_week()
        await asyncio.gather(first, second)
        self.assertEqual(self.status_writes(), 2)
        self.assertEqual(sum(p[:2] == b"\x52\x15" for p in self.peer.writes), 1)
        self.assertFalse(self.device._al_refresh_lock.locked())

    async def test_inflight_short_reply_precedes_full_read_even_before_header(self):
        self.peer.reply_status = False
        await self.start_reader()
        await self.clock.tick()
        await eventually(lambda: self.status_writes() == 1)
        refresh = asyncio.create_task(self.device.refresh_state())
        self.addAsyncCleanup(self.cancel, refresh)
        await REAL_ASYNCIO.sleep(0.01)
        self.assertEqual(self.status_writes(), 1)
        frame = self.peer.frame(status_payload(3))
        self.peer.deliver(frame[:20])
        await REAL_ASYNCIO.sleep(0.01)
        self.assertEqual(self.status_writes(), 1)
        self.peer.reply_status = True
        self.peer.deliver(frame[20:])
        await refresh
        self.assertEqual(self.status_writes(), 2)
        self.assertIsNotNone(self.device.state.week_slots)

    async def test_full_subquery_does_not_reset_prior_status_fragment(self):
        self.peer.reply_status = False
        original = self.peer.write_gatt_char
        status_sent = asyncio.Event()
        async def send(uuid, data, *, response):
            await original(uuid, data, response=response)
            if data[4:6] == b"\x52\x0a":
                self.peer.deliver(self.peer.frame(status_payload(4))[:20])
                status_sent.set()
        self.peer.write_gatt_char = send
        refresh = asyncio.create_task(self.device.refresh_state())
        self.addAsyncCleanup(self.cancel, refresh)
        await status_sent.wait()
        await REAL_ASYNCIO.sleep(0.01)
        self.assertEqual(len(self.peer.writes), 1)
        self.peer.deliver(self.peer.frame(status_payload(4))[20:])
        await refresh
        self.assertIsNotNone(self.device.state.week_slots)

    async def test_unexpected_refresh_exception_releases_busy_gate(self):
        await self.start_reader()
        self.peer.error = EOFError("synthetic full-read error")
        with self.assertRaises(EOFError):
            await self.device.refresh_state()
        self.assertFalse(self.device._al_refresh_lock.locked())
        self.peer.error = None
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(), 2)

    async def test_abandoned_unsolicited_fragment_has_bounded_reader_guard(self):
        await self.start_reader()
        frame = self.peer.frame(week_payload())
        self.peer.deliver(frame[:20])
        await self.clock.cycle(self.device)
        self.assertEqual(self.peer.writes, [])
        self.assertTrue(self.device._protocol.rx_pending)
        await REAL_ASYNCIO.sleep(0.07)
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(), 1)
        self.assertFalse(self.device._protocol.rx_pending)

    @staticmethod
    async def cancel(task):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TeardownRaceTests(AromaTestCase):
    async def test_unload_cancels_active_and_queued_full_reads_without_late_writes(self):
        self.peer.defer_week = True
        await self.start_reader()
        active = asyncio.create_task(self.device.refresh_state())
        self.addAsyncCleanup(RefreshExclusionTests.cancel, active)
        await self.peer.week_sent.wait()
        queued = asyncio.create_task(self.device.async_retry_initial_refresh())
        self.addAsyncCleanup(RefreshExclusionTests.cancel, queued)
        await REAL_ASYNCIO.sleep(0)
        before = len(self.peer.writes)
        await self.device.async_shutdown()
        self.assertTrue(active.cancelled())
        self.assertTrue(queued.cancelled())
        self.assertFalse(self.device._al_refresh_tasks)
        self.assertFalse(self.device._al_refresh_lock.locked())
        self.assertFalse(self.device._protocol.rx_pending)
        self.assertFalse(self.peer.is_connected)
        await self.device.refresh_state()
        self.assertFalse(await self.device._ble_send(self.device._protocol.build_query()))
        self.assertEqual(len(self.peer.writes), before)

    async def test_hung_status_send_has_bounded_failure_cleanup(self):
        async def hang(payload):
            await asyncio.Event().wait()
        self.peer.before_write = hang
        await self.start_reader()
        await self.clock.cycle(self.device)
        self.assertFalse(self.device._ble_connected)
        self.assertFalse(self.peer.is_connected)
        self.assertFalse(self.device._ble_lock.locked())
        self.assertFalse(self.device._al_refresh_lock.locked())
        self.assertEqual(len(self.peer.writes), 1)

    async def test_hung_unsubscribe_is_bounded_and_still_disconnects(self):
        async def hang():
            await asyncio.Event().wait()
        self.peer.before_stop = hang
        self.peer.error = EOFError("synthetic failure")
        await self.start_reader()
        await self.clock.cycle(self.device)
        self.assertFalse(self.peer.is_connected)
        self.assertIsNone(self.device._ble_client)
        self.assertFalse(self.device._ble_lock.locked())
        self.assertFalse(self.device._status_task.done())

    async def test_cancellation_during_teardown_disconnects_before_propagating(self):
        entered = asyncio.Event()
        async def hang():
            entered.set()
            await asyncio.Event().wait()
        self.peer.before_stop = hang
        self.peer.error = EOFError("synthetic failure")
        task = await self.start_reader()
        await self.clock.tick()
        await entered.wait()
        task.cancel()
        # Shutdown must await, not cancel the already-cancelling task again.
        await self.device.async_shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.peer.is_connected)
        self.assertIsNone(self.device._ble_client)
        self.assertFalse(self.device._ble_lock.locked())

    async def test_option_toggle_during_failure_cleanup_does_not_cancel_it(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop():
            entered.set()
            await release.wait()
        self.peer.before_stop = stop
        self.peer.error = EOFError("synthetic failure")
        task = await self.start_reader()
        await self.clock.tick()
        await entered.wait()
        self.device.set_stay_connected(False)
        self.device.set_stay_connected(True)
        self.assertIs(self.device._status_task, task)
        self.assertFalse(task.done())
        release.set()
        await eventually(lambda: self.device._ble_client is None)
        self.assertFalse(self.peer.is_connected)
        self.assertFalse(self.device._ble_connected)
        self.assertFalse(self.device._ble_lock.locked())

    async def test_connect_waits_for_teardown_and_never_closes_replacement(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def stop():
            entered.set()
            await release.wait()
        self.peer.before_stop = stop
        self.peer.error = EOFError("synthetic failure")
        await self.start_reader()
        await self.clock.tick()
        await entered.wait()
        # Ignore cooldown only to exercise the replacement race, not recovery
        # policy. A separate test below asserts the real cooldown.
        self.enterContext(patch.object(MODULES.device, "BLE_FAILURE_COOLDOWN_SECONDS", 0))
        replacement = AromaPeer(Device(ble_address="02:00:00:00:00:04"))
        factory = self.enterContext(patch.object(MODULES.device, "establish_connection", AsyncMock(return_value=replacement)))
        connecting = asyncio.create_task(self.device._ble_connect())
        self.addAsyncCleanup(RefreshExclusionTests.cancel, connecting)
        await REAL_ASYNCIO.sleep(0.01)
        factory.assert_not_awaited()
        self.assertFalse(connecting.done())
        release.set()
        self.assertTrue(await connecting)
        self.assertIs(self.device._ble_client, replacement)
        self.assertTrue(replacement.is_connected)
        self.assertFalse(self.peer.is_connected)
        self.assertEqual(sum(event[0] == "disconnect" for event in self.peer.events), 1)
        self.assertFalse(any(event[0] == "disconnect" for event in replacement.events))

    async def test_status_failure_respects_existing_connect_cooldown(self):
        self.peer.error = EOFError("synthetic failure")
        await self.start_reader()
        await self.clock.cycle(self.device)
        factory = self.enterContext(patch.object(MODULES.device, "establish_connection", AsyncMock()))
        self.assertFalse(await self.device._ble_connect())
        factory.assert_not_awaited()

    async def test_only_normal_refresh_reconnects_after_failure_and_cooldown(self):
        self.peer.error = EOFError("synthetic failure")
        await self.start_reader()
        await self.clock.cycle(self.device)
        replacement = AromaPeer()
        replacement.device = self.device
        factory = self.enterContext(patch.object(
            MODULES.device, "establish_connection", AsyncMock(return_value=replacement)
        ))
        await self.clock.cycle(self.device)
        factory.assert_not_awaited()
        # Expire only the existing connect cooldown, without bypassing the
        # normal refresh method or its transport/notification path.
        self.device._ble_last_failure_ts = (
            asyncio.get_running_loop().time() - MODULES.device.BLE_FAILURE_COOLDOWN_SECONDS - 1
        )
        await self.device.async_periodic_refresh()
        factory.assert_awaited_once()
        self.assertIs(self.device._ble_client, replacement)
        self.assertTrue(self.device._ble_connected)
        self.assertIsNotNone(self.device.state.week_slots)
        before = self.status_writes(replacement)
        await self.clock.cycle(self.device)
        self.assertEqual(self.status_writes(replacement), before + 1)
        self.assertTrue(all(frame[0] == 0x52 for frame in replacement.writes))

    async def test_unload_waits_for_inflight_connect_then_closes_without_rearming(self):
        self.device._ble_client = None
        self.device._ble_connected = False
        self.device._ble_notify_subscribed = False
        connected, release = asyncio.Event(), asyncio.Event()
        async def establish(*args, **kwargs):
            connected.set()
            await release.wait()
            return self.peer
        self.enterContext(patch.object(MODULES.device, "establish_connection", establish))
        connecting = asyncio.create_task(self.device._ble_connect())
        self.addAsyncCleanup(RefreshExclusionTests.cancel, connecting)
        await connected.wait()
        shutting_down = asyncio.create_task(self.device.async_shutdown())
        self.addAsyncCleanup(RefreshExclusionTests.cancel, shutting_down)
        await REAL_ASYNCIO.sleep(0)
        self.assertFalse(shutting_down.done())
        self.device.set_stay_connected(True)
        release.set()
        self.assertFalse(await connecting)
        await shutting_down
        self.assertFalse(self.peer.is_connected)
        self.assertIsNone(self.device._ble_client)
        self.assertIsNone(self.device._status_task)
        self.assertFalse(await self.device._ble_connect())

    async def test_unload_cancels_and_awaits_reader_before_teardown(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def send(payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await REAL_ASYNCIO.sleep(0)
                cancelled.set()
        self.peer.before_write = send
        task = await self.start_reader()
        await self.clock.tick()
        await entered.wait()
        await self.device.async_shutdown()
        self.assertTrue(cancelled.is_set())
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertFalse(self.device._ble_lock.locked())
        self.assertFalse(self.device._al_refresh_lock.locked())
        self.assertEqual([name for name, _ in self.peer.events], ["write", "stop_notify", "disconnect"])


class OptionsLifecycleTests(AromaTestCase):
    async def test_option_form_default_save_disable_and_family_gating(self):
        for family in Types:
            entry = SimpleNamespace(data={"device_type": family.value, "connection_mode": "ble"}, options={"unrelated": 42})
            supported = MODULES.config_flow.ScentDiffuserConfigFlow.async_supports_options_flow(entry)
            self.assertEqual(supported, family in (Types.AROMA_LINK, Types.AI_DIFFUSER))
            if not supported:
                continue
            flow = MODULES.config_flow.ScentDiffuserOptionsFlow(entry)
            form = await flow.async_step_init()
            schema = {marker.schema: marker for marker in form["data_schema"].schema}
            self.assertEqual("stay_connected" in schema, family == Types.AROMA_LINK)
            if family == Types.AROMA_LINK:
                self.assertFalse(schema["stay_connected"].default)
            saved = await flow.async_step_init({"refresh_interval": 120, "stay_connected": True})
            self.assertEqual(saved["data"].get("stay_connected", False), family == Types.AROMA_LINK)
            self.assertEqual(saved["data"]["unrelated"], 42)
            entry.options = saved["data"]
            disabled = await flow.async_step_init({"refresh_interval": 300, "stay_connected": False})
            self.assertEqual(disabled["data"], {"unrelated": 42})
        for data in ({"connection_mode": "cloud"}, {"device_type": "unknown"}):
            self.assertFalse(MODULES.config_flow.ScentDiffuserConfigFlow.async_supports_options_flow(SimpleNamespace(data=data)))

    async def test_real_entry_setup_options_update_and_unload_await_background_reader(self):
        background = []
        def create_background(coro, name):
            task = asyncio.create_task(coro, name=name)
            background.append(task)
            return task
        listeners = []
        unsubs = []
        entry = SimpleNamespace(
            entry_id="synthetic-entry", data={"ble_address": "02:00:00:00:00:02", "device_type": "aroma_link"},
            options={"stay_connected": True},
            async_on_unload=unsubs.append,
            add_update_listener=lambda cb: (listeners.append(cb) or (lambda: None)),
        )
        hass = SimpleNamespace(
            data={}, async_create_background_task=create_background,
            services=SimpleNamespace(has_service=lambda *args: True),
            config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True)),
        )
        # Keep the actual lifecycle wiring, reader, refresh and shutdown; only
        # supply the already-inert device and prevent live scheduling/registry.
        device = self.device
        device._hass = hass
        self.peer.reply_frequency = True
        self.enterContext(patch.object(MODULES.integration, "ScentDiffuserDevice", Mock(return_value=device)))
        self.enterContext(patch.object(MODULES.integration, "async_track_time_interval", lambda *a: (lambda: None)))
        self.enterContext(patch.object(MODULES.device, "ScentDiffuserDevice", Device))
        # isinstance in unload's annotation is not evaluated; service fan-out
        # is never entered because the inert HA reports it already registered.
        self.assertTrue(await MODULES.integration.async_setup_entry(hass, entry))
        self.assertEqual(len(background), 1)
        self.assertIs(device._status_task, background[0])
        self.assertTrue(device._stay_connected)
        self.assertTrue(listeners)
        entry.options = {"stay_connected": False}
        await listeners[0](hass, entry)
        self.assertFalse(device._stay_connected)
        self.assertIsNotNone(device._ble_disconnect_task)
        self.assertTrue(await MODULES.integration.async_unload_entry(hass, entry))
        self.assertTrue(background[0].done())
        self.assertFalse(self.peer.is_connected)
        self.assertNotIn(entry.entry_id, hass.data[MODULES.const.DOMAIN])
        entry.options = {"stay_connected": True}
        await listeners[0](hass, entry)
        self.assertFalse(device._stay_connected)
        self.assertIsNone(device._status_task)
