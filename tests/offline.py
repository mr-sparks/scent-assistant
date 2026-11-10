"""Load real integration modules with explicit, inert dependency stubs.

No Home Assistant service setup is imported. The full production modules are
loaded under a private package name, without AST extraction or copied methods.
Only external HA/BLE/HTTP dependencies are stubbed, within a scoped sys.modules
patch; the returned module objects retain those stubs. Transport construction
fails closed. These are offline unit tests, not HA runtime or hardware tests.
"""
from importlib import import_module, util
from importlib.machinery import SourceFileLoader
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
    """Only the base behavior needed by the tested entities."""

    hass = None
    async_write_ha_state = forbidden_io


class Flow:
    """Return form/entry descriptions; no HA flow engine or persistence."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


class Marker:
    def __init__(self, schema, default=None):
        self.schema, self.default = schema, default


class Schema:
    def __init__(self, schema):
        self.schema = schema


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
    module("voluptuous", Schema=Schema, Required=Marker, Optional=Marker,
           All=lambda *a: a, In=lambda a: a, Coerce=lambda a: a,
           Range=lambda **k: k)
    module("homeassistant")
    module("homeassistant.components")
    module("homeassistant.components.bluetooth", BluetoothServiceInfoBleak=object,
           async_ble_device_from_address=forbidden_io,
           async_discovered_service_info=forbidden_io)
    for name, entity_name in (
        ("number", "NumberEntity"), ("select", "SelectEntity"),
        ("switch", "SwitchEntity"), ("time", "TimeEntity"),
    ):
        module(f"homeassistant.components.{name}", **{entity_name: Entity})
    stubs["homeassistant.components.number"].NumberMode = SimpleNamespace(
        BOX="box", SLIDER="slider"
    )
    module("homeassistant.components.sensor", SensorEntity=Entity,
           SensorDeviceClass=SimpleNamespace(BATTERY="battery", ENUM="enum", DURATION="duration"),
           SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"))
    module("homeassistant.components.diagnostics", async_redact_data=lambda data, keys: data)
    module("homeassistant.config_entries", ConfigEntry=object,
           ConfigFlow=Flow, OptionsFlow=Flow)
    module("homeassistant.const", PERCENTAGE="%", UnitOfTime=SimpleNamespace(SECONDS="s"),
           EntityCategory=SimpleNamespace(CONFIG="config", DIAGNOSTIC="diagnostic"))
    module("homeassistant.core", HomeAssistant=object,
           CoreState=SimpleNamespace(running="running"), ServiceCall=object,
           callback=lambda f: f)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    module("homeassistant.helpers")
    module("homeassistant.helpers.device_registry")
    module("homeassistant.helpers.entity_registry")
    module("homeassistant.helpers.entity", Entity=Entity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=forbidden_io)
    module("homeassistant.helpers.event", async_track_time_interval=forbidden_io)
    module("homeassistant.helpers.config_validation", ensure_list=lambda a: a,
           time=object(), boolean=bool, string=str)

    # Keep a private package so installed HA integrations cannot be imported.
    package = "_scent_assistant_offline_tests"
    source = Path(__file__).resolve().parents[1] / "custom_components" / "scent_assistant"
    root = ModuleType(package)
    root.__path__ = [str(source)]
    stubs[package] = root
    names = ("const", "protocol_ble", "device", "timer_entity",
             "number", "select", "switch", "time", "sensor", "config_flow", "diagnostics")
    with patch.dict(sys.modules, stubs):
        modules = {
            name: import_module(f"{package}.{name}") for name in names
        }
        # Load the complete lifecycle module too. Defining schemas does not
        # register services; tests explicitly supply an inert HA/entry boundary.
        name = f"{package}.integration"
        spec = util.spec_from_loader(
            name, SourceFileLoader(name, str(source / "__init__.py")), is_package=False
        )
        assert spec is not None and spec.loader is not None
        integration = util.module_from_spec(spec)
        sys.modules[spec.name] = integration
        spec.loader.exec_module(integration)
        return SimpleNamespace(**modules, integration=integration)


MODULES = load_modules()
