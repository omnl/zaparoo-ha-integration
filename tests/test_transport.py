"""Exercise real socket request/reply, disconnect, and reconnect behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaparoo.config_flow import validate_connection
from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.websocket_client import SNAPSHOT_METHODS, ZaparooWebSocket


async def test_snapshot_errors_and_pending_disconnect(hass, core_server):
    port, sockets, requests, _ = core_server
    coordinator = ZaparooCoordinator(hass)
    async with aiohttp.ClientSession() as session:
        client = ZaparooWebSocket("127.0.0.1", port, coordinator, session)
        await client.start()
        try:
            await client.wait_ready(3)
            assert coordinator.data["synchronized"]
            assert set(SNAPSHOT_METHODS) <= {r["method"] for r in requests}
            assert coordinator.data["active_profile"]["profileId"] == "child"
            assert coordinator.data["media"]["launcherId"] == "Kodi"
            assert "switchId" not in coordinator.data["profiles"][0]
            with pytest.raises(HomeAssistantError, match="Denied"):
                await client.send_jsonrpc("fail")
            assert not client._pending
            await sockets[-1].send_json(
                {"jsonrpc": "2.0", "method": "tokens.removed", "params": None}
            )
            await asyncio.sleep(0.02)
            assert coordinator.data["last_event_method"] == "tokens.removed"
            sequence = coordinator.data["event_sequence"]
            await client.refresh()
            assert coordinator.data["event_sequence"] == sequence
            with pytest.raises(HomeAssistantError, match="disconnected"):
                await client.send_jsonrpc("drop")
            assert not client._pending
        finally:
            await client.stop()
        assert not coordinator.data["connected"]


async def test_reconnect_reloads_changed_state(hass, core_server):
    port, sockets, requests, snapshots = core_server
    coordinator = ZaparooCoordinator(hass)
    async with aiohttp.ClientSession() as session:
        client = ZaparooWebSocket("127.0.0.1", port, coordinator, session)
        await client.start()
        try:
            await client.wait_ready(3)
            snapshots["media"] = {"active": []}
            snapshots["profiles.active"] = None
            await sockets[0].close()
            async with asyncio.timeout(4):
                while len(sockets) < 2 or not coordinator.data["connected"]:
                    await asyncio.sleep(0.02)
            assert coordinator.data["active_profile"] is None
            assert coordinator.data["media"] is None
            assert sum(r["method"] == "playtime" for r in requests) >= 2
        finally:
            await client.stop()


async def test_connection_probe_requires_rpc_success(core_server):
    port, _, _, _ = core_server
    async with aiohttp.ClientSession() as session:
        result = await validate_connection(
            session, {"host": "127.0.0.1", "port": port, "transport": "agent"}
        )
        assert "profiles.manage" in result["capabilities"]


async def test_failed_send_is_action_error_and_cleans_pending(hass):
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", 7497, coordinator, None)
    client._ws = SimpleNamespace(
        closed=False, send_json=AsyncMock(side_effect=aiohttp.ClientConnectionError("closed"))
    )
    with pytest.raises(HomeAssistantError, match="disconnected while sending"):
        await client.send_jsonrpc("media")
    assert not client._pending
