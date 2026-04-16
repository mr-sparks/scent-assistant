"""Constants for the Scent Diffuser integration."""
from enum import StrEnum

DOMAIN = "scent_assistant"

# ---------------------------------------------------------------------------
# Device types
# ---------------------------------------------------------------------------

class DeviceType(StrEnum):
    TUYA_BLE = "tuya_ble"          # ShinePick QT-I300 and similar
    AROMA_LINK = "aroma_link"      # Aroma-Link WiFi+BLE diffusers


# ---------------------------------------------------------------------------
# BLE UUIDs
# ---------------------------------------------------------------------------

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_INDICATE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"  # Aroma-Link only

# ---------------------------------------------------------------------------
# BLE name patterns for device detection
# ---------------------------------------------------------------------------

BLE_NAME_PATTERNS = {
    DeviceType.AROMA_LINK: ["Scent "],
    DeviceType.TUYA_BLE: ["BT-ivy"],
}

# ---------------------------------------------------------------------------
# Tuya BLE protocol constants
# ---------------------------------------------------------------------------

TUYA_HEADER = bytes([0x55, 0xAA])
TUYA_VERSION = 0x00

TUYA_CMD_DP_WRITE = 0x06
TUYA_CMD_DP_REPORT = 0x07
TUYA_CMD_QUERY = 0x08
TUYA_CMD_TIME_SYNC = 0x1C

TUYA_DP_TYPE_RAW = 0x00
TUYA_DP_TYPE_BOOL = 0x01
TUYA_DP_TYPE_VALUE = 0x02
TUYA_DP_TYPE_STRING = 0x03
TUYA_DP_TYPE_ENUM = 0x04

TUYA_DP_POWER = 1
TUYA_DP_SCHEDULE = 18

# ---------------------------------------------------------------------------
# Aroma-Link BLE protocol constants
# ---------------------------------------------------------------------------

AL_HEADER = bytes([0xA5, 0xAA, 0xAC])
AL_TRAILER = bytes([0xC5, 0xCC, 0xCA])

AL_CMD_QUERY = 0x52
AL_CMD_STATUS = 0x53
AL_CMD_WRITE = 0x57

AL_SUB_POWER = 0x08
AL_SUB_FAN = 0x03
AL_SUB_SCHEDULE = 0x16
AL_SUB_TIME_SYNC = 0x17
AL_SUB_DEVICE_NAME = 0x01
AL_SUB_DEVICE_INFO = 0x0D
AL_SUB_QUERY_SCHEDULES = 0x15

AL_FAN_ON_VALUE = 0x10
AL_FAN_OFF_VALUE = 0x00

AL_SLOT_ENABLED = 0x11
AL_SLOT_DISABLED = 0x10

# Status report phases
AL_PHASE_IDLE = 0x00
AL_PHASE_SPRAYING = 0x01
AL_PHASE_PAUSED = 0x02

# ---------------------------------------------------------------------------
# Aroma-Link Cloud API constants
# ---------------------------------------------------------------------------

CLOUD_BASE_URL = "https://www.aroma-link.com"
CLOUD_WEB_URL = "https://www.aroma-link.com"

CLOUD_ENDPOINT_TOKEN = "/v2/app/token"
CLOUD_ENDPOINT_DEVICES = "/v1/app/device/listAll/{user_id}"
CLOUD_ENDPOINT_SWITCH = "/v1/app/data/newSwitch"
CLOUD_ENDPOINT_STATUS = "/v1/app/device/newWork/{device_id}"
CLOUD_ENDPOINT_SCHEDULE = "/v1/app/data/workSetApp"

# ---------------------------------------------------------------------------
# Weekday bitmask (shared by both protocols)
# ---------------------------------------------------------------------------

WEEKDAY_MON = 0x01
WEEKDAY_TUE = 0x02
WEEKDAY_WED = 0x04
WEEKDAY_THU = 0x08
WEEKDAY_FRI = 0x10
WEEKDAY_SAT = 0x20
WEEKDAY_SUN = 0x40
WEEKDAY_ALL = 0x7F

# ---------------------------------------------------------------------------
# Config keys
# ---------------------------------------------------------------------------

CONF_DEVICE_TYPE = "device_type"
CONF_BLE_ADDRESS = "ble_address"
CONF_BLE_NAME = "ble_name"
CONF_CLOUD_USERNAME = "cloud_username"
CONF_CLOUD_PASSWORD = "cloud_password"
CONF_CLOUD_DEVICE_ID = "cloud_device_id"
CONF_CLOUD_USER_ID = "cloud_user_id"
CONF_CONNECTION_MODE = "connection_mode"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_WORK_DURATION = 10    # seconds
DEFAULT_PAUSE_DURATION = 120  # seconds
DEFAULT_SCAN_TIMEOUT = 10.0   # BLE scan seconds
DEFAULT_CONNECT_TIMEOUT = 15  # BLE connect seconds
DEFAULT_RECONNECT_DELAY = 30  # seconds between reconnect attempts
