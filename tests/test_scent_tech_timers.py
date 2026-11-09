"""Offline regressions for Scent Tech timer confirmation and HA callers."""
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import fields, replace
from datetime import time
import socket
import unittest
from unittest.mock import AsyncMock, patch

from tests.offline import BleakError, HomeAssistantError, MODULES, forbidden_io


Timer = MODULES.protocol_ble.ScentTechTimer
Device = MODULES.device.ScentDiffuserDevice


def timer_table():
    """Five synthetic, distinct records; no captured device/account data."""
    return {
        slot: Timer(
            enabled=bool(slot % 2), serial=20 + slot, weekdays=0x9F,
            start_minute=480 + slot, stop_minute=1080 + slot,
            run_seconds=10 + slot, pause_seconds=100 + slot,
            timer_id=0xA0B00000 + slot,
        )
        for slot in range(1, 6)
    }


class TimerPeer:
    """In-memory peer at the BLE send boundary; real RX/parser/state path.

    Keeps its stored table separate from the production device cache. Every
    read is delivered as a synthetic 0x88 through _on_ble_notification; ACKs
    use 0x94. No transport object is ever created.
    """

    def __init__(self):
        self.device = Device(
            ble_address="02:00:00:00:00:01", ble_name="Synthetic diffuser",
            device_type=MODULES.const.DeviceType.SCENT_TECH,
        )
        self.table = timer_table()
        self.initial = dict(self.table)
        self.ack = True
        self.apply_write = True
        self.transform = lambda table: table
        self.drop_queries = set()
        self.error_stage: str | None = None
        self.error: Exception | None = None
        self.before_send: Callable[[str], Awaitable[None]] | None = None
        self.query_count = 0
        self.stages = []
        self.writes = []
        self.snapshots = []
        self.device.register_state_callback(
            lambda: self.snapshots.append(dict(self.device.state.timer_slots or {}))
        )
        self.device._ble_connect = AsyncMock(return_value=True)
        self.device._ble_send = AsyncMock(side_effect=self.send)
        self.device._teardown_ble_client = AsyncMock()

    def notify(self, command, payload=b""):
        frame = self.device._protocol._frame(command, payload)
        self.device._on_ble_notification(0, bytearray(frame))

    async def send(self, frame):
        command = frame[3]
        if command == 0x08:
            self.query_count += 1
            stage = f"read-{self.query_count}"
        elif command == 0x14:
            stage = "write"
        else:
            raise AssertionError(f"Unexpected command {command:#x}")
        self.stages.append(stage)
        if self.before_send:
            await self.before_send(stage)
        if self.error_stage == stage:
            assert self.error is not None
            raise self.error
        if command == 0x08:
            if self.query_count not in self.drop_queries:
                payload = len(self.table).to_bytes(2, "little") + b"".join(
                    timer.to_bytes() for timer in self.table.values()
                )
                self.notify(0x88, payload)
        else:
            written = Timer.from_bytes(frame[4:-2])
            self.writes.append(written)
            slot = next(
                slot for slot, old in self.table.items()
                if (old.serial, old.timer_id) == (written.serial, written.timer_id)
            )
            if self.apply_write:
                self.table = self.transform({**self.table, slot: written})
            if self.ack:
                self.notify(0x94)
        return True


class OfflineTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Exercise real asyncio event timeouts, shortened only for offline tests.
        self.enterContext(patch.object(MODULES.device, "SCENT_TECH_REPLY_TIMEOUT", 0.01))
        # Defense in depth: tests cannot fall through to DNS/network calls.
        for name in ("connect", "connect_ex"):
            self.enterContext(patch.object(socket.socket, name, forbidden_io))
        self.enterContext(patch.object(socket, "getaddrinfo", forbidden_io))
        self.enterContext(patch.object(socket, "create_connection", forbidden_io))
        self.peer = TimerPeer()
        self.device = self.peer.device

    def assert_unlocked(self):
        self.assertFalse(self.device._timer_write_lock.locked())
        self.assertFalse(self.device._ble_lock.locked())

    def assert_no_retry(self, count=1):
        self.assertEqual(len(self.peer.writes), count)
        self.device._ble_connect.assert_awaited_once()


class TimerConfirmationTests(OfflineTestCase):
    async def test_exact_readback_succeeds_with_or_without_ack(self):
        for ack in (True, False):
            with self.subTest(ack=ack):
                peer = TimerPeer()
                peer.ack = ack
                desired = replace(peer.initial[3], run_seconds=45)
                self.assertTrue(await peer.device.set_timer_slot(3, run_seconds=45))
                self.assertEqual(peer.writes, [desired])
                self.assertEqual(peer.device.state.timer_slots, {**peer.initial, 3: desired})
                self.assertEqual(peer.stages, ["read-1", "write", "read-2"])
                self.assertFalse(peer.device._timer_write_lock.locked())
                peer.device._teardown_ble_client.assert_not_awaited()

    async def test_full_record_and_other_slots_preserved(self):
        changes = dict(enabled=False, weekdays=0xE0, start_minute=600,
                       stop_minute=900, run_seconds=23, pause_seconds=456)
        desired = replace(self.peer.initial[3], **changes)
        self.assertTrue(await self.device.set_timer_slot(3, **changes))
        self.assertEqual(self.peer.writes, [desired])
        self.assertEqual(self.device.state.timer_slots[3], desired)
        self.assertEqual(desired.serial, self.peer.initial[3].serial)
        self.assertEqual(desired.timer_id, self.peer.initial[3].timer_id)
        for slot in (1, 2, 4, 5):
            self.assertEqual(self.device.state.timer_slots[slot], self.peer.initial[slot])
        self.assertEqual(self.peer.snapshots, [self.peer.initial, self.peer.table])
        self.assert_no_retry()
        self.assert_unlocked()

    async def test_ack_with_old_readback_is_failure(self):
        self.peer.apply_write = False
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.assertEqual(self.device.state.timer_slots, self.peer.initial)
        self.assertEqual(self.peer.snapshots, [self.peer.initial, self.peer.initial])
        self.assert_no_retry()
        self.assert_unlocked()

    async def test_mismatch_in_every_record_field_is_failure(self):
        # run_seconds is requested; all other fields, including identity, are
        # deliberately unchanged by the request and still must be confirmed.
        different = dict(enabled=False, serial=99, weekdays=0xE0,
                         start_minute=600, stop_minute=900, run_seconds=46,
                         pause_seconds=456, timer_id=0xC0D00003)
        self.assertEqual(set(different), {field.name for field in fields(Timer)})
        for field, value in different.items():
            with self.subTest(field=field):
                peer = TimerPeer()
                peer.transform = lambda table: {
                    **table, 3: replace(table[3], **{field: value})
                }
                self.assertFalse(await peer.device.set_timer_slot(3, run_seconds=45))
                # Preserve the actual read-back, not the request or a rollback.
                self.assertEqual(peer.device.state.timer_slots, peer.table)
                self.assertNotEqual(peer.table[3], peer.writes[0])
                self.assertEqual(len(peer.writes), 1)
                for slot in (1, 2, 4, 5):
                    self.assertEqual(peer.table[slot], peer.initial[slot])

    async def test_missing_readback_never_echoes_even_with_ack_or_matching_cache(self):
        for ack in (True, False):
            for value in (13, 45):  # 13 already matches slot 3's old cached value.
                with self.subTest(ack=ack, value=value):
                    peer = TimerPeer()
                    peer.ack = ack
                    peer.drop_queries = {2}
                    self.assertFalse(await peer.device.set_timer_slot(3, run_seconds=value))
                    self.assertEqual(peer.device.state.timer_slots, peer.initial)
                    self.assertEqual(peer.snapshots, [peer.initial])
                    self.assertEqual(peer.query_count, 2)
                    self.assertEqual(len(peer.writes), 1)
                    peer.device._teardown_ble_client.assert_not_awaited()

    async def test_missing_target_or_empty_readback_is_failure(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                peer = TimerPeer()
                peer.transform = lambda table: {} if empty else {
                    slot: record for slot, record in table.items() if slot != 5
                }
                self.assertFalse(await peer.device.set_timer_slot(5, run_seconds=45))
                self.assertEqual(peer.device.state.timer_slots, peer.table)
                self.assertNotIn(5, peer.device.state.timer_slots)
                self.assertEqual(len(peer.writes), 1)

    async def test_no_initial_readback_refuses_write_despite_cached_table(self):
        self.device.state.timer_slots = dict(self.peer.initial)
        self.peer.drop_queries = {1}
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.assertEqual(self.peer.writes, [])
        self.assertEqual(self.peer.stages, ["read-1"])
        self.assertEqual(self.device.state.timer_slots, self.peer.initial)
        self.assert_unlocked()

    async def test_initial_target_missing_refuses_write(self):
        del self.peer.table[5]
        self.assertFalse(await self.device.set_timer_slot(5, run_seconds=45))
        self.assertEqual(self.peer.writes, [])
        self.assertEqual(self.device.state.timer_slots, self.peer.table)

    async def test_connection_failure_refuses_write(self):
        self.device._ble_connect.return_value = False
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.device._ble_send.assert_not_awaited()
        self.assert_unlocked()

    async def test_invalid_times_and_durations_refuse_write(self):
        for field in ("start_minute", "stop_minute", "run_seconds", "pause_seconds"):
            for value in (-1, 1440 if field.endswith("minute") else 65536):
                with self.subTest(field=field, value=value):
                    peer = TimerPeer()
                    self.assertFalse(await peer.device.set_timer_slot(3, **{field: value}))
                    self.assertEqual(peer.writes, [])
                    self.assertEqual(peer.device.state.timer_slots, peer.initial)

    async def test_wrong_family_or_missing_address_refuses_without_io(self):
        self.device._ble_address = None
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.device._ble_address = "02:00:00:00:00:01"
        self.device._protocol = MODULES.protocol_ble.AromaLinkBleProtocol()
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.device._ble_connect.assert_not_awaited()
        self.device._ble_send.assert_not_awaited()

    async def test_stale_ack_and_timer_event_cannot_confirm_write(self):
        self.device._timer_write_acked.set()
        self.device._timers_received.set()
        self.peer.ack = False
        self.peer.drop_queries = {2}
        self.assertFalse(await self.device.set_timer_slot(3, run_seconds=45))
        self.assertEqual(self.device.state.timer_slots, self.peer.initial)
        self.assertFalse(self.device._timer_write_acked.is_set())
        self.assertFalse(self.device._timers_received.is_set())
        self.assert_no_retry()


class TimerLifecycleTests(OfflineTestCase):
    async def test_concurrent_edits_are_serialized_for_same_and_different_slots(self):
        for second_slot in (3, 4):
            with self.subTest(second_slot=second_slot):
                peer = TimerPeer()
                entered = asyncio.Event()
                release = asyncio.Event()

                async def gate(stage):
                    if stage == "write" and not peer.writes:
                        entered.set()
                        await release.wait()

                peer.before_send = gate
                first = asyncio.create_task(peer.device.set_timer_slot(3, run_seconds=45))
                self.addAsyncCleanup(self.cancel_task, first)
                await asyncio.wait_for(entered.wait(), 1)
                second = asyncio.create_task(
                    peer.device.set_timer_slot(second_slot, pause_seconds=222)
                )
                self.addAsyncCleanup(self.cancel_task, second)
                await asyncio.sleep(0)
                self.assertEqual(peer.stages, ["read-1", "write"])
                peer.device._ble_connect.assert_awaited_once()
                self.assertFalse(second.done())
                release.set()
                self.assertEqual(await asyncio.gather(first, second), [True, True])
                self.assertEqual(peer.stages, ["read-1", "write", "read-2",
                                               "read-3", "write", "read-4"])
                expected = {**peer.initial, 3: replace(peer.initial[3], run_seconds=45)}
                expected[second_slot] = replace(expected[second_slot], pause_seconds=222)
                self.assertEqual(peer.device.state.timer_slots, expected)
                self.assertEqual(peer.writes[1], expected[second_slot])
                self.assertFalse(peer.device._timer_write_lock.locked())

    async def cancel_task(self, task):
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_io_errors_return_false_teardown_and_release_locks_without_retry(self):
        for stage in ("read-1", "write", "read-2"):
            for error_type in (BleakError, TimeoutError, OSError):
                with self.subTest(stage=stage, error=error_type.__name__):
                    peer = TimerPeer()
                    peer.error_stage = stage
                    peer.error = error_type("synthetic transport failure")

                    async def teardown(**kwargs):
                        self.assertTrue(peer.device._timer_write_lock.locked())
                        self.assertTrue(peer.device._ble_lock.locked())

                    peer.device._teardown_ble_client.side_effect = teardown
                    self.assertFalse(await peer.device.set_timer_slot(3, run_seconds=45))
                    peer.device._teardown_ble_client.assert_awaited_once_with(
                        reason="write-failure"
                    )
                    self.assertGreater(peer.device._ble_last_failure_ts, 0)
                    self.assertFalse(peer.device._timer_write_lock.locked())
                    self.assertFalse(peer.device._ble_lock.locked())
                    self.assertEqual(len(peer.writes), 1 if stage == "read-2" else 0)
                    self.assertLessEqual(peer.stages.count("write"), 1)
                    if stage != "read-1":
                        self.assertEqual(peer.device.state.timer_slots, peer.initial)
                    # Another explicit operation can acquire the released lock.
                    peer.error_stage = None
                    self.assertTrue(await peer.device.set_timer_slot(3, pause_seconds=222))

    async def test_unexpected_exception_propagates_and_releases_lock(self):
        self.peer.error_stage = "write"
        self.peer.error = ValueError("synthetic programming error")
        with self.assertRaisesRegex(ValueError, "synthetic programming error"):
            await self.device.set_timer_slot(3, run_seconds=45)
        self.assertEqual(self.device.state.timer_slots, self.peer.initial)
        self.device._teardown_ble_client.assert_not_awaited()
        self.assert_unlocked()

    async def test_cancellation_during_send_propagates_and_releases_lock(self):
        for stage in ("read-1", "write", "read-2"):
            with self.subTest(stage=stage):
                peer = TimerPeer()
                entered = asyncio.Event()

                async def gate(current):
                    if current == stage:
                        entered.set()
                        await asyncio.Event().wait()

                peer.before_send = gate
                task = asyncio.create_task(peer.device.set_timer_slot(3, run_seconds=45))
                self.addAsyncCleanup(self.cancel_task, task)
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(peer.device._timer_write_lock.locked())
                self.assertFalse(peer.device._ble_lock.locked())
                peer.device._teardown_ble_client.assert_not_awaited()
                if stage != "read-1":
                    self.assertEqual(peer.device.state.timer_slots, peer.initial)
                peer.before_send = None
                self.assertTrue(await peer.device.set_timer_slot(3, pause_seconds=222))

    async def test_cancellation_during_event_waits_leaves_no_waiter_or_retry(self):
        for wait in ("initial", "ack", "readback"):
            with self.subTest(wait=wait):
                peer = TimerPeer()
                if wait == "initial":
                    peer.drop_queries = {1}
                    event = peer.device._timers_received
                elif wait == "ack":
                    peer.ack = False
                    event = peer.device._timer_write_acked
                else:
                    peer.drop_queries = {2}
                    event = peer.device._timers_received
                waiting = asyncio.Event()
                real_wait = event.wait

                async def observe_wait():
                    if wait != "readback" or peer.query_count == 2:
                        waiting.set()
                    return await real_wait()

                with patch.object(event, "wait", side_effect=observe_wait):
                    task = asyncio.create_task(peer.device.set_timer_slot(3, run_seconds=45))
                    self.addAsyncCleanup(self.cancel_task, task)
                    await asyncio.wait_for(waiting.wait(), 1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertFalse(peer.device._timer_write_lock.locked())
                self.assertEqual(len(peer.writes), 0 if wait == "initial" else 1)
                self.assertEqual(asyncio.all_tasks(), {asyncio.current_task()})
                peer.device._teardown_ble_client.assert_not_awaited()

    async def test_cancelled_queued_edit_does_not_write_or_release_owner_lock(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def gate(stage):
            if stage == "write":
                entered.set()
                await release.wait()

        self.peer.before_send = gate
        owner = asyncio.create_task(self.device.set_timer_slot(3, run_seconds=45))
        self.addAsyncCleanup(self.cancel_task, owner)
        await asyncio.wait_for(entered.wait(), 1)
        queued = asyncio.create_task(self.device.set_timer_slot(3, pause_seconds=222))
        self.addAsyncCleanup(self.cancel_task, queued)
        await asyncio.sleep(0)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertTrue(self.device._timer_write_lock.locked())
        self.assertEqual(self.peer.stages, ["read-1", "write"])
        release.set()
        self.assertTrue(await owner)
        self.assertEqual(len(self.peer.writes), 1)
        self.assertEqual(self.peer.writes[0].pause_seconds, self.peer.initial[3].pause_seconds)
        self.assert_unlocked()


class TimerEntityTests(OfflineTestCase):
    def surfaces(self, device):
        """Every HA-facing caller and both field variants where applicable."""
        number = MODULES.number.ScentTechTimerDuration
        clock = MODULES.time.ScentTechTimerTime
        days = MODULES.select.ScentTechTimerDays
        switch = MODULES.switch.ScentTechTimerSwitch
        return (
            (number(device, 3, "run_seconds"), "async_set_native_value", 45.0, {"run_seconds": 45}),
            (number(device, 3, "pause_seconds"), "async_set_native_value", 222.0, {"pause_seconds": 222}),
            (clock(device, 3, "start_minute"), "async_set_value", time(9, 15), {"start_minute": 555}),
            (clock(device, 3, "stop_minute"), "async_set_value", time(19, 30), {"stop_minute": 1170}),
            (days(device, 3), "async_select_option", "Weekends (Sat, Sun)", {"weekdays": 0xE0}),
            (days(device, 3), "async_select_option", "No days", {"weekdays": 0}),
            (switch(device, 3), "async_turn_on", None, {"enabled": True}),
            (switch(device, 3), "async_turn_off", None, {"enabled": False}),
        )

    async def invoke(self, surface):
        entity, method, value, changes = surface
        return await getattr(entity, method)(*(() if value is None else (value,)))

    async def test_all_callers_convert_false_to_home_assistant_error(self):
        self.device.state.timer_slots = dict(self.peer.initial)
        self.device.set_timer_slot = AsyncMock(return_value=False)
        for surface in self.surfaces(self.device):
            entity, method, value, changes = surface
            with self.subTest(entity=type(entity).__name__, changes=changes):
                with self.assertRaisesRegex(HomeAssistantError, "Schedule 3 write was not confirmed"):
                    await self.invoke(surface)
                self.device.set_timer_slot.assert_awaited_with(3, **changes)
                self.assertEqual(self.device.state.timer_slots, self.peer.initial)
        self.assertEqual(self.device.set_timer_slot.await_count, len(self.surfaces(self.device)))

    async def test_all_callers_accept_confirmed_result(self):
        self.device.set_timer_slot = AsyncMock(return_value=True)
        for surface in self.surfaces(self.device):
            with self.subTest(changes=surface[3]):
                self.assertIsNone(await self.invoke(surface))
                self.device.set_timer_slot.assert_awaited_with(3, **surface[3])

    async def test_all_callers_propagate_unexpected_exception_and_cancellation(self):
        for error in (ValueError("synthetic error"), asyncio.CancelledError()):
            self.device.set_timer_slot = AsyncMock(side_effect=error)
            for surface in self.surfaces(self.device):
                with self.subTest(changes=surface[3], error=type(error).__name__):
                    with self.assertRaises(type(error)) as raised:
                        await self.invoke(surface)
                    self.assertIs(raised.exception, error)

    async def test_all_callers_end_to_end_confirm_or_surface_missing_readback(self):
        for confirmed in (True, False):
            for index in range(len(self.surfaces(self.device))):
                peer = TimerPeer()
                if not confirmed:
                    peer.drop_queries = {2}
                surface = self.surfaces(peer.device)[index]
                with self.subTest(confirmed=confirmed, changes=surface[3]):
                    if confirmed:
                        self.assertIsNone(await self.invoke(surface))
                        expected = replace(peer.initial[3], **surface[3])
                    else:
                        with self.assertRaises(HomeAssistantError):
                            await self.invoke(surface)
                        expected = peer.initial[3]
                    self.assertEqual(peer.device.state.timer_slots[3], expected)
                    self.assertEqual(len(peer.writes), 1)
                    for slot in (1, 2, 4, 5):
                        self.assertEqual(peer.device.state.timer_slots[slot], peer.initial[slot])


if __name__ == "__main__":
    unittest.main()
