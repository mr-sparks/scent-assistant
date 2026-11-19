"""Offline tests for the Scent Marketing GW lamp (DP-11 light) support.

Covers the pieces the lamp switch relies on: the DP-11 frame builder, the
chunked transport, the DP-11 report parser (plain GW and XOR), the device
set_lamp() path, and the switch platform registering a Lamp entity for GW
devices (which previously only got one for the AK family).
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

# build_light(True)  == FF 01 | 00 0B AF 00 02 01 01
# build_light(False) == FF 01 | 00 0B AF 00 02 01 00
LIGHT_ON_FRAME = bytes.fromhex("FF01000BAF00020101")
LIGHT_OFF_FRAME = bytes.fromhex("FF01000BAF00020100")


def gw_device(device_type=DeviceType.SCENT_MARKETING_GW, address="02:00:00:00:00:01"):
    return Device(ble_address=address, ble_name="Synthetic GW diffuser", device_type=device_type)


def feed_gw_notification(proto, inner_frame: bytes, nonce: int = 0xAB):
    """Deliver one inner DP frame the way the wire does: one short chunk."""
    chunk = bytes([nonce, 0x01]) + inner_frame
    assert len(chunk) < 20
    return proto.parse_notification(chunk)


class GwLightFrameTests(unittest.TestCase):
    def test_build_light_on_off(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        self.assertEqual(proto.build_light(True), LIGHT_ON_FRAME)
        self.assertEqual(proto.build_light(False), LIGHT_OFF_FRAME)

    def test_build_light_colour_type_keeps_type_byte(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        self.assertEqual(
            proto.build_light(True, light_type=3),
            bytes.fromhex("FF01000BAF00020301"),
        )

    def test_wire_chunks_single_short_chunk(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        chunks = proto.wire_chunks(LIGHT_ON_FRAME)
        self.assertEqual(len(chunks), 1)
        self.assertLess(len(chunks[0]), 20)
        self.assertEqual(chunks[0][2:], LIGHT_ON_FRAME)

    def test_parse_light_report_on_off(self):
        proto = protocol_ble.ScentMarketingGwProtocol()
        self.assertIs(feed_gw_notification(proto, LIGHT_ON_FRAME)["light_on"], True)
        proto = protocol_ble.ScentMarketingGwProtocol()
        self.assertIs(feed_gw_notification(proto, LIGHT_OFF_FRAME)["light_on"], False)

    def test_parse_light_report_colour_types(self):
        # light_type 3/7: status 1 or 2 = on, anything else = off
        for status, expected in ((1, True), (2, True), (0, False)):
            frame = bytes.fromhex("FF01000BAF0002") + bytes([3, status])
            proto = protocol_ble.ScentMarketingGwProtocol()
            self.assertIs(feed_gw_notification(proto, frame)["light_on"], expected)


class GwXorLightTests(unittest.TestCase):
    MAC = "A1B2C3D4E5F6"

    def test_build_light_is_encrypted_json_dp11(self):
        proto = protocol_ble.ScentMarketingGwXorProtocol(mac=self.MAC)
        frame = proto.build_light(True)
        plaintext = proto.decrypt(frame)
        obj = json.loads(plaintext.decode("utf-8"))
        self.assertIn("data", obj)
        dp = obj["data"][str(const.SM_GW_DP_LIGHT)]
        self.assertEqual(dp["type"], const.SM_GW_TYPE_BINARY)
        self.assertEqual(dp["value"], "0101")
        frame_off = proto.build_light(False)
        obj_off = json.loads(proto.decrypt(frame_off).decode("utf-8"))
        self.assertEqual(obj_off["data"][str(const.SM_GW_DP_LIGHT)]["value"], "0100")

    def test_parse_encrypted_light_report(self):
        proto = protocol_ble.ScentMarketingGwXorProtocol(mac=self.MAC)
        payload = json.dumps({"data": {str(const.SM_GW_DP_LIGHT): {
            "type": const.SM_GW_TYPE_BINARY, "value": "0101",
        }}}).encode("utf-8")
        encrypted = proto.encrypt(payload)
        result = {}
        for chunk in proto.chunk_for_transport(encrypted):
            result = proto.parse_notification(chunk) or result
        self.assertIs(result.get("light_on"), True)


class GwLampDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("connect", "connect_ex"):
            self.enterContext(patch.object(socket.socket, name, forbidden_io))
        self.enterContext(patch.object(socket, "getaddrinfo", forbidden_io))
        self.enterContext(patch.object(socket, "create_connection", forbidden_io))
        # _ble_execute waits 1 s for a notification; keep tests fast.
        self.enterContext(patch("asyncio.sleep", new=AsyncMock()))
        self.device = gw_device()
        self.sent = []
        self.device._ble_connect = AsyncMock(return_value=True)
        self.device._ble_send = AsyncMock(side_effect=lambda frame: self.sent.append(frame) or True)
        self.device._teardown_ble_client = AsyncMock()

    async def test_set_lamp_sends_dp11_frame_and_updates_state(self):
        self.assertIsNone(self.device.state.light_on)
        self.assertTrue(await self.device.set_lamp(True))
        self.assertEqual(self.sent, [LIGHT_ON_FRAME])
        self.assertIs(self.device.state.light_on, True)
        self.assertTrue(await self.device.set_lamp(False))
        self.assertEqual(self.sent[-1], LIGHT_OFF_FRAME)
        self.assertIs(self.device.state.light_on, False)

    async def test_lamp_report_notification_updates_state(self):
        self.device._on_ble_notification(0, bytearray(bytes([0xAB, 0x01]) + LIGHT_ON_FRAME))
        self.assertIs(self.device.state.light_on, True)

    async def test_set_lamp_without_ble_address_fails_closed(self):
        device = Device(ble_address=None, ble_name="cloud-only", device_type=DeviceType.SCENT_MARKETING_GW)
        self.assertFalse(await device.set_lamp(True))


class LampSwitchSetupTests(unittest.IsolatedAsyncioTestCase):
    """The switch platform must register a Lamp entity for GW devices."""

    async def _collect(self, device):
        hass = SimpleNamespace(data={const.DOMAIN: {"entry-1": device}})
        entry = SimpleNamespace(entry_id="entry-1", data={})
        added = []
        # capability_entity isn't in the MODULES namespace; reach its `er`
        # import through the function's globals.
        er_stub = MODULES.switch.add_when_present.__globals__["er"]
        registry = SimpleNamespace()
        with patch.object(er_stub, "async_get", return_value=registry, create=True), \
             patch.object(er_stub, "async_entries_for_config_entry",
                          return_value=[], create=True):
            await MODULES.switch.async_setup_entry(hass, entry, added.extend)
        return added

    async def test_gw_device_gets_lamp_switch(self):
        for dtype in (DeviceType.SCENT_MARKETING_GW, DeviceType.SCENT_MARKETING_GW_XOR):
            with self.subTest(device_type=dtype):
                device = gw_device(dtype, address=f"02:00:00:00:00:0{len(dtype)}")
                added = await self._collect(device)
                by_name = {e._attr_name: e for e in added}
                self.assertIn("Power", by_name)
                self.assertIn("Child lock", by_name)
                self.assertIn("Lamp", by_name)
                lamp = by_name["Lamp"]
                self.assertEqual(lamp._attr_unique_id, f"{device.unique_id}_lamp")
                # GW has no verified fan write — no Fan switch.
                self.assertNotIn("Fan", by_name)

    async def test_ak_device_still_gets_lamp_and_program(self):
        device = gw_device(DeviceType.SCENT_MARKETING_AK, address="02:00:00:00:00:0A")
        added = await self._collect(device)
        names = {e._attr_name for e in added}
        self.assertTrue({"Power", "Child lock", "Lamp", "Program"} <= names)

    async def test_non_scent_marketing_device_gets_no_lamp(self):
        device = gw_device(DeviceType.SCENT_TECH, address="02:00:00:00:00:0T")
        added = await self._collect(device)
        self.assertNotIn("Lamp", {e._attr_name for e in added})

    async def test_lamp_switch_calls_device_set_lamp(self):
        device = gw_device()
        added = await self._collect(device)
        lamp = next(e for e in added if e._attr_name == "Lamp")
        device.set_lamp = AsyncMock(return_value=True)
        await lamp.async_turn_on()
        device.set_lamp.assert_awaited_with(True)
        await lamp.async_turn_off()
        device.set_lamp.assert_awaited_with(False)
        # is_on reflects the device state when no command is pending.
        device.state.light_on = True
        self.assertIs(lamp.is_on, True)


if __name__ == "__main__":
    unittest.main()
