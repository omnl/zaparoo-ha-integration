"""Validated, per-device actions, registered once across all instances."""

import uuid

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

SERVICES = ("launch", "stop", "media", "switch_profile", "add_time", "set_schedule")
TARGET = {vol.Required("device_id"): vol.Any(str, [str])}
RECIPIENT = {
    vol.Exclusive("profile_id", "recipient"): str,
    vol.Exclusive("person", "recipient"): str,
}


def _entry_for_device(hass, device_id):
    device = dr.async_get(hass).async_get(device_id)
    if device:
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN and entry_id in hass.data.get(DOMAIN, {}):
                return entry
    raise HomeAssistantError(f"No loaded KidsStation instance for device {device_id}")


def _recipient(entry, data):
    if data.get("profile_id"):
        return data["profile_id"]
    matches = [
        pid
        for pid, options in entry.options.get("profile_options", {}).items()
        if data.get("person") and options.get("person") == data["person"]
    ]
    if len(matches) != 1:
        raise HomeAssistantError(
            "Choose a profile_id or a person linked to exactly one profile on this device"
        )
    return matches[0]


async def _handle(call):
    device_ids = call.data["device_id"]
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    if call.service == "media" and len(device_ids) != 1:
        raise HomeAssistantError("Media query requires exactly one device")
    for device_id in dict.fromkeys(device_ids):
        entry = _entry_for_device(call.hass, device_id)
        client = entry.runtime_data.client
        data = call.data
        params = None
        method = call.service
        if call.service == "launch":
            if not any(data.get(k) for k in ("text", "data")):
                raise HomeAssistantError("Token text or data is required")
            params = {k: data[k] for k in ("text", "data", "type", "unsafe") if k in data}
            method = "run"
        elif call.service == "switch_profile":
            method = "profiles.switch"
            params = {"profileId": _recipient(entry, data)}
            if "pin" in data:
                params["pin"] = data["pin"]
        elif call.service in ("add_time", "set_schedule"):
            if not client.agent:
                raise HomeAssistantError("This action requires the local KidsStation agent")
            params = {"profileId": _recipient(entry, data)}
            if call.service == "add_time":
                method = "kidsstation.add_time"
                params.update(minutes=data["minutes"], requestId=str(uuid.uuid4()))
            else:
                method = "kidsstation.policy.set"
                params.update(
                    minutes=data["minutes"],
                    enabled=data["enabled"],
                    timezone=call.hass.config.time_zone,
                )
        result = (await client.send_jsonrpc(method, params))["result"]
        if call.service in ("add_time", "set_schedule"):
            entry.runtime_data.coordinator.handle_ws_event("kidsstation.state", result)
        if call.service == "set_schedule":
            saved = entry.options.get("profile_options", {})
            pid = params["profileId"]
            call.hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    "profile_options": {
                        **saved,
                        pid: {
                            **saved.get(pid, {}),
                            "minutes": params["minutes"],
                            "enabled": params["enabled"],
                        },
                    },
                },
            )
        if call.service == "media":
            return result
    return None


def async_register_services(hass):
    schemas = {
        "launch": {
            **TARGET,
            vol.Optional("type"): str,
            vol.Optional("text"): str,
            vol.Optional("data"): str,
            vol.Optional("unsafe", default=False): bool,
        },
        "stop": TARGET,
        "media": TARGET,
        "switch_profile": {**TARGET, **RECIPIENT, vol.Optional("pin"): str},
        "add_time": {
            **TARGET,
            **RECIPIENT,
            vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        },
        "set_schedule": {
            **TARGET,
            **RECIPIENT,
            vol.Required("minutes"): vol.All(
                [vol.All(vol.Coerce(int), vol.Range(min=0, max=1440))], vol.Length(min=7, max=7)
            ),
            vol.Optional("enabled", default=True): bool,
        },
    }
    for name, schema in schemas.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                _handle,
                schema=vol.Schema(schema),
                supports_response=SupportsResponse.ONLY
                if name == "media"
                else SupportsResponse.NONE,
            )


def async_unregister_services(hass):
    for name in SERVICES:
        hass.services.async_remove(DOMAIN, name)
