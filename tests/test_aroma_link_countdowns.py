"""Offline regressions for Aroma-Link remaining-time snapshots."""
import unittest

from tests.offline import MODULES


Device = MODULES.device.ScentDiffuserDevice
DeviceType = MODULES.const.DeviceType


class CountdownSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.device = Device(
            ble_address="02:00:00:00:00:01",
            ble_name="Synthetic Aroma-Link diffuser",
            device_type=DeviceType.AROMA_LINK,
        )
        self.work = MODULES.sensor.DiffuserWorkRemainSensor(self.device, object())
        self.pause = MODULES.sensor.DiffuserPauseRemainSensor(self.device, object())
        self.device.state.work_remaining = 27
        self.device.state.pause_remaining = 91

    def assert_values(self, work, pause):
        self.assertEqual(self.work.native_value, work)
        self.assertEqual(self.pause.native_value, pause)
        self.assertEqual(self.work.available, work is not None)
        self.assertEqual(self.pause.available, pause is not None)

    def test_only_the_active_phase_exposes_its_counter(self):
        self.device.state.power = True
        self.device.state.phase = "spraying"
        self.assert_values(27, None)

        self.device.state.phase = "paused"
        self.assert_values(None, 91)

    def test_idle_off_and_unknown_do_not_expose_retained_durations(self):
        for power, phase in (
            (True, "idle"), (True, "unknown"), (False, "off"), (None, "unknown")
        ):
            with self.subTest(power=power, phase=phase):
                self.device.state.power = power
                self.device.state.phase = phase
                self.assert_values(None, None)

    def test_zero_is_a_valid_active_countdown(self):
        self.device.state.power = True
        self.device.state.phase = "spraying"
        self.device.state.work_remaining = 0
        self.assert_values(0, None)

        self.device.state.phase = "paused"
        self.device.state.pause_remaining = 0
        self.assert_values(None, 0)

    def test_missing_active_counter_stays_unavailable(self):
        self.device.state.power = True
        self.device.state.phase = "spraying"
        self.device.state.work_remaining = None
        self.assert_values(None, None)


if __name__ == "__main__":
    unittest.main()
