"""Cloud API client for Aroma-Link diffusers (WiFi fallback).

Uses the aroma-link.com REST API when BLE is not available.
Only supports Aroma-Link devices (ShinePick has no WiFi).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import aiohttp

from .const import (
    CLOUD_BASE_URL,
    CLOUD_ENDPOINT_TOKEN,
    CLOUD_ENDPOINT_DEVICES,
    CLOUD_ENDPOINT_SWITCH,
    CLOUD_ENDPOINT_STATUS,
    CLOUD_ENDPOINT_SCHEDULE,
    CLOUD_WEB_URL,
)

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 11) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)


@dataclass
class CloudDevice:
    """A device discovered via the cloud API."""

    device_id: str
    name: str
    user_id: str
    online: bool = False


class AromaLinkCloudClient:
    """REST API client for the aroma-link.com cloud service."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None
        self._access_token: str | None = None
        self._user_id: str | None = None

    @property
    def authenticated(self) -> bool:
        return self._access_token is not None and self._user_id is not None

    @property
    def user_id(self) -> str | None:
        return self._user_id

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            cookie_jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=cookie_jar)
            self._owns_session = True
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        """Build auth headers for Aroma-Link cloud requests."""
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        if self._access_token:
            headers["access_token"] = self._access_token
            headers["Authorization"] = self._access_token
        return headers

    @staticmethod
    def _response_ok(data: dict | list | None) -> bool:
        """Determine whether an Aroma-Link API response indicates success."""
        if isinstance(data, dict):
            code = data.get("code")
            success = data.get("success")
            msg = str(data.get("msg", "")).lower()

            if code in (200, "200", 0, "0"):
                return True
            if success is True:
                return True
            if msg in ("success", "ok", "operate success", "operation success"):
                return True

        return False

    async def _web_login(self, username: str, password: str) -> bool:
        """Log into the Aroma-Link web app to obtain session cookies."""
        session = await self._ensure_session()

        attempts = [
            {"username": username, "password": password},
            {"username": username, "password": hashlib.md5(password.encode("utf-8")).hexdigest()},
        ]

        for form_data in attempts:
            try:
                async with session.post(
                    f"{CLOUD_WEB_URL}/login",
                    headers={
                        "User-Agent": _USER_AGENT,
                        "Accept": "application/json, text/plain, */*",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{CLOUD_WEB_URL}/",
                        "Origin": CLOUD_WEB_URL,
                    },
                    data=form_data,
                    timeout=aiohttp.ClientTimeout(total=15),
                    ssl=False,
                ) as resp:
                    raw_text = await resp.text()
                    _LOGGER.debug("Cloud web login response [%s]: %s", resp.status, raw_text)

                    if resp.status != 200:
                        continue

                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        continue

                    if isinstance(data, dict) and data.get("code") in (0, "0", 200, "200"):
                        _LOGGER.debug("Cloud web login successful")
                        return True

            except Exception as err:
                _LOGGER.debug("Cloud web login attempt failed: %s", err)

        _LOGGER.warning("Cloud web login did not confirm success")
        return False

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self, username: str, password: str) -> bool:
        """Authenticate with the Aroma-Link cloud.

        Password is MD5-hashed before transmission to the app token endpoint.
        """
        session = await self._ensure_session()
        hashed_pw = hashlib.md5(password.encode("utf-8")).hexdigest()

        form = aiohttp.FormData()
        form.add_field("userName", username)
        form.add_field("password", hashed_pw)

        try:
            async with session.post(
                f"{CLOUD_BASE_URL}{CLOUD_ENDPOINT_TOKEN}",
                headers={"User-Agent": _USER_AGENT},
                data=form,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Cloud login failed: HTTP %s", resp.status)
                    return False

                data = await resp.json(content_type=None)
                _LOGGER.debug("Cloud login response: %s", data)

                if not self._response_ok(data):
                    _LOGGER.error("Cloud login error: %s", data)
                    return False

                inner = data.get("data", {}) if isinstance(data, dict) else {}
                self._access_token = (
                    inner.get("accessToken")
                    or inner.get("access_token")
                    or inner.get("token")
                )
                self._user_id = str(inner.get("id") or inner.get("userId") or "")

        except Exception as err:
            _LOGGER.error("Cloud login error: %s", err)
            return False

        if not self._access_token or not self._user_id:
            _LOGGER.error(
                "Cloud auth incomplete: token=%s user_id=%s",
                bool(self._access_token),
                bool(self._user_id),
            )
            return False

        await self._web_login(username, password)

        _LOGGER.info("Cloud login successful for user %s", self._user_id)
        return True

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[CloudDevice]:
        """Fetch all devices linked to the authenticated account."""
        if not self.authenticated:
            return []

        session = await self._ensure_session()
        url = f"{CLOUD_BASE_URL}{CLOUD_ENDPOINT_DEVICES.format(user_id=self._user_id)}"

        try:
            async with session.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Cloud device list failed: HTTP %s", resp.status)
                    return []

                data = await resp.json(content_type=None)
                _LOGGER.debug("Cloud device list response: %s", data)
                return self._parse_device_list(data)
        except Exception as err:
            _LOGGER.error("Cloud device list error: %s", err)
            return []

    # ------------------------------------------------------------------
    # Device control
    # ------------------------------------------------------------------

    async def set_power(self, device_id: str, on: bool) -> bool:
        """Turn device on or off via cloud API."""
        if not self.authenticated:
            return False

        session = await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("deviceId", device_id)
        form.add_field("userId", self._user_id)
        form.add_field("onOff", "1" if on else "0")

        try:
            async with session.post(
                f"{CLOUD_BASE_URL}{CLOUD_ENDPOINT_SWITCH}",
                headers=self._auth_headers(),
                data=form,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
            ) as resp:
                raw_text = await resp.text()
                _LOGGER.debug("Cloud power response [%s]: %s", resp.status, raw_text)

                if resp.status != 200:
                    _LOGGER.error("Cloud power command failed: HTTP %s", resp.status)
                    return False

                return True
        except Exception as err:
            _LOGGER.error("Cloud power error: %s", err)
            return False

    async def get_status(self, device_id: str) -> dict | None:
        """Fetch current device status from cloud."""
        if not self.authenticated:
            return None

        session = await self._ensure_session()
        url = (
            f"{CLOUD_BASE_URL}"
            f"{CLOUD_ENDPOINT_STATUS.format(device_id=device_id)}"
            f"?isOpenPage=0&userId={self._user_id}"
        )

        try:
            async with session.get(
                url,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Cloud status failed: HTTP %s", resp.status)
                    return None

                data = await resp.json(content_type=None)
                _LOGGER.debug("Cloud status response: %s", data)
                return self._parse_status(data)
        except Exception as err:
            _LOGGER.error("Cloud status error: %s", err)
            return None

    async def set_schedule(
        self,
        device_id: str,
        work_seconds: int,
        pause_seconds: int,
        weekdays: list[int] | None = None,
        start_time: str = "00:00",
        end_time: str = "23:59",
        enabled: bool = True,
    ) -> bool:
        """Set diffuser schedule via cloud API.

        Cloud API expects weekday list values like:
        1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat, 7=Sun
        """
        if not self.authenticated:
            return False

        if weekdays is None:
            weekdays = [1, 2, 3, 4, 5, 6, 7]

        session = await self._ensure_session()

        payload = {
            "deviceId": int(device_id),
            "userId": int(self._user_id),
            "week": weekdays,
            "workTimeList": [
                {
                    "consistenceLevel": 1,
                    "enabled": 1 if enabled else 0,
                    "endTime": end_time,
                    "manyPumpEnabled": 1 if enabled else 0,
                    "pauseDuration": int(pause_seconds),
                    "selectPump": "#4#",
                    "startTime": start_time,
                    "workDuration": int(work_seconds),
                },
                {
                    "consistenceLevel": 1,
                    "enabled": 0,
                    "endTime": "24:00",
                    "manyPumpEnabled": 0,
                    "pauseDuration": 900,
                    "selectPump": "#4#",
                    "startTime": "00:00",
                    "workDuration": 10,
                },
                {
                    "consistenceLevel": 1,
                    "enabled": 0,
                    "endTime": "24:00",
                    "manyPumpEnabled": 0,
                    "pauseDuration": 900,
                    "selectPump": "#4#",
                    "startTime": "00:00",
                    "workDuration": 10,
                },
                {
                    "consistenceLevel": 1,
                    "enabled": 0,
                    "endTime": "24:00",
                    "manyPumpEnabled": 0,
                    "pauseDuration": 900,
                    "selectPump": "#4#",
                    "startTime": "00:00",
                    "workDuration": 10,
                },
                {
                    "consistenceLevel": 1,
                    "enabled": 0,
                    "endTime": "24:00",
                    "manyPumpEnabled": 0,
                    "pauseDuration": 900,
                    "selectPump": "#4#",
                    "startTime": "00:00",
                    "workDuration": 10,
                },
            ],
        }

        try:
            async with session.post(
                f"{CLOUD_BASE_URL}{CLOUD_ENDPOINT_SCHEDULE}",
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/json;charset=UTF-8",
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
                ssl=False,
            ) as resp:
                response_text = await resp.text()
                _LOGGER.warning("Cloud schedule payload: %s", payload)
                _LOGGER.warning("Cloud schedule response [%s]: %s", resp.status, response_text)

                if resp.status != 200:
                    _LOGGER.error("Cloud schedule failed: HTTP %s", resp.status)
                    return False

                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    _LOGGER.error(
                        "Cloud schedule returned non-JSON response; response_text=%s",
                        response_text,
                    )
                    return False

                if self._response_ok(data):
                    _LOGGER.warning("Cloud schedule set for %s", device_id)
                    return True

                _LOGGER.error("Cloud schedule API indicated failure: %s", data)
                return False

        except Exception as err:
            _LOGGER.error("Cloud schedule error: %s", err)
            return False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP session if we own it."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Response parsing helpers
    # ------------------------------------------------------------------

    def _parse_device_list(self, data: dict) -> list[CloudDevice]:
        """Parse device list API response."""
        devices: list[CloudDevice] = []
        if not isinstance(data, dict):
            return devices

        raw_list = data.get("data", data.get("rows", []))
        if not isinstance(raw_list, list):
            return devices

        all_items: list[dict] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "device":
                all_items.append(item)

            children = item.get("children", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict) and child.get("type") == "device":
                        all_items.append(child)

        for item in all_items:
            device_id = item.get("deviceId") or item.get("id")
            name = (
                item.get("text")
                or item.get("deviceName")
                or item.get("name")
                or f"Device {device_id}"
            )
            if device_id:
                devices.append(
                    CloudDevice(
                        device_id=str(device_id),
                        name=str(name),
                        user_id=self._user_id or "",
                        online=item.get("onlineStatus") == 1,
                    )
                )

        return devices

    @staticmethod
    def _parse_status(data: dict) -> dict | None:
        """Parse device status into a simple dict."""
        if not isinstance(data, dict):
            return None

        info = data.get("data", data)
        if not isinstance(info, dict):
            return None

        on_off = info.get("onOff")
        work_status = info.get("workStatus")

        power = None
        if on_off is not None:
            power = int(on_off) == 1
        elif work_status is not None:
            power = int(work_status) != 0

        phase = "off"
        if power:
            phase = "spraying" if work_status == 1 else "paused" if work_status == 2 else "idle"

        return {
            "power": power,
            "phase": phase,
            "work_remain": info.get("workRemainTime"),
            "pause_remain": info.get("pauseRemainTime"),
        }