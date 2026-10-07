"""Exposed user services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .profile import profile_identifier, resolve_profile

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from custom_components.zaparoo.data import ZaparooDataConfigEntry

    from .websocket_client import ZaparooWebSocket

SERVICE_LAUNCH = "launch"
SERVICE_STOP = "stop"
SERVICE_MEDIA = "media"
SERVICE_SWITCH_PROFILE = "switch_profile"
SERVICE_SET_PROFILE_LIMITS = "set_profile_limits"

SWITCH_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.Any(str, [str]),
        vol.Exclusive("profile_id", "recipient"): str,
        vol.Exclusive("person", "recipient"): str,
        vol.Exclusive("deactivate", "recipient"): vol.In([True]),
        vol.Optional("pin"): str,
    }
)

PROFILE_LIMITS_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.Any(str, [str]),
        vol.Exclusive("profile_id", "recipient"): str,
        vol.Exclusive("person", "recipient"): str,
        vol.Optional("enabled"): bool,
        vol.Optional("daily_minutes"): vol.All(int, vol.Range(min=0, max=1440)),
        vol.Optional("session_minutes"): vol.All(int, vol.Range(min=0, max=1440)),
        vol.Optional("clear_limits", default=False): bool,
    }
)

LAUNCH_SCHEMA = vol.Schema(
    {
        vol.Optional("type"): str,
        vol.Optional("text"): str,
        vol.Optional("data"): str,
        vol.Optional("unsafe", default=False): bool,
        vol.Optional("device_id"): object,
        vol.Optional("area_id"): object,
    }
)

NO_BODY_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): object,
        vol.Optional("area_id"): object,
    }
)


def _device_ids_from_target(call: ServiceCall) -> list[str]:
    """Extract device IDs from HA service call."""
    device_ids = call.data.get("device_id")
    if not device_ids:
        msg = "No target devices specified"
        raise HomeAssistantError(msg)
    if isinstance(device_ids, str):
        return [device_ids]
    if isinstance(device_ids, list):
        return device_ids
    msg = "Invalid device_id type"
    raise HomeAssistantError(msg)


async def async_launch_service(call: ServiceCall) -> None:
    """Call to launch a token."""
    hass = call.hass
    device_ids = _device_ids_from_target(call)

    if not any(call.data.get(k) for k in ("text", "data")):
        msg = "One of 'text' or 'data' is required"
        raise HomeAssistantError(msg)

    params = {
        k: v
        for k, v in {
            "type": call.data.get("type"),
            "text": call.data.get("text"),
            "data": call.data.get("data"),
            "unsafe": call.data.get("unsafe"),
        }.items()
        if v is not None
    }

    for device_id in device_ids:
        ws = _get_ws_for_device(hass, device_id)

        try:
            response = await ws.send_jsonrpc("run", params)
        except Exception as err:
            msg = f"Launch failed: {err}"
            raise HomeAssistantError(msg) from err

        if isinstance(response, dict) and "error" in response:
            raise HomeAssistantError(response["error"])

        result = response.get("result") if isinstance(response, dict) else None
        if result not in (None, {}, []):
            msg = "Non-empty result from Zaparoo run(): %s"
            raise HomeAssistantError(msg, result)


async def async_stop_service(call: ServiceCall) -> None:
    """Call to stop the current running game."""
    hass = call.hass
    device_ids = _device_ids_from_target(call)

    for device_id in device_ids:
        ws = _get_ws_for_device(hass, device_id)

        try:
            response = await ws.send_jsonrpc("stop")
        except Exception as err:
            msg = f"Stop failed: {err}"
            raise HomeAssistantError(msg) from err

        if isinstance(response, dict) and "error" in response:
            raise HomeAssistantError(response["error"])

        result = response.get("result") if isinstance(response, dict) else None
        if result not in (None, {}, []):
            msg = "Non-empty result from Zaparoo stop(): %s"
            raise HomeAssistantError(msg, result)


async def async_media_service(call: ServiceCall) -> Any:
    """Call to return the currently running game."""
    hass = call.hass
    device_ids = _device_ids_from_target(call)
    if len(device_ids) != 1:
        msg = "Media query requires exactly one device"
        raise HomeAssistantError(msg)
    device_id = device_ids[0]
    ws = _get_ws_for_device(hass, device_id)

    try:
        response = await ws.send_jsonrpc("media")
    except Exception as err:
        msg = f"Media query failed: {err}"
        raise HomeAssistantError(msg) from err

    if isinstance(response, dict) and "error" in response:
        raise HomeAssistantError(response["error"])

    return response.get("result") if isinstance(response, dict) else None


def _profile_for_target(
    entry: ZaparooDataConfigEntry, call: ServiceCall, device_id: str
) -> str:
    """Allow profile devices as targets while keeping all resolution instance-local."""
    if call.data.get("profile_id") or call.data.get("person"):
        return resolve_profile(entry, call.data)
    device = dr.async_get(call.hass).async_get(device_id)
    if device:
        for profile in entry.runtime_data.coordinator.data["profiles"]:
            if (
                DOMAIN,
                profile_identifier(entry, profile["profileId"]),
            ) in device.identifiers:
                return profile["profileId"]
    return resolve_profile(entry, call.data)


async def async_switch_profile_service(call: ServiceCall) -> None:
    """Switch by ID, person or profile device; Core still enforces PINs."""
    for device_id in dict.fromkeys(_device_ids_from_target(call)):
        entry = _get_entry_for_device(call.hass, device_id)
        params = (
            {}
            if call.data.get("deactivate")
            else {"profileId": _profile_for_target(entry, call, device_id)}
        )
        if "pin" in call.data:
            params["pin"] = call.data["pin"]
        await entry.runtime_data.client.send_jsonrpc("profiles.switch", params)
        await entry.runtime_data.client.refresh()


async def async_set_profile_limits_service(call: ServiceCall) -> None:
    """Change Core limit overrides once, without a scheduler or local rules."""
    fields = {
        "enabled": "limitsEnabled",
        "daily_minutes": "dailyLimit",
        "session_minutes": "sessionLimit",
    }
    changes = {}
    for field, core_field in fields.items():
        if field in call.data:
            value = call.data[field]
            changes[core_field] = (
                f"{value * 60}s" if field.endswith("_minutes") else value
            )
    if call.data.get("clear_limits"):
        changes["clearLimits"] = True
    if not changes:
        msg = "Specify a limit setting or clear_limits"
        raise HomeAssistantError(msg)
    for device_id in dict.fromkeys(_device_ids_from_target(call)):
        entry = _get_entry_for_device(call.hass, device_id)
        params = {"profileId": _profile_for_target(entry, call, device_id), **changes}
        await entry.runtime_data.client.send_jsonrpc("profiles.update", params)
        await entry.runtime_data.client.refresh()


def async_register_services(hass: HomeAssistant) -> None:
    """Register all the above services."""
    for name, handler, schema in (
        (SERVICE_LAUNCH, async_launch_service, LAUNCH_SCHEMA),
        (SERVICE_STOP, async_stop_service, NO_BODY_SCHEMA),
        (SERVICE_MEDIA, async_media_service, NO_BODY_SCHEMA),
        (SERVICE_SWITCH_PROFILE, async_switch_profile_service, SWITCH_PROFILE_SCHEMA),
        (
            SERVICE_SET_PROFILE_LIMITS,
            async_set_profile_limits_service,
            PROFILE_LIMITS_SCHEMA,
        ),
    ):
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                handler,
                schema=schema,
                supports_response=SupportsResponse.ONLY
                if name == SERVICE_MEDIA
                else SupportsResponse.NONE,
            )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove services after the final integration instance unloads."""
    for name in (
        SERVICE_LAUNCH,
        SERVICE_STOP,
        SERVICE_MEDIA,
        SERVICE_SWITCH_PROFILE,
        SERVICE_SET_PROFILE_LIMITS,
    ):
        hass.services.async_remove(DOMAIN, name)


def _get_ws_for_device(hass: HomeAssistant, device_id: str) -> ZaparooWebSocket:
    """Route an existing action through the loaded Core instance."""
    return _get_entry_for_device(hass, device_id).runtime_data.client


def _get_entry_for_device(
    hass: HomeAssistant, device_id: str
) -> ZaparooDataConfigEntry:
    """Resolve root and profile devices without crossing Core instances."""
    device_reg = dr.async_get(hass)
    device = device_reg.async_get(device_id)

    if device is None:
        msg = f"Device not found: {device_id}"
        raise HomeAssistantError(msg)

    entry_ids = (
        {device.config_entry_id}
        if hasattr(device, "config_entry_id")
        else device.config_entries
    )
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)

        if (
            entry is None
            or entry.domain != DOMAIN
            or entry_id not in hass.data.get(DOMAIN, {})
        ):
            continue

        zap_entry = cast("ZaparooDataConfigEntry", entry)
        runtime = getattr(zap_entry, "runtime_data", None)

        if runtime is None or runtime.client is None:
            msg = "Zaparoo is not connected"
            raise HomeAssistantError(msg)

        return zap_entry

    msg = "Zaparoo device not linked to a loaded config entry"
    raise HomeAssistantError(msg)
