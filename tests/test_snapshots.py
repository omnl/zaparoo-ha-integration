"""Reconnect and event identity regressions."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.event import ZaparooEventEntity
from custom_components.zaparoo.websocket_client import ZaparooWebSocket


async def test_bootstrap_and_refresh_do_not_repeat_events(hass, core_server):
    port, _sockets, requests, snapshots = core_server
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", port, coordinator)
    await client.start()
    try:
        await client.wait_ready(3)
        assert coordinator.data["media"]["mediaName"] == "Show"
        assert {"media", "readers"} <= {r["method"] for r in requests}
        event = ZaparooEventEntity(SimpleNamespace(entry_id="entry"), coordinator)
        event._trigger_event = Mock()
        event.async_write_ha_state = Mock()
        coordinator.data["playtime"] = {"dailyUsageToday": "30m"}
        coordinator.handle_ws_event("playtime.limit.warning", {"remaining": "10m"})
        event._handle_coordinator_update()
        event._handle_coordinator_update()
        assert event._trigger_event.call_count == 1
        assert coordinator.data["playtime"]["dailyUsageToday"] == "30m"
        snapshots["media"] = {"active": []}
        await client.refresh()
        event._handle_coordinator_update()
        assert coordinator.data["media"] is None
        assert event._trigger_event.call_count == 1
    finally:
        await client.stop()


async def test_nullable_events(hass):
    coordinator = ZaparooCoordinator(hass)
    coordinator.handle_ws_event("readers.removed", None)
    coordinator.handle_ws_event("profiles.active", {"profile": None})
    assert coordinator.data["active_profile"] is None
