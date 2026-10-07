"""Profile identity, explicit switching, Core overrides and local person links."""

from inspect import signature
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

import custom_components.zaparoo as integration
from custom_components.zaparoo import (
    async_options_updated,
    async_setup_entry,
    async_unload_entry,
    sensor,
    services,
)
from custom_components.zaparoo.binary_sensor import ZaparooProfileActiveSensor
from custom_components.zaparoo.config_flow import ZaparooOptionsFlow
from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.profile import CONF_PROFILE_PEOPLE, profile_identifier
from custom_components.zaparoo.select import SHARED_PROFILE, ZaparooProfileSelect
from custom_components.zaparoo.sensor import ZaparooProfileValueSensor
from custom_components.zaparoo.websocket_client import ZaparooWebSocket


def profile_entry(hass, entry_id="core", profile_id="child", person="person.child"):
    coordinator = ZaparooCoordinator(hass)
    coordinator.data.update(
        connected=True,
        profiles=[{"profileId": profile_id, "name": "Child", "role": "member"}],
        active_profile={"profileId": profile_id, "name": "Child"},
    )
    client = SimpleNamespace(
        host="127.0.0.1", send_jsonrpc=AsyncMock(), refresh=AsyncMock()
    )
    return SimpleNamespace(
        entry_id=entry_id,
        domain="zaparoo",
        options={CONF_PROFILE_PEOPLE: {profile_id: person}},
        async_on_unload=Mock(),
        runtime_data=SimpleNamespace(
            coordinator=coordinator, client=client, device_id="root"
        ),
    )


async def test_profile_entities_discover_rename_and_remove(hass):
    entry = profile_entry(hass)
    coordinator = entry.runtime_data.coordinator
    entities = []
    await sensor.async_setup_entry(hass, entry, lambda new: entities.extend(new))
    limits = next(
        e
        for e in entities
        if isinstance(e, ZaparooProfileValueSensor) and e.field == "dailyLimit"
    )
    unique_id = limits.unique_id
    assert limits.extra_state_attributes["inherited"] is True
    coordinator.data["profiles"][0].update(
        name="New name", dailyLimit="1h", limitsEnabled=True
    )
    coordinator.async_set_updated_data(dict(coordinator.data))
    assert limits.native_value == 60
    assert limits.device_info["name"] == "New name"
    assert limits.unique_id == unique_id
    assert limits.extra_state_attributes["person"] == "person.child"
    before = len(entities)
    coordinator.data["profiles"].append({"profileId": "other", "name": "Sibling"})
    coordinator.async_set_updated_data(dict(coordinator.data))
    after = len(entities)
    assert after > before
    coordinator.async_set_updated_data(dict(coordinator.data))
    assert len(entities) == after
    coordinator.data["profiles"] = []
    coordinator.async_set_updated_data(dict(coordinator.data))
    assert not limits.available


async def test_profile_usage_is_only_available_for_active_identity(hass):
    entry = profile_entry(hass)
    coordinator = entry.runtime_data.coordinator
    coordinator.data["profiles"].append({"profileId": "other", "name": "Sibling"})
    coordinator.data["playtime"] = {"dailyUsageToday": "12m"}
    child = ZaparooProfileValueSensor(entry, coordinator, "child", "dailyUsageToday")
    other = ZaparooProfileValueSensor(entry, coordinator, "other", "dailyUsageToday")
    active = ZaparooProfileActiveSensor(entry, coordinator, "child")
    assert child.native_value == 12
    assert not other.available
    assert other.native_value is None
    assert active.is_on
    coordinator.handle_ws_event(
        "profiles.active",
        {"profile": {"profileId": "other", "name": "Sibling", "switchId": "secret"}},
    )
    assert not active.is_on
    assert not child.available
    assert "secret" not in str(coordinator.data)
    assert other.native_value is None  # Never show Child's last accounting on Sibling.
    coordinator.data["playtime"] = {"dailyUsageToday": "5m"}
    assert other.native_value == 5
    coordinator.disconnected()
    assert not other.available


async def test_select_duplicate_names_pin_and_shared_profile(hass, core_server):
    port, _, requests, snapshots = core_server
    snapshots["profiles"]["profiles"].append(
        {"profileId": "parent", "name": "Child", "hasPin": True}
    )
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", port, coordinator)
    entry = SimpleNamespace(
        entry_id="core", options={}, runtime_data=SimpleNamespace(client=client)
    )
    select = ZaparooProfileSelect(entry, coordinator)
    await client.start()
    try:
        await client.wait_ready(3)
        assert select.options == [SHARED_PROFILE, "Child [child]", "Child [parent]"]
        with pytest.raises(HomeAssistantError, match="PIN required"):
            await select.async_select_option("Child [parent]")
        assert select.current_option == "Child [child]"
        assert "pin" not in select.extra_state_attributes
        await select.async_select_option(SHARED_PROFILE)
        assert select.current_option == SHARED_PROFILE
        assert any(
            r["method"] == "profiles.switch" and r.get("params") == {} for r in requests
        )
        with pytest.raises(HomeAssistantError, match="no longer exists"):
            await select.async_select_option("Removed profile")
    finally:
        await client.stop()


async def test_snapshot_switch_does_not_attribute_previous_usage(hass, core_server):
    port, _, _, snapshots = core_server
    snapshots["profiles"]["profiles"].append({"profileId": "parent", "name": "Parent"})
    snapshots["switch_during_playtime"] = "parent"
    coordinator = ZaparooCoordinator(hass)
    client = ZaparooWebSocket("127.0.0.1", port, coordinator)
    await client.start()
    try:
        await client.wait_ready(3)
        assert coordinator.data["active_profile"]["profileId"] == "parent"
        assert coordinator.data["playtime"] == {}
        snapshots["playtime"] = {"dailyUsageToday": "5m"}
        await client.refresh()
        assert coordinator.data["playtime"]["dailyUsageToday"] == "5m"
    finally:
        await client.stop()


async def test_snapshot_discards_usage_when_profile_switches_away_and_back(
    hass, core_server
):
    port, _, _, snapshots = core_server
    snapshots["profiles"]["profiles"].append({"profileId": "parent", "name": "Parent"})
    snapshots["switch_during_playtime"] = "parent"
    snapshots["switch_back"] = True
    client = ZaparooWebSocket("127.0.0.1", port, ZaparooCoordinator(hass))
    await client.start()
    try:
        await client.wait_ready(3)
        assert client.coordinator.data["active_profile"]["profileId"] == "child"
        assert client.coordinator.data["playtime"] == {}
    finally:
        await client.stop()


async def test_person_options_preserve_links_and_never_write_core(hass):
    entry = profile_entry(hass)
    entry.options["unrelated_option"] = True
    entry.options[CONF_PROFILE_PEOPLE]["other"] = "person.other"
    hass.states.async_set("person.alice", "home")
    flow = ZaparooOptionsFlow(entry)
    flow.hass = hass
    result = await flow.async_step_init({"profile_id": "child"})
    assert result["step_id"] == "person"
    result = await flow.async_step_person({"person": "person.alice"})
    assert result["data"][CONF_PROFILE_PEOPLE] == {
        "child": "person.alice",
        "other": "person.other",
    }
    assert result["data"]["unrelated_option"] is True
    entry.options = result["data"]
    await async_options_updated(hass, entry)
    result = await flow.async_step_person({})
    assert result["data"][CONF_PROFILE_PEOPLE] == {"other": "person.other"}
    entry.runtime_data.client.send_jsonrpc.assert_not_awaited()
    entry.runtime_data.client.refresh.assert_not_awaited()


async def test_person_options_validate_current_profiles_and_people(hass):
    entry = profile_entry(hass)
    flow = ZaparooOptionsFlow(entry)
    flow.hass = hass
    assert (await flow.async_step_init({"profile_id": "removed"}))[
        "reason"
    ] == "profile_removed"
    await flow.async_step_init({"profile_id": "child"})
    result = await flow.async_step_person({"person": "person.missing"})
    assert result["errors"]["base"] == "invalid_person"
    entry.runtime_data.coordinator.data["profiles"] = []
    assert (await flow.async_step_person({}))["reason"] == "profile_removed"
    entry.runtime_data.coordinator.disconnected()
    assert (await flow.async_step_init())["reason"] == "not_connected"


async def test_actions_resolve_people_per_instance_and_profile_devices(
    hass, monkeypatch
):
    entries = {
        key: profile_entry(hass, key, pid)
        for key, pid in [("first", "child"), ("second", "sibling")]
    }
    devices = {
        key: SimpleNamespace(config_entries={key}, identifiers={("zaparoo", key)})
        for key in entries
    }
    devices["child-device"] = SimpleNamespace(
        config_entries={"second"},
        identifiers={("zaparoo", profile_identifier(entries["second"], "sibling"))},
    )
    hass.config_entries = SimpleNamespace(async_get_entry=lambda key: entries.get(key))
    hass.data["zaparoo"] = dict(entries)
    monkeypatch.setattr(
        services.dr, "async_get", lambda _: SimpleNamespace(async_get=devices.get)
    )
    services.async_register_services(hass)
    await hass.services.async_call(
        "zaparoo",
        "switch_profile",
        {"device_id": "second", "person": "person.child", "pin": "1234"},
        blocking=True,
    )
    entries["first"].runtime_data.client.send_jsonrpc.assert_not_awaited()
    second = entries["second"].runtime_data.client
    second.send_jsonrpc.assert_awaited_once_with(
        "profiles.switch", {"profileId": "sibling", "pin": "1234"}
    )
    await hass.services.async_call(
        "zaparoo",
        "set_profile_limits",
        {"device_id": "child-device", "daily_minutes": 0, "enabled": True},
        blocking=True,
    )
    assert second.send_jsonrpc.call_args.args == (
        "profiles.update",
        {"profileId": "sibling", "dailyLimit": "0s", "limitsEnabled": True},
    )
    await hass.services.async_call(
        "zaparoo",
        "set_profile_limits",
        {"device_id": "second", "profile_id": "sibling", "clear_limits": True},
        blocking=True,
    )
    assert second.send_jsonrpc.call_args.args == (
        "profiles.update",
        {"profileId": "sibling", "clearLimits": True},
    )
    entries["second"].runtime_data.coordinator.data["profiles"].append(
        {"profileId": "duplicate", "name": "Other"}
    )
    entries["second"].options[CONF_PROFILE_PEOPLE]["duplicate"] = "person.child"
    calls = second.send_jsonrpc.call_count
    with pytest.raises(HomeAssistantError, match="exactly one"):
        await hass.services.async_call(
            "zaparoo",
            "switch_profile",
            {"device_id": "second", "person": "person.child"},
            blocking=True,
        )
    assert second.send_jsonrpc.call_count == calls
    hass.data["zaparoo"].pop("second")
    with pytest.raises(HomeAssistantError, match="loaded config entry"):
        await hass.services.async_call(
            "zaparoo",
            "switch_profile",
            {"device_id": "second", "profile_id": "sibling"},
            blocking=True,
        )
    services.async_unregister_services(hass)
    assert not hass.services.has_service("zaparoo", "set_profile_limits")


async def test_setup_creates_child_devices_and_updates_names(
    hass, core_server, monkeypatch
):
    port, _, _, snapshots = core_server
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain="zaparoo",
        source="user",
        title="Station",
        data={"host": "127.0.0.1", "port": port},
        options={},
        unique_id="station",
        discovery_keys=MappingProxyType({}),
        **(
            {"subentries_data": None}
            if "subentries_data" in signature(ConfigEntry).parameters
            else {}
        ),
    )
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda key: entry if key == entry.entry_id else None,
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    )
    if setup_registry := getattr(dr, "async_setup", None):
        setup_registry(hass)
    await dr.async_load(hass)
    registry = dr.async_get(hass)
    monkeypatch.setattr(
        integration, "async_get_loaded_integration", lambda *_args: None
    )
    assert await async_setup_entry(hass, entry)
    try:
        root = registry.async_get(entry.runtime_data.device_id)
        profile = next(
            device
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
            if ("zaparoo", profile_identifier(entry, "child")) in device.identifiers
        )
        assert profile.via_device_id == root.id
        snapshots["profiles"]["profiles"][0]["name"] = "Renamed"
        await entry.runtime_data.client.refresh()
        assert registry.async_get(profile.id).name == "Renamed"
        await hass.services.async_call(
            "zaparoo",
            "set_profile_limits",
            {"device_id": profile.id, "session_minutes": 30},
            blocking=True,
        )
        assert (
            entry.runtime_data.coordinator.data["profiles"][0]["sessionLimit"]
            == "1800s"
        )
    finally:
        assert await async_unload_entry(hass, entry)
