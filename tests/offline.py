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
    async_write_ha_state = forbidden_io


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
        ("number", "NumberEntity"), ("select", "SelectEntity"),
        ("switch", "SwitchEntity"), ("time", "TimeEntity"),
    ):
        module(f"homeassistant.components.{name}", **{entity_name: Entity})
    stubs["homeassistant.components.number"].NumberMode = SimpleNamespace(
        BOX="box", SLIDER="slider"
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module("homeassistant.const", EntityCategory=SimpleNamespace(CONFIG="config"))
    module("homeassistant.core", HomeAssistant=object, CoreState=object)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    module("homeassistant.helpers")
    module("homeassistant.helpers.device_registry")
    module("homeassistant.helpers.entity_registry")
    module("homeassistant.helpers.entity", Entity=Entity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)

    # Avoid integration __init__.py: it registers real HA services and is outside
    # this test's contract. Relative imports still resolve the real source files.
    package = "_scent_assistant_offline_tests"
    source = Path(__file__).resolve().parents[1] / "custom_components" / "scent_assistant"
    root = ModuleType(package)
    root.__path__ = [str(source)]
    stubs[package] = root
    names = ("const", "protocol_ble", "device", "timer_entity",
             "number", "select", "switch", "time")
    with patch.dict(sys.modules, stubs):
        return SimpleNamespace(**{
            name: import_module(f"{package}.{name}") for name in names
        })


MODULES = load_modules()
