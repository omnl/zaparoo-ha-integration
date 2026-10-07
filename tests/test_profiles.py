"""Core profile, playtime and optional capability regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaparoo import services
from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.models import duration_minutes
from custom_components.zaparoo.sensor import ZaparooPlaytimeSensor, ZaparooProfileSensor
from custom_components.zaparoo.websocket_client import ZaparooWebSocket


async def test_profiles_playtime_and_safe_attributes(hass, core_server):
    port, _, _, snapshots = core_server
    snapshots["profiles.active"]["switchId"] = "private-token"
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", port, coordinator)
    await client.start()
    try:
        await client.wait_ready(3)
        entry = SimpleNamespace(entry_id="core", options={})
        sensor = ZaparooProfileSensor(entry, coordinator)
        assert sensor.native_value == "Child"
        assert sensor.extra_state_attributes["profile_id"] == "child"
        assert "private-token" not in str(coordinator.data)
        assert "secret-card" not in str(sensor.extra_state_attributes)
        assert (
            ZaparooPlaytimeSensor(entry, coordinator, "dailyUsageToday").native_value
            == 12.5
        )
        assert (
            ZaparooPlaytimeSensor(entry, coordinator, "dailyRemaining").native_value
            == 47.5
        )
    finally:
        await client.stop()


async def test_old_core_optional_methods_and_real_errors(hass, core_server):
    port, _, _, snapshots = core_server
    snapshots["unsupported"] = [
        "profiles",
        "profiles.active",
        "playtime",
        "clients.current",
    ]
    client = ZaparooWebSocket("127.0.0.1", port, ZaparooCoordinator(hass))
    await client.start()
    try:
        await client.wait_ready(3)
        assert client.coordinator.data["connected"]
        assert client.coordinator.data["profiles"] == []
        snapshots["unsupported"] = []
        snapshots["denied"] = ["profiles"]
        with pytest.raises(HomeAssistantError, match="Denied"):
            await client.refresh()
    finally:
        await client.stop()


async def test_switch_profile_targets_only_requested_core(hass, monkeypatch):
    clients = {
        key: SimpleNamespace(send_jsonrpc=AsyncMock(), refresh=AsyncMock())
        for key in ("first", "second")
    }
    entries = {
        key: SimpleNamespace(
            options={},
            runtime_data=SimpleNamespace(
                client=client,
                coordinator=SimpleNamespace(
                    data={"profiles": [{"profileId": "child"}]}
                ),
            ),
        )
        for key, client in clients.items()
    }
    monkeypatch.setattr(services, "_get_entry_for_device", lambda _, key: entries[key])
    call = ServiceCall(
        hass,
        "zaparoo",
        "switch_profile",
        {"device_id": "second", "profile_id": "child", "pin": "1234"},
    )
    await services.async_switch_profile_service(call)
    clients["first"].send_jsonrpc.assert_not_awaited()
    clients["second"].send_jsonrpc.assert_awaited_once_with(
        "profiles.switch", {"profileId": "child", "pin": "1234"}
    )
    clients["second"].refresh.assert_awaited_once()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1h2m30s", 62.5),
        ("-30s", -0.5),
        ("0s", 0),
        ("not-a-duration", None),
        ("12minutes", None),
        (None, None),
    ],
)
def test_core_durations(value, expected):
    assert duration_minutes(value) == expected
