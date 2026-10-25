"""Config flow for Scent Diffuser integration."""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

import voluptuous as vol
from bleak import BleakClient, BleakError
from bleak_retry_connector import establish_connection

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from .const import (
    BLE_CONNECT_MAX_ATTEMPTS,
    DOMAIN,
    CONF_DEVICE_TYPE,
    CONF_BLE_ADDRESS,
    CONF_BLE_NAME,
    CONF_CLOUD_USERNAME,
    CONF_CLOUD_PASSWORD,
    CONF_CLOUD_DEVICE_ID,
    CONF_CLOUD_USER_ID,
    CONF_CONNECTION_MODE,
    CONF_GW_PASSWORD,
    CONF_AIOT_NID,
    CONF_AIOT_CODE,
    SCENT_TECH_CHAR_UUID,
    DeviceType,
)
from .protocol_ble import (
    AromaIotProtocol,
    classify_gatt,
    detect_device_type,
    extract_scent_marketing_metadata,
)
from .protocol_cloud import AromaLinkCloudClient

_LOGGER = logging.getLogger(__name__)

_BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"
ISSUES_URL = "https://github.com/mr-sparks/scent-assistant/issues"


def _short_uuid(uuid: str) -> str:
    """"0000fff1-0000-1000-…" -> "fff1"; other UUIDs unchanged."""
    if uuid.startswith("0000") and uuid.endswith(_BASE_UUID_SUFFIX):
        return uuid[4:8]
    return uuid


def _format_gatt(services) -> str:
    """One line per service, its characteristics with their properties."""
    lines = []
    for service in services:
        chars = ", ".join(
            f"{_short_uuid(c.uuid)} ({'/'.join(c.properties)})"
            for c in service.characteristics
        )
        lines.append(f"{_short_uuid(service.uuid)}: {chars or '-'}")
    return "\n".join(lines) or "(no services)"


def _advertised_name(info: BluetoothServiceInfoBleak) -> str:
    """Return the advertised local name, or "" for a nameless device.

    BluetoothServiceInfo falls back to the address when a device
    advertises no name; treat that as nameless.
    """
    name = info.name or getattr(info.advertisement, "local_name", None) or ""
    if name.replace("-", ":").upper() == info.address.upper():
        return ""
    return name


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
        self._selected_sm_metadata: dict | None = None
        self._selected_gw_password: str | None = None
        self._selected_aiot_credentials: tuple[str, str] | None = None

    def _create_ble_entry(self) -> config_entries.ConfigFlowResult:
        """Build the BLE-mode config entry. Shared by all BLE setup paths."""
        entry_data: dict[str, Any] = {
            CONF_BLE_ADDRESS: self._selected_ble_address,
            CONF_BLE_NAME: self._selected_ble_name,
            CONF_DEVICE_TYPE: self._selected_device_type,
            CONF_CONNECTION_MODE: "ble",
        }
        if self._selected_sm_metadata:
            entry_data["sm_metadata"] = self._selected_sm_metadata
        if self._selected_gw_password:
            entry_data[CONF_GW_PASSWORD] = self._selected_gw_password
        if self._selected_aiot_credentials:
            entry_data[CONF_AIOT_NID], entry_data[CONF_AIOT_CODE] = self._selected_aiot_credentials
        return self.async_create_entry(
            title=self._selected_ble_name or "Scent Diffuser",
            data=entry_data,
        )

    async def async_step_gw_password(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Optional password prompt for Scent Marketing GW / Scent Tech devices."""
        if user_input is not None:
            pwd = (user_input.get(CONF_GW_PASSWORD) or "").strip()
            # The firmware accepts up to 4 ASCII chars. We trim silently.
            self._selected_gw_password = pwd[:4] if pwd else None
            return self._create_ble_entry()
        return self.async_show_form(
            step_id="gw_password",
            data_schema=vol.Schema({
                vol.Optional(CONF_GW_PASSWORD, default=""): str,
            }),
            description_placeholders={"device": self._selected_ble_name or ""},
        )

    async def _async_step_credentials(self) -> config_entries.ConfigFlowResult:
        """Ask for whatever the selected family needs before creating the
        entry. Shared by the discovery, scan and GATT-probe paths."""
        if self._needs_password_step():
            return await self.async_step_gw_password()
        if self._selected_device_type == DeviceType.AROMA_IOT:
            return await self.async_step_aiot_bind()
        return self._create_ble_entry()

    async def async_step_aiot_bind(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Aroma IOT: control needs a login, and a never-paired unit only
        accepts one after it has been BOUND to an id + code. Binding with
        HA's own pair locks the official app out (no reset procedure is
        known), so it only happens when the user opts in here (#49).
        Existing credentials can be entered to re-add a unit bound before.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            nid = str(user_input.get(CONF_AIOT_NID) or "").strip()
            code = str(user_input.get(CONF_AIOT_CODE) or "").strip()
            existing = bool(nid or code)
            if existing and not (nid.isdigit() and code):
                errors["base"] = "aiot_invalid_credentials"
            else:
                if not existing:
                    nid = str(secrets.randbelow(9 * 10**8) + 10**8)
                    code = secrets.token_hex(16)
                outcome = await self._aiot_login_probe(
                    nid, code, allow_bind=bool(user_input.get("bind")) and not existing,
                )
                if outcome in ("logged_in", "bound"):
                    self._selected_aiot_credentials = (nid, code)
                    return self._create_ble_entry()
                if outcome == "already_bound":
                    return self.async_abort(
                        reason="aiot_already_bound",
                        description_placeholders={"name": self._selected_ble_name or ""},
                    )
                errors["base"] = {
                    "refused": "aiot_invalid_credentials" if existing else "aiot_bind_required",
                }.get(outcome, "probe_failed")
        return self.async_show_form(
            step_id="aiot_bind",
            data_schema=vol.Schema({
                vol.Optional("bind", default=False): bool,
                vol.Optional(CONF_AIOT_NID, default=""): str,
                vol.Optional(CONF_AIOT_CODE, default=""): str,
            }),
            errors=errors,
            description_placeholders={"name": self._selected_ble_name or ""},
        )

    async def _aiot_login_probe(self, nid: str, code: str, allow_bind: bool) -> str:
        """Connect once and log in, binding first if allowed and needed.

        Returns "logged_in", "bound", "refused" (unbound and not allowed to
        bind, or wrong credentials), "already_bound" (bind refused: the unit
        belongs to other credentials) or "no_reply".
        """
        address = self._selected_ble_address
        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return "no_reply"
        proto = AromaIotProtocol(nid=nid, code=code)
        results: dict[str, asyncio.Queue] = {"login": asyncio.Queue(), "bind": asyncio.Queue()}

        def _on_notify(_sender, data: bytearray) -> None:
            updates = proto.parse_notification(bytes(data))
            if "login_result" in updates:
                results["login"].put_nowait(updates["login_result"])
            if "bind_result" in updates:
                results["bind"].put_nowait(updates["bind_result"])

        async def _ask(frame: bytes, kind: str) -> int | None:
            await client.write_gatt_char(SCENT_TECH_CHAR_UUID, frame, response=False)
            try:
                return await asyncio.wait_for(results[kind].get(), 8)
            except asyncio.TimeoutError:
                return None

        client = None
        try:
            client = await establish_connection(
                BleakClient, ble_device, self._selected_ble_name or address,
                max_attempts=BLE_CONNECT_MAX_ATTEMPTS,
            )
            await client.start_notify(SCENT_TECH_CHAR_UUID, _on_notify)
            login = await _ask(proto.build_login(), "login")
            if login is None:
                return "no_reply"
            if login == 0:
                return "logged_in"
            if not allow_bind:
                return "refused"
            bind = await _ask(proto.build_bind(), "bind")
            if bind is None:
                return "no_reply"
            if bind != 0:
                _LOGGER.warning("Aroma IOT %s refused binding (code %d)", address, bind)
                return "already_bound"
            login = await _ask(proto.build_login(), "login")
            return "bound" if login == 0 else "no_reply"
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("Aroma IOT login probe of %s failed: %s", address, err)
            return "no_reply"
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as err:
                    _LOGGER.debug("Aroma IOT probe disconnect from %s: %s", address, err)

    def _needs_password_step(self) -> bool:
        """GW and Scent Tech devices may be password-protected; we can't
        tell before connecting, so offer the optional password prompt."""
        return self._selected_device_type in (
            DeviceType.SCENT_MARKETING_GW,
            DeviceType.SCENT_MARKETING_GW_XOR,
            DeviceType.SCENT_TECH,
        )

    # ------------------------------------------------------------------
    # Bluetooth discovery (manifest "bluetooth" matchers)
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        """Handle a diffuser found by HA's Bluetooth integration.

        The manifest matchers are deliberately broad (e.g. bare
        manufacturer IDs), so run the same detection as the manual scan
        and drop anything it doesn't recognise rather than offering it as
        Aroma-Link.
        """
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        adv = discovery_info.advertisement
        name = _advertised_name(discovery_info)
        dtype = detect_device_type(name, adv)
        if dtype is None:
            return self.async_abort(reason="not_supported")
        if not name:
            name = f"Scent Marketing {discovery_info.address[-8:]}"

        self._selected_ble_address = discovery_info.address
        self._selected_ble_name = name
        self._selected_device_type = dtype
        self._selected_sm_metadata = (
            extract_scent_marketing_metadata(adv)
            if dtype.value.startswith("scent_marketing") else None
        )
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask the user to confirm a discovered diffuser."""
        if user_input is not None:
            return await self._async_step_credentials()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._selected_ble_name or ""},
        )

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
        # Selection errors are shown on the re-scanned list; scan errors
        # replace it.
        select_errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input.get("ble_address")
            if address:
                # User selected a device – proceed to create entry
                device_info = self._discovered_devices.get(address, {})
                self._selected_ble_address = address
                self._selected_ble_name = device_info.get("name", "")
                self._selected_device_type = device_info.get("device_type")
                self._selected_sm_metadata = device_info.get("sm_metadata")

                # Check if already configured. A pending discovery for the
                # same device must not block setting it up by hand.
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                if self._selected_device_type is None:
                    # The advertisement didn't say what this is. It used
                    # to become Aroma-Link unseen and then fail with
                    # "fff1 not found" (#29, #30, #48, #50); connect once
                    # and decide from the GATT table instead.
                    dtype, gatt = await self._probe_gatt(address)
                    if gatt is None:
                        select_errors["base"] = "probe_failed"
                    elif dtype is None:
                        return self.async_abort(
                            reason="unsupported_gatt",
                            description_placeholders={
                                "name": self._selected_ble_name or address,
                                "gatt": gatt,
                                "issues_url": ISSUES_URL,
                            },
                        )
                    else:
                        self._selected_device_type = dtype

                if not select_errors:
                    return await self._async_step_credentials()
            # If address is missing, user clicked Submit on an empty error
            # form – fall through to re-scan.

        # List what HA's bluetooth integration has seen. A BleakScanner of
        # our own would only reach the local adapter — never an ESPHome
        # proxy (#45) — and competes with HA for it. Only connectable
        # sources: a passive-only proxy can't run our protocol anyway.
        self._discovered_devices = {}
        try:
            for info in async_discovered_service_info(self.hass, connectable=True):
                adv_data = info.advertisement
                name = _advertised_name(info)
                dtype = detect_device_type(name, adv_data)

                # A Scent Marketing device may advertise without a useful
                # local name (the app routes purely on manufacturer-data),
                # so we accept it even when `name` is empty.
                if not name and dtype is None:
                    continue
                if not name:
                    name = f"Scent Marketing {info.address[-8:]}"

                sm_meta = extract_scent_marketing_metadata(adv_data) if dtype and dtype.value.startswith("scent_marketing") else None
                if sm_meta:
                    _LOGGER.info(
                        "Discovered Scent Marketing device: addr=%s name=%s family=%s mfr_id=0x%04X pid=%s flag=%s raw=%s",
                        info.address, name, dtype.value,
                        sm_meta["mfr_id"] or 0, sm_meta["pid"], sm_meta["wifi_flag"],
                        sm_meta["raw_hex"],
                    )

                self._discovered_devices[info.address] = {
                    "name": name,
                    # None: identified from the GATT table once selected.
                    "device_type": dtype,
                    "rssi": info.rssi,
                    "auto_detected": dtype is not None,
                    "sm_metadata": sm_meta,
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
            errors=select_errors,
        )

    async def _probe_gatt(self, address: str) -> tuple[DeviceType | None, str | None]:
        """Connect once and classify the device by its GATT table.

        Returns (family, table as text); the table is None when the
        connection failed, the family None when the layout is unknown.
        """
        ble_device = async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return None, None
        client = None
        try:
            client = await establish_connection(
                BleakClient,
                ble_device,
                self._selected_ble_name or address,
                max_attempts=BLE_CONNECT_MAX_ATTEMPTS,
            )
            services = client.services
            table = {
                char.uuid.lower(): set(char.properties)
                for service in services
                for char in service.characteristics
            }
            dtype = classify_gatt(table)
            gatt = _format_gatt(services)
            _LOGGER.info(
                "GATT probe of %s (%s): %s\n%s",
                self._selected_ble_name, address,
                dtype.value if dtype else "no supported layout", gatt,
            )
            return dtype, gatt
        except (BleakError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("GATT probe of %s failed: %s", address, err)
            return None, None
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as err:
                    _LOGGER.debug("GATT probe disconnect from %s: %s", address, err)

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
