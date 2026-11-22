"""Load real integration modules with explicit, inert dependency stubs.

No Home Assistant service setup is imported. The full production modules are
loaded under a private package name, without AST extraction or copied methods.
Only external HA/BLE/HTTP dependencies are stubbed, within a scoped sys.modules
patch; the returned module objects retain those stubs. Transport construction
fails closed. These are offline unit tests, not HA runtime or hardware tests.
"""
from importlib import import_module
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class HomeAssistantError(Exception):
    """Minimal stand-in for the HA action exception (no HA installed)."""


class BleakError(Exception):
    """Minimal stand-in for the BLE transport exception."""


def forbidden_io(*args, **kwargs):
    raise AssertionError("Real Bluetooth, HTTP and network I/O is forbidden")


class Entity:
    """Only the base behavior needed by the timer entities."""

    hass = None
    _attr_unique_id = None
    _attr_device_info = None

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def device_info(self):
        return self._attr_device_info

    def async_write_ha_state(self):
        pass

    async def async_added_to_hass(self):
        pass

    async def async_will_remove_from_hass(self):
        pass


def load_modules():
    """Import complete production files without starting the integration."""
    stubs = {}

    def module(name, **attributes):
        stub = ModuleType(name)
        stub.__path__ = []
        stub.__dict__.update(attributes)
        stubs[name] = stub
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(stubs[parent], child, stub)
        return stub

    module("bleak", BleakClient=forbidden_io, BleakScanner=forbidden_io,
           BleakError=BleakError)
    module("bleak_retry_connector", establish_connection=forbidden_io)
    module("aiohttp", ClientSession=forbidden_io)
    module("homeassistant")
    module("homeassistant.components")
    module("homeassistant.components.bluetooth")
    for name, entity_name in (
        ("binary_sensor", "BinarySensorEntity"),
        ("button", "ButtonEntity"),
        ("number", "NumberEntity"),
        ("select", "SelectEntity"),
        ("sensor", "SensorEntity"),
        ("switch", "SwitchEntity"),
        ("time", "TimeEntity"),
    ):
        module(f"homeassistant.components.{name}", **{entity_name: Entity})
    stubs["homeassistant.components.number"].NumberMode = SimpleNamespace(
        BOX="box", SLIDER="slider"
    )
    stubs["homeassistant.components.number"].NumberDeviceClass = SimpleNamespace(
        VOLUME="volume"
    )
    stubs["homeassistant.components.sensor"].SensorDeviceClass = SimpleNamespace(
        VOLUME="volume", DURATION="duration", BATTERY="battery", ENUM="enum"
    )
    stubs["homeassistant.components.sensor"].SensorStateClass = SimpleNamespace(
        MEASUREMENT="measurement", TOTAL="total"
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EntityCategory=SimpleNamespace(CONFIG="config", DIAGNOSTIC="diagnostic"),
        UnitOfVolume=SimpleNamespace(MILLILITERS="mL", LITERS="L"),
        UnitOfTime=SimpleNamespace(SECONDS="s", MINUTES="min", HOURS="h"),
        PERCENTAGE="%",
    )
    module("homeassistant.core", HomeAssistant=object, CoreState=object)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    module("homeassistant.helpers")
    module("homeassistant.helpers.device_registry")
    module("homeassistant.helpers.entity_registry")
    module("homeassistant.helpers.entity", Entity=Entity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    class MockStore:
        def __init__(self, hass, version, key):
            self.hass = hass
            self.version = version
            self.key = key
            self.data = None

        async def async_load(self):
            return self.data

        async def async_save(self, data):
            self.data = data

    module("homeassistant.helpers.storage", Store=MockStore)
    module("homeassistant.util")
    from datetime import datetime, timezone
    module("homeassistant.util.dt", now=lambda: datetime.now(timezone.utc))

    # Avoid integration __init__.py: it registers real HA services and is outside
    # this test's contract. Relative imports still resolve the real source files.
    package = "_scent_assistant_offline_tests"
    source = Path(__file__).resolve().parents[1] / "custom_components" / "scent_assistant"
    root = ModuleType(package)
    root.__path__ = [str(source)]
    stubs[package] = root
    names = (
        "const", "protocol_ble", "device", "timer_entity",
        "number", "select", "switch", "time",
        "estimated_oil", "binary_sensor", "button", "sensor",
    )
    with patch.dict(sys.modules, stubs):
        return SimpleNamespace(**{
            name: import_module(f"{package}.{name}") for name in names
        })


MODULES = load_modules()
