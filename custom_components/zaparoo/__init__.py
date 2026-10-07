"""Zaparoo integration init."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import callback
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
from .profile import profile_identifier, profile_parent_device

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
PLATFORMS = [Platform.SENSOR, Platform.EVENT, Platform.BINARY_SENSOR, Platform.SELECT]


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
        entry=entry,
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
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady(str(err)) from err
    core_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Zaparoo ({entry.data['host']})",
        manufacturer="Zaparoo",
    )
    entry.runtime_data.device_id = core_device.id

    @callback
    def sync_profile_devices() -> None:
        registry = dr.async_get(hass)
        for profile in coordinator.data["profiles"]:
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, profile_identifier(entry, profile["profileId"]))},
                name=profile["name"],
                manufacturer="Zaparoo",
                **profile_parent_device(entry),
            )

    sync_profile_devices()
    entry.async_on_unload(coordinator.async_add_listener(sync_profile_devices))

    async def on_shutdown(_event: Event) -> None:
        """Close the connection when HA stops."""
        await entry.runtime_data.client.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown)
    )
    # Load entity platforms after the initial Core snapshot.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
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


async def async_options_updated(
    _hass: HomeAssistant, entry: ZaparooDataConfigEntry
) -> None:
    """Refresh person metadata without replaying settings or reconnecting."""
    entry.runtime_data.coordinator.async_set_updated_data(
        dict(entry.runtime_data.coordinator.data)
    )
