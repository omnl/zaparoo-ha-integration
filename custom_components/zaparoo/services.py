"""Exposed user services."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from custom_components.zaparoo.data import ZaparooDataConfigEntry

    from .websocket_client import ZaparooWebSocket

SERVICE_LAUNCH = "launch"
SERVICE_STOP = "stop"
SERVICE_MEDIA = "media"
SERVICE_SWITCH_PROFILE = "switch_profile"

SWITCH_PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.Any(str, [str]),
        vol.Required("profile_id"): str,
        vol.Optional("pin"): str,
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


async def async_switch_profile_service(call: ServiceCall) -> None:
    """Switch an explicit profile on the target Core instance."""
    params = {"profileId": call.data["profile_id"]}
    if "pin" in call.data:
        params["pin"] = call.data["pin"]
    for device_id in dict.fromkeys(_device_ids_from_target(call)):
        ws = _get_ws_for_device(call.hass, device_id)
        await ws.send_jsonrpc("profiles.switch", params)
        await ws.refresh()


def async_register_services(hass: HomeAssistant) -> None:
    """Register all the above services."""
    for name, handler, schema in (
        (SERVICE_LAUNCH, async_launch_service, LAUNCH_SCHEMA),
        (SERVICE_STOP, async_stop_service, NO_BODY_SCHEMA),
        (SERVICE_MEDIA, async_media_service, NO_BODY_SCHEMA),
        (SERVICE_SWITCH_PROFILE, async_switch_profile_service, SWITCH_PROFILE_SCHEMA),
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
    for name in (SERVICE_LAUNCH, SERVICE_STOP, SERVICE_MEDIA, SERVICE_SWITCH_PROFILE):
        hass.services.async_remove(DOMAIN, name)


def _get_ws_for_device(hass: HomeAssistant, device_id: str) -> ZaparooWebSocket:
    device_reg = dr.async_get(hass)
    device = device_reg.async_get(device_id)

    if device is None:
        msg = f"Device not found: {device_id}"
        raise HomeAssistantError(msg)

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)

        if entry is None or entry.domain != DOMAIN:
            continue

        zap_entry = cast("ZaparooDataConfigEntry", entry)
        ws = zap_entry.runtime_data.client

        if ws is None:
            msg = "Zaparoo is not connected"
            raise HomeAssistantError(msg)

        return ws

    msg = "Zaparoo device not linked to config entry"
    raise HomeAssistantError(msg)
