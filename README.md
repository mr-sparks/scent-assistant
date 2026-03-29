<p align="center">
  <img src="images/header.jpg" alt="Scent Assistant" width="100%">
</p>

<h1 align="center">Scent Assistant</h1>

<p align="center">
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant"></a>
  <a href="https://github.com/mr-sparks/scent-assistant/releases"><img src="https://img.shields.io/github/v/release/mr-sparks/scent-assistant?style=for-the-badge" alt="Release"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/mr-sparks/scent-assistant/stargazers"><img src="https://img.shields.io/github/stars/mr-sparks/scent-assistant?style=for-the-badge" alt="Stars"></a>
  <a href="https://github.com/mr-sparks/scent-assistant/releases"><img src="https://img.shields.io/github/downloads/mr-sparks/scent-assistant/total?style=for-the-badge" alt="Downloads"></a>
</p>

<p align="center">
  <a href="https://ko-fi.com/K3K21WUCCD"><img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Ko-fi"></a>
</p>

<p align="center">
  A <a href="https://www.home-assistant.io/">Home Assistant</a> custom integration for controlling scent and aroma diffusers via Bluetooth or WiFi.<br>
  Supports devices using the <b>Aroma-Link</b> and <b>Aroma Buddy</b> apps.
</p>

---

## &#x2728; Features

- **Bluetooth (BLE)** - Fully local control, no cloud account needed, all features including fan control
- **WiFi / Cloud** - Control via Aroma-Link cloud API, works from anywhere, no Bluetooth required
- **Connect-on-Demand** - BLE connects only when sending commands, frees the adapter for other devices
- **Automatic Time Sync** - Device clock synced on every connection
- **Schedule Management** - Set spray schedules via dashboard or automations
- **Multiple Devices** - Add as many diffusers as you want

---

## &#x1F4E6; Supported Devices

### Confirmed Working

| Device | App | Connection | Notes |
|--------|-----|------------|-------|
| Aroma-Link WiFi+BLE Diffusers | Aroma-Link | BLE + Cloud | Full support including fan control via BLE |
| JCloud Scent Diffusers | Aroma-Link | BLE + Cloud | Same as Aroma-Link, different branding |
| Cavir Smart Scent Air Machine | Aroma-Link | BLE + Cloud | Same as Aroma-Link, different branding |
| AromaPlan Diffusers | Aroma-Link | BLE + Cloud | Same as Aroma-Link, different branding |
| Crearoma Diffusers | Aroma-Link | BLE + Cloud | Same as Aroma-Link, different branding |
| ShinePick QT-I300 | Aroma Buddy | BLE | Tuya BLE protocol |

### Likely Compatible

Most waterless cold-air nebulizing scent/aroma diffusers that use the **Aroma-Link** or **Aroma Buddy** apps should work. These are sold under various brand names on Amazon and AliExpress.

> **Have a working device not listed here?** Please [open an issue](https://github.com/mr-sparks/scent-assistant/issues) to let us know!

---

## &#x1F4E5; Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mr-sparks&repository=scent-assistant&category=integration)

1. Click the button above, or in Home Assistant go to **HACS > Integrations > three dots > Custom repositories**
2. Add `https://github.com/mr-sparks/scent-assistant` as **Integration**
3. Search for **Scent Assistant** and click **Download**
4. Restart Home Assistant

### Manual Installation

1. Download the `custom_components/scent_assistant` folder from this repository
2. Copy it to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

> &#x26A0;&#xFE0F; Manual installation will not provide automatic update notifications. HACS is recommended.

---

## &#x2795; Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=scent_assistant)

Click the button above to start setup, or follow the steps below.

### Option 1: Bluetooth

Fully local, no account required. Supports all features including fan control.

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Scent Assistant**
3. Select **Bluetooth**
4. Your diffuser should appear in the device list
5. Select it and you're done

> **Note:** Make sure no other device (phone, tablet) is connected to the diffuser via Bluetooth. BLE only allows one connection at a time.

### Option 2: WiFi / Cloud (Aroma-Link only)

Control your diffuser via the Aroma-Link cloud service. No Bluetooth required.

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Scent Assistant**
3. Select **WiFi / Cloud**
4. Enter your Aroma-Link account credentials
5. Select your device from the list

> **Note:** Requires an Aroma-Link account and internet connection. Fan control is only available via Bluetooth.

---

## &#x1F3AE; Entities

| Entity | Type | Description |
|--------|------|-------------|
| Power | Switch | Turn diffuser on/off |
| Fan | Switch | Fan on/off (Aroma-Link, BLE only) |
| Start Time | Time | Daily schedule start time |
| End Time | Time | Daily schedule end time |
| Work Duration | Number | Spray duration in seconds |
| Pause Duration | Number | Pause between sprays in seconds |
| Status | Sensor | Current phase: spraying / paused / idle / off |

---

## &#x2699;&#xFE0F; Services

### `scent_assistant.set_schedule`

Set a complete spray schedule for specific days. Useful for automations.

```yaml
service: scent_assistant.set_schedule
data:
  days:
    - mon
    - wed
    - fri
  start_time: "08:00"
  end_time: "20:00"
  work_seconds: 15
  pause_seconds: 60
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `days` | Yes | - | List of days: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`, or `all` |
| `start_time` | No | `00:00` | Start time (HH:MM) |
| `end_time` | No | `23:59` | End time (HH:MM) |
| `work_seconds` | No | `10` | Spray duration (5-600 seconds) |
| `pause_seconds` | No | `120` | Pause between sprays (5-3600 seconds) |
| `enabled` | No | `true` | Enable or disable the schedule slot |

---

## &#x1F916; Automation Examples

### Turn on when arriving home

```yaml
automation:
  - alias: "Scent on when home"
    trigger:
      - platform: state
        entity_id: person.your_name
        to: "home"
    action:
      - service: switch.turn_on
        entity_id: switch.scent_diffuser_power
```

### Different schedules for weekdays and weekends

```yaml
automation:
  - alias: "Weekday scent schedule"
    trigger:
      - platform: time
        at: "00:00"
    condition:
      - condition: time
        weekday: [mon, tue, wed, thu, fri]
    action:
      - service: scent_assistant.set_schedule
        data:
          days: [mon, tue, wed, thu, fri]
          start_time: "07:00"
          end_time: "18:00"
          work_seconds: 10
          pause_seconds: 120

  - alias: "Weekend scent schedule"
    trigger:
      - platform: time
        at: "00:00"
    condition:
      - condition: time
        weekday: [sat, sun]
    action:
      - service: scent_assistant.set_schedule
        data:
          days: [sat, sun]
          start_time: "09:00"
          end_time: "22:00"
          work_seconds: 15
          pause_seconds: 60
```

### Turn off at bedtime

```yaml
automation:
  - alias: "Scent off at night"
    trigger:
      - platform: time
        at: "23:00"
    action:
      - service: switch.turn_off
        entity_id: switch.scent_diffuser_power
```

---

## &#x1F527; Troubleshooting

### Diffuser not found in BLE scan

- Make sure no other device is connected to the diffuser via Bluetooth (close the app on your phone)
- Try power-cycling the diffuser
- If your HA device is too far away, consider using an [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html)

### Commands sent but diffuser doesn't respond

- Check the HA logs for BLE connection errors
- Try reloading the integration: **Settings > Devices & Services > Scent Assistant > three dots > Reload**
- Power-cycle the diffuser

### Fan switch not available

Fan control is only available via Bluetooth. If you set up the device via Cloud/WiFi, the fan switch will not appear. This is a limitation of the Aroma-Link cloud API.

### BLE range issues

The integration uses connect-on-demand: it briefly connects, sends the command, then disconnects after 10 seconds. If your HA host is too far from the diffuser, consider:

- An [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html) (ESP32, ~$5)
- A USB Bluetooth dongle closer to the diffuser

---

## &#x1F4D6; Technical Details

This integration was built by reverse engineering the BLE protocols of both device types. For full protocol documentation, see [PROTOCOL.md](PROTOCOL.md).

| Protocol | Header | Checksum | Features |
|----------|--------|----------|----------|
| Aroma-Link | `A5 AA AC ... C5 CC CA` | XOR | Power, fan, per-day scheduling (5 slots), time sync |
| Tuya BLE (Aroma Buddy) | `55 AA ...` | Sum mod 256 | Power, scheduling (5 setups), time sync |

---

## &#x1F91D; Contributing

Contributions are welcome! If you have a diffuser that uses the Aroma-Link or Aroma Buddy app and can help test, please [open an issue](https://github.com/mr-sparks/scent-assistant/issues).

---

## &#x1F4DC; License

[MIT](LICENSE)
