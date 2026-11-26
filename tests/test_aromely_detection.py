"""Offline regressions for Aromely advertisement detection."""
from types import SimpleNamespace
import unittest

from tests.offline import MODULES


detect = MODULES.protocol_ble.detect_device_type
DeviceType = MODULES.const.DeviceType


def adv(local_name=None, manufacturer_data=None, service_uuids=None):
    return SimpleNamespace(
        local_name=local_name,
        manufacturer_data=manufacturer_data or {},
        service_uuids=service_uuids or [],
    )


class AromelyDetectionTest(unittest.TestCase):
    def test_aro_pro_name_without_af30_or_marker(self):
        # Aro Pro: FFE0 only in GATT, nothing Aromely-specific advertised.
        name = "YGZK-XXJ-V3TJ-CA2032112AC6"
        self.assertEqual(detect(name, adv(local_name=name)),
                         DeviceType.AROMELY_ARO_MAX)
        self.assertEqual(detect(name, None), DeviceType.AROMELY_ARO_MAX)

    def test_aro_max_af30_still_detected(self):
        self.assertEqual(
            detect("A1B2C3", adv(local_name="A1B2C3", service_uuids=[
                "0000af30-0000-1000-8000-00805f9b34fb"])),
            DeviceType.AROMELY_ARO_MAX)

    def test_unrelated_ygzk_prefix_not_matched(self):
        self.assertIsNone(detect("YGZK-LAMP-1", adv(local_name="YGZK-LAMP-1")))


if __name__ == "__main__":
    unittest.main()
