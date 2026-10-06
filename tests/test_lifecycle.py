"""Multiple entries share one service registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp

from custom_components.zaparoo import async_unload_entry
from custom_components.zaparoo.config_flow import validate_connection
from custom_components.zaparoo.services import async_register_services


async def test_unload_keeps_other_instances_services(hass):
    first = SimpleNamespace(
        entry_id="first",
        runtime_data=SimpleNamespace(client=SimpleNamespace(stop=AsyncMock())),
    )
    last = SimpleNamespace(
        entry_id="last",
        runtime_data=SimpleNamespace(client=SimpleNamespace(stop=AsyncMock())),
    )
    hass.data["zaparoo"] = {"first": {}, "last": {}}
    hass.config_entries = SimpleNamespace(
        async_unload_platforms=AsyncMock(return_value=True)
    )
    async_register_services(hass)
    async_register_services(hass)
    assert await async_unload_entry(hass, first)
    assert hass.services.has_service("zaparoo", "stop")
    assert await async_unload_entry(hass, last)
    assert not hass.services.has_service("zaparoo", "stop")


async def test_core_probe(core_server):
    port, _, _, _ = core_server
    async with aiohttp.ClientSession() as session:
        await validate_connection(session, "127.0.0.1", port)
