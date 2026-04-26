"""Config flow for Scent Diffuser integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from bleak import BleakScanner

from homeassistant import config_entries
from .const import (
    DOMAIN,
    CONF_DEVICE_TYPE,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CLOUD_USERNAME,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_DEVICE_ID,
    CONF_CLOUD_USER_ID,
    CONF_CONNECTION_MODE,
    DEFAULT_SCAN_TIMEOUT,
    DeviceType,
)
from .protocol_ble import detect_device_type
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)


class ScentDiffuserConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Scent Diffuser."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, dict] = {}
        self._cloud_client: AromaLinkCloudClient | None = None
        self._cloud_devices: list = []
        self._selected_ble_address: str | None = None
        self._selected_ble_name: str | None = None
        self._selected_device_type: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - choose connection method."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["ble_scan", "cloud"],
        )

    # ------------------------------------------------------------------
    # BLE flow
    # ------------------------------------------------------------------

    async def async_step_ble_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Scan for BLE devices and let user select one."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input.get("ble_address")
            if address:
                # User selected a device – proceed to create entry
                device_info = self._discovered_devices.get(address, {})
                self._selected_ble_address = address
                self._selected_ble_name = device_info.get("name", "")
                self._selected_device_type = device_info.get("device_type", "aroma_link")

                # Check if already configured
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()

                # Create entry directly - no extra steps needed for BLE
                return self.async_create_entry(
                    title=self._selected_ble_name or "Scent Diffuser",
                    data={
                        CONF_BLE_ADDRESS: self._selected_ble_address,
                        CONF_BLE_NAME: self._selected_ble_name,
                        CONF_DEVICE_TYPE: self._selected_device_type,
                        CONF_CONNECTION_MODE: "ble",
                    },
                )
            # If address is missing, user clicked Submit on an empty error
            # form – fall through to re-scan.

        # Scan for devices
        self._discovered_devices = {}
        try:
            devices = await BleakScanner.discover(timeout=DEFAULT_SCAN_TIMEOUT, return_adv=True)
            for device, adv_data in devices.values():
                name = device.name or adv_data.local_name or ""
                if not name:
                    continue

                dtype = detect_device_type(name)

                # Show all named devices - different brands may have
                # different BLE names. Auto-detected ones are marked.
                self._discovered_devices[device.address] = {
                    "name": name,
                    "device_type": dtype or DeviceType.AROMA_LINK,
                    "rssi": adv_data.rssi,
                    "auto_detected": dtype is not None,
                }
        except Exception as err:
            _LOGGER.error("BLE scan failed: %s", err)
            errors["base"] = "cannot_connect"

        if not self._discovered_devices and not errors:
            errors["base"] = "no_devices"

        if errors:
            return self.async_show_form(
                step_id="ble_scan",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Build selection list - auto-detected devices shown first with marker
        device_options = {}
        for addr, info in sorted(
            self._discovered_devices.items(),
            key=lambda x: (not x[1].get("auto_detected", False), -x[1]["rssi"]),
        ):
            short_mac = addr[-8:]  # Last 8 chars of MAC for identification
            if info.get("auto_detected"):
                device_options[addr] = f"✓ {info['name']} ({short_mac})"
            else:
                device_options[addr] = f"  {info['name']} ({short_mac})"

        return self.async_show_form(
            step_id="ble_scan",
            data_schema=vol.Schema({
                vol.Required("ble_address"): vol.In(device_options),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Cloud flow
    # ------------------------------------------------------------------

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle cloud login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_CLOUD_USERNAME]
            password = user_input[CONF_CLOUD_PASSWORD]

            self._cloud_client = AromaLinkCloudClient()
            if await self._cloud_client.login(username, password):
                self._cloud_devices = await self._cloud_client.get_devices()
                if self._cloud_devices:
                    # Store credentials for next step
                    self._cloud_username = username
                    self._cloud_password = password
                    return await self.async_step_cloud_device()
                errors["base"] = "no_devices"
            else:
                errors["base"] = "invalid_auth"

            await self._cloud_client.close()

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema({
                vol.Required(CONF_CLOUD_USERNAME): str,
                vol.Required(CONF_CLOUD_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_cloud_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select cloud device."""
        if user_input is not None:
            device_id = user_input[CONF_CLOUD_DEVICE_ID]

            # Find device name
            device_name = "Scent Diffuser"
            for dev in self._cloud_devices:
                if dev.device_id == device_id:
                    device_name = dev.name
                    break

            await self.async_set_unique_id(f"cloud_{device_id}")
            self._abort_if_unique_id_configured()

            data = {
                CONF_DEVICE_TYPE: "aroma_link",
                CONF_CLOUD_USERNAME: self._cloud_username,
                CONF_CLOUD_PASSWORD: self._cloud_password,
                CONF_CLOUD_DEVICE_ID: device_id,
                CONF_CLOUD_USER_ID: self._cloud_client.user_id,
                CONF_CONNECTION_MODE: "cloud",
            }

            if self._cloud_client:
                await self._cloud_client.close()

            return self.async_create_entry(title=device_name, data=data)

        device_options = {
            dev.device_id: f"{dev.name} ({'online' if dev.online else 'offline'})"
            for dev in self._cloud_devices
        }

        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema({
                vol.Required(CONF_CLOUD_DEVICE_ID): vol.In(device_options),
            }),
        )
