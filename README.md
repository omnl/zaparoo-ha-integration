# Zaparoo Home Assistant Integration

The Zaparoo integration connects Home Assistant to a Zaparoo device, allowing you to emulate token scans, control active launchers, and monitor media and connection state in real time.
This integration is designed with events in mind. It uses a persistent WebSocket connection for fast updates and responsive control.

## Features

- Emulate scanning Zaparoo NFC or token data
- Stop active launchers remotely
- Query current media state and database info
- Live sensors for:
  - Last Zaparoo event
  - Device connection state
  - Currently playing media
- Compatible with automations, scripts, and dashboards

## Installation

### Via HACS (recommended)

1. Add this repository as a Custom Repository as Type Integration
2. Install the Zaparoo integration
3. Restart Home Assistant

### Manual Installation

1. Copy `custom_components/zaparoo` into your Home Assistant configuration directory
2. Restart Home Assistant


## Configuration

Configuration is done through the Home Assistant UI.
You will need:
- The Zaparoo device hostname or address
- Network connectivity to the device to the Home Assistant Server

Once configured, the integration creates a Zaparoo device with associated sensors and services.


## Services

### zaparoo.launch

Emulate scanning a Zaparoo token. This is the primary way to trigger ZapScript actions from Home Assistant.

Fields:

- device_id (required)  
  Target Zaparoo device

- type (optional)  
  Optional internal token category (used for logging), for example nfc

- text (optional)  
  Main token text containing ZapScript  
  Example:
  **launch.title:SNES/Super Mario World

- data (optional)  
  Raw token data as a hexadecimal string  
  Example:
  04A224BCFF12

- unsafe (optional, default: false)  
  Allow unsafe ZapScript operations

Example:
```yaml
service: zaparoo.launch  
data:  
  device_id: YOUR_DEVICE_ID  
  text: "**launch.title:SNES/Super Mario World"
```

### zaparoo.stop

Stop any active launcher, if supported by the device.

Fields:

- device_id (required)  
  Target Zaparoo device

Example:
```yaml
service: zaparoo.stop  
data:  
  device_id: YOUR_DEVICE_ID
```

### zaparoo.media

Query the current media state and database info.
This service returns a response payload and is intended for use in scripts and automations that consume service responses.

Fields:

- device_id (required)  
  Target Zaparoo device

Example:
```yaml
service: zaparoo.media  
data:  
  device_id: YOUR_DEVICE_ID  
response_variable: media_state
```
---

## Sensors

Each configured Zaparoo device provides the following sensors.

### Zaparoo Notification

Displays the most recent Zaparoo notification, such as media.started.
The sensor exposes additional attributes containing the full event payload received from the device. The full documentation of events can be found [here](https://zaparoo.org/docs/core/api/notifications/)

### Zaparoo Connected

Shows whether the Zaparoo device is currently connected.
true indicates the device is online  
false indicates the device is offline or powered off

### Zaparoo Media

Shows the name of the currently playing media, if available.
Additional attributes expose the full media payload returned by the device, including metadata such as title and platform.
If no media is active, the sensor state will be unknown.


## Core profiles and playtime

Profile features connect directly to Zaparoo Core. They require Core's `profiles`,
`profiles.active` and `playtime` APIs; older versions keep the existing media and
token features without profile entities. Core state is restored on reconnect and
refreshed every 15 seconds. Profile selection and limit actions refresh immediately.

The Core device provides an **Active profile** select entity, the active-profile
sensor, and current media-time and daily-remaining sensors. Select a profile by
name, or **Shared (no profile)** to deactivate it. A protected profile requires the
`zaparoo.switch_profile` action with its PIN; the select entity does not store a PIN
or bypass Core authorization.

Each Core profile has a child device with these entities:

| Entity | Value |
| --- | --- |
| Active | Whether Core currently uses this profile |
| Role | Core profile role |
| Daily limit override | Profile's daily limit in minutes |
| Session limit override | Profile's session limit in minutes |
| Media time today | Core's live daily usage for the active profile |
| Daily time remaining | Core's live remaining daily allowance |

Core exposes live accounting for the active profile only. An inactive profile's
usage and remaining-time sensors are unavailable. Missing Core values are unknown,
not zero. Limit sensors expose `inherited` and `unlimited` attributes: an absent
override inherits Core's global setting, while an explicit zero means unlimited.
`limits_enabled_override` is unknown when it inherits the global setting.
Profile IDs are scoped to the Core instance, so renaming a profile preserves its
entity identity. Newly discovered profiles are added automatically; removed
profiles become unavailable so existing automations and history retain their IDs.

### Link profiles to Home Assistant people

Open **Settings → Devices & services → Zaparoo → Configure**, select a profile and
choose a `person` entity. Repeat for other profiles. Leave the person field empty
to remove a link. The link appears as the `person` attribute on profile entities
and on current-profile/accounting entities. This setting stores only the HA link;
it does not change Core profiles or switch them when a person arrives or leaves.

### Switch a profile

Use `profile_id`, a linked `person`, or target a profile's child `device_id` without
either field. A person must resolve to exactly one existing profile on the target
Core instance. To return to the shared profile, use only `deactivate: true`.
Core enforces the same PIN and permission checks as its own clients.

```yaml
action: zaparoo.switch_profile
data:
  device_id: YOUR_CORE_DEVICE_ID
  person: person.alice
  # pin: "1234"  # Only when Core requires it; prefer a HA secret.
```

### Set Core profile limits

`zaparoo.set_profile_limits` accepts a profile device, an explicit `profile_id`, or
a linked `person`. Supply one or more of `enabled`, `daily_minutes`,
`session_minutes`, and `clear_limits`. Times are whole minutes from 0 to 1440;
**zero means unlimited**. Omitted fields remain unchanged. `clear_limits: true`
first removes all profile limit overrides so they inherit the global Core settings,
then applies any other fields supplied in the same action. Core requires profile
management permission and reports authorization or validation errors to HA.

```yaml
action: zaparoo.set_profile_limits
data:
  device_id: YOUR_PROFILE_DEVICE_ID
  enabled: true
  daily_minutes: 60
  session_minutes: 30
```

Limits are sent to Core once per action. Core remains responsible for persistence
and enforcement; these values are not replayed from HA settings on reconnect.

## Debugging

To enable debug logging:
```yaml
logger:  
  logs:  
    custom_components.zaparoo: debug
```

## License

GPL-3.0 license




