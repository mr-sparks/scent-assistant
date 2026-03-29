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
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        headers = {"User-Agent": _USER_AGENT}
        if self._access_token:
            headers["token"] = self._access_token
        return headers

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self, username: str, password: str) -> bool:
        """Authenticate with the Aroma-Link cloud.

        Password is MD5-hashed before transmission (as the official app does).
        The token endpoint returns both the access token and user ID.
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
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Cloud login failed: HTTP %s", resp.status)
                    return False
                data = await resp.json(content_type=None)

                if data.get("code") != 200:
                    _LOGGER.error("Cloud login error: %s", data.get("msg", "Unknown"))
                    return False

                inner = data.get("data", {})
                self._access_token = inner.get("accessToken")
                self._user_id = str(inner.get("id") or inner.get("userId") or "")

        except Exception as err:
            _LOGGER.error("Cloud login error: %s", err)
            return False

        if not self._access_token or not self._user_id:
            _LOGGER.error("Cloud auth incomplete: token=%s user_id=%s",
                          bool(self._access_token), bool(self._user_id))
            return False

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
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("Cloud device list failed: HTTP %s", resp.status)
                    return []
                data = await resp.json(content_type=None)
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
            ) as resp:
                success = resp.status == 200
                if success:
                    _LOGGER.debug("Cloud power %s for %s", "on" if on else "off", device_id)
                else:
                    _LOGGER.error("Cloud power command failed: HTTP %s", resp.status)
                return success
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
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
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
    ) -> bool:
        """Set diffuser schedule via cloud API."""
        if not self.authenticated:
            return False

        if weekdays is None:
            weekdays = [0, 1, 2, 3, 4, 5, 6]

        session = await self._ensure_session()
        payload = {
            "deviceId": device_id,
            "type": "workTime",
            "week": weekdays,
            "workTimeList": [
                {
                    "startTime": start_time,
                    "endTime": end_time,
                    "enabled": 1,
                    "consistenceLevel": "1",
                    "workDuration": str(work_seconds),
                    "pauseDuration": str(pause_seconds),
                },
                *[
                    {
                        "startTime": "00:00",
                        "endTime": "24:00",
                        "enabled": 0,
                        "consistenceLevel": "1",
                        "workDuration": "10",
                        "pauseDuration": "120",
                    }
                    for _ in range(4)
                ],
            ],
        }

        try:
            async with session.post(
                f"{CLOUD_WEB_URL}{CLOUD_ENDPOINT_SCHEDULE}",
                headers={
                    **self._auth_headers(),
                    "Content-Type": "application/json;charset=UTF-8",
                },
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,  # aroma-link.com has certificate issues
            ) as resp:
                success = resp.status == 200
                if success:
                    _LOGGER.debug("Cloud schedule set for %s", device_id)
                else:
                    _LOGGER.error("Cloud schedule failed: HTTP %s", resp.status)
                return success
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

    @staticmethod
    def _extract_token(data: dict) -> str | None:
        """Find access token in nested API response."""
        if not isinstance(data, dict):
            return None
        for key in ("accessToken", "accesstoken", "access_token", "token"):
            if key in data:
                return str(data[key])
            if "data" in data and isinstance(data["data"], dict) and key in data["data"]:
                return str(data["data"][key])
        return None

    @staticmethod
    def _extract_user_id(data: dict) -> str | None:
        """Find user ID in nested API response."""
        if not isinstance(data, dict):
            return None
        for key in ("userId", "userid", "user_id", "uid"):
            if key in data:
                return str(data[key])
            if "data" in data and isinstance(data["data"], dict) and key in data["data"]:
                return str(data["data"][key])
        return None

    def _parse_device_list(self, data: dict) -> list[CloudDevice]:
        """Parse device list API response.

        The API returns a nested structure: groups contain children (devices).
        """
        devices: list[CloudDevice] = []
        if not isinstance(data, dict):
            return devices

        raw_list = data.get("data", data.get("rows", []))
        if not isinstance(raw_list, list):
            return devices

        # Flatten: collect devices from top level and from children of groups
        all_items: list[dict] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "device":
                all_items.append(item)
            # Also check children (devices nested in groups)
            children = item.get("children", [])
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict) and child.get("type") == "device":
                        all_items.append(child)

        for item in all_items:
            device_id = item.get("deviceId") or item.get("id")
            name = item.get("text") or item.get("deviceName") or item.get("name") or f"Device {device_id}"
            if device_id:
                devices.append(CloudDevice(
                    device_id=str(device_id),
                    name=str(name),
                    user_id=self._user_id or "",
                    online=item.get("onlineStatus") == 1,
                ))

        return devices

    @staticmethod
    def _parse_status(data: dict) -> dict | None:
        """Parse device status into a simple dict."""
        if not isinstance(data, dict):
            return None

        # Navigate nested response
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
