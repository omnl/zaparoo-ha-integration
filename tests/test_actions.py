"""Exercise HA's service registry and the actual options selector schemas."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaparoo.config_flow import KidsStationOptionsFlow
from custom_components.zaparoo.services import async_register_services, async_unregister_services


async def test_action_routes_person_to_selected_device(hass, monkeypatch):
    client = SimpleNamespace(
        agent=True, send_jsonrpc=AsyncMock(return_value={"result": {"synchronized": True}})
    )
    coordinator = SimpleNamespace(handle_ws_event=lambda *args: None)
    entry = SimpleNamespace(
        entry_id="entry",
        domain="zaparoo",
        options={"profile_options": {"child": {"person": "person.child"}}},
        runtime_data=SimpleNamespace(client=client, coordinator=coordinator),
    )
    hass.data["zaparoo"] = {"entry": entry}
    hass.config_entries = SimpleNamespace(async_get_entry=lambda _: entry)
    from custom_components.zaparoo import services

    monkeypatch.setattr(
        services.dr,
        "async_get",
        lambda _: SimpleNamespace(async_get=lambda _: SimpleNamespace(config_entries={"entry"})),
    )
    async_register_services(hass)
    await hass.services.async_call(
        "zaparoo",
        "add_time",
        {"device_id": "station", "person": "person.child", "minutes": 30},
        blocking=True,
    )
    method, payload = client.send_jsonrpc.call_args.args
    assert method == "kidsstation.add_time"
    assert payload["profileId"] == "child"
    assert payload["minutes"] == 30
    assert payload["requestId"]
    with pytest.raises(HomeAssistantError, match="exactly one"):
        await hass.services.async_call(
            "zaparoo",
            "add_time",
            {"device_id": "station", "person": "person.unknown", "minutes": 15},
            blocking=True,
        )
    assert client.send_jsonrpc.call_count == 1
    async_unregister_services(hass)
    assert not hass.services.has_service("zaparoo", "add_time")


async def test_person_options_preserve_other_children(hass):
    client = SimpleNamespace(agent=True, send_jsonrpc=AsyncMock(return_value={"result": {}}))
    entry = SimpleNamespace(
        options={"profile_options": {"other": {"person": "person.other", "minutes": [90] * 7}}},
        runtime_data=SimpleNamespace(client=client),
    )
    flow = KidsStationOptionsFlow(entry)
    flow.hass = hass
    flow.profile_id = "child"
    form = await flow.async_step_profile()
    values = form["data_schema"](
        {
            "person": "person.child",
            "enabled": True,
            "monday": 0,
            "tuesday": 60,
            "wednesday": 60,
            "thursday": 60,
            "friday": 60,
            "saturday": 120,
            "sunday": 120,
        }
    )
    result = await flow.async_step_profile(values)
    assert result["data"]["profile_options"]["other"]["minutes"] == [90] * 7
    assert result["data"]["profile_options"]["child"]["person"] == "person.child"
    assert client.send_jsonrpc.call_args.args[1]["minutes"][0] == 0
