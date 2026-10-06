"""Zaparoo integration init."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_loaded_integration

from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.data import ZaparooData, ZaparooDataConfigEntry
from custom_components.zaparoo.services import (
    async_register_services,
    async_unregister_services,
)
from custom_components.zaparoo.websocket_client import ZaparooWebSocket

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
PLATFORMS = [Platform.SENSOR, Platform.EVENT, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ZaparooDataConfigEntry) -> bool:
    """Set up a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Store host/port
    hass.data[DOMAIN][entry.entry_id] = {
        "host": entry.data["host"],
        "port": entry.data["port"],
    }

    coordinator = ZaparooCoordinator(
        hass=hass,
    )

    entry.runtime_data = ZaparooData(
        client=ZaparooWebSocket(
            host=entry.data["host"],
            port=entry.data["port"],
            coordinator=coordinator,
        ),
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )
    await entry.runtime_data.client.start()
    try:
        await entry.runtime_data.client.wait_ready()
    except (TimeoutError, HomeAssistantError) as err:
        await entry.runtime_data.client.stop()
        raise ConfigEntryNotReady(str(err)) from err
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Zaparoo ({entry.data['host']})",
        manufacturer="Zaparoo",
    )

    async def on_shutdown(_event: Event) -> None:
        """Close the connection when HA stops."""
        await entry.runtime_data.client.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown)
    )
    # Load the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ZaparooDataConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.stop()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        async_unregister_services(hass)
    return True
