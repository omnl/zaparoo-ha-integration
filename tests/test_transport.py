"""Transport regressions using real sockets."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.websocket_client import ZaparooWebSocket


async def test_rpc_errors_and_disconnect(hass, core_server):
    port, _sockets, _, _ = core_server
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", port, coordinator)
    ready = asyncio.Event()
    remove = coordinator.async_add_listener(
        lambda: ready.set() if coordinator.data["connected"] else None
    )
    await client.start()
    try:
        await asyncio.wait_for(ready.wait(), 3)
        with pytest.raises(HomeAssistantError, match="Denied"):
            await client.send_jsonrpc("fail")
        assert not client._pending
        with pytest.raises(HomeAssistantError, match="disconnected"):
            await client.send_jsonrpc("drop")
        assert not client._pending
    finally:
        remove()
        await client.stop()


async def test_failed_send_cleans_pending(hass):
    client = ZaparooWebSocket("127.0.0.1", 7497, ZaparooCoordinator(hass))
    client._ws = SimpleNamespace(
        closed=False,
        send_json=AsyncMock(side_effect=aiohttp.ClientConnectionError("closed")),
    )
    with pytest.raises(HomeAssistantError, match="disconnected while sending"):
        await client.send_jsonrpc("media")
    assert not client._pending
