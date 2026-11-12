"""Offline regressions for BLE power reports received during a command."""
import unittest
from unittest.mock import AsyncMock

from tests.offline import MODULES


Device = MODULES.device.ScentDiffuserDevice
DeviceType = MODULES.const.DeviceType


class PowerReadbackRaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = Device(
            ble_address="02:00:00:00:00:01",
            ble_name="Synthetic Aroma-Link diffuser",
            device_type=DeviceType.AROMA_LINK,
        )

    def status_frame(self, power: bool, phase: int) -> bytes:
        payload = bytes([0x53, 0x0A, *([0] * 9), int(power), phase])
        return self.device._protocol._build_packet(payload)

    async def test_power_off_report_during_power_on_wins(self):
        async def execute(_frame):
            report = self.status_frame(False, 0)
            self.device._on_ble_notification(0, bytearray(report))
            return True

        self.device._ble_execute = AsyncMock(side_effect=execute)
        self.assertTrue(await self.device.set_power(True))
        self.assertIs(self.device.state.power, False)
        self.assertEqual(self.device.state.phase, "off")

    async def test_spraying_report_during_power_off_wins(self):
        async def execute(_frame):
            report = self.status_frame(True, 1)
            self.device._on_ble_notification(0, bytearray(report))
            return True

        self.device._ble_execute = AsyncMock(side_effect=execute)
        self.assertTrue(await self.device.set_power(False))
        self.assertIs(self.device.state.power, True)
        self.assertEqual(self.device.state.phase, "spraying")

    async def test_no_report_keeps_existing_optimistic_update(self):
        self.device._ble_execute = AsyncMock(return_value=True)

        self.assertTrue(await self.device.set_power(True))
        self.assertIs(self.device.state.power, True)
        self.assertEqual(self.device.state.phase, "idle")

        self.assertTrue(await self.device.set_power(False))
        self.assertIs(self.device.state.power, False)
        self.assertEqual(self.device.state.phase, "off")


if __name__ == "__main__":
    unittest.main()
