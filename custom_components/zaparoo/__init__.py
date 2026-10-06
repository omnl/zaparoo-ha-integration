"""KidsStation additions to the Zaparoo Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.loader import async_get_loaded_integration

from .const import DOMAIN
from .coordinator import ZaparooCoordinator
from .data import ZaparooData
from .services import async_register_services, async_unregister_services
from .websocket_client import ZaparooWebSocket

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.BUTTON]


async def async_setup_entry(hass, entry):
    """Load entities after Core has provided a complete snapshot."""
    coordinator = ZaparooCoordinator(hass, entry)
    client = ZaparooWebSocket(
        entry.data["host"],
        entry.data["port"],
        coordinator,
        async_get_clientsession(hass),
        token=entry.data.get("token", ""),
        agent=entry.data.get("transport") == "agent",
    )
    coordinator.client = client
    entry.runtime_data = ZaparooData(
        client, coordinator, async_get_loaded_integration(hass, entry.domain)
    )
    await client.start()
    try:
        await client.wait_ready()
        await coordinator.push_policies()
    except (TimeoutError, HomeAssistantError) as err:
        await client.stop()
        raise ConfigEntryNotReady(str(err)) from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.runtime_data
    async_get_device_registry(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"KidsStation ({entry.data['host']})",
        manufacturer="KidsStation / Zaparoo",
    )

    async def on_shutdown(event):
        await client.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_shutdown))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    async_register_services(hass)
    return True


async def async_options_updated(hass, entry):
    """Apply options without restarting the live connection."""
    await entry.runtime_data.coordinator.push_policies()
    coordinator = entry.runtime_data.coordinator
    coordinator.async_set_updated_data(dict(coordinator.data))


async def async_unload_entry(hass, entry):
    """Remove actions only after the final instance is unloaded."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.client.stop()
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        async_unregister_services(hass)
    return True
