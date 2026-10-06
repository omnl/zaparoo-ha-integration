"""Calendar semantics, person resolution, and notification identity."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaparoo.coordinator import ZaparooCoordinator
from custom_components.zaparoo.event import ZaparooEventEntity
from custom_components.zaparoo.models import duration_minutes, media_category, profile_allowance
from custom_components.zaparoo.services import _recipient


@pytest.mark.parametrize(
    ("value", "minutes"),
    [("1h30m0s", 90), ("4m58s", 4.97), ("-1m", -1), ("0", 0), (None, None), ("1hBAD", None)],
)
def test_duration(value, minutes):
    assert duration_minutes(value) == minutes


def test_bonus_expires_at_zurich_midnight():
    saved = {
        "policies": {"child": {"enabled": True, "timezone": "Europe/Zurich", "minutes": [60] * 7}},
        "bonuses": {"child": {"date": "2026-10-06", "minutes": 30}},
    }
    assert profile_allowance(saved, "child", datetime(2026, 10, 6, 21, 59, tzinfo=UTC)) == (90, 30)
    assert profile_allowance(saved, "child", datetime(2026, 10, 6, 22, 0, tzinfo=UTC)) == (60, 0)


def test_person_resolution_never_guesses():
    entry = SimpleNamespace(
        options={
            "profile_options": {"a": {"person": "person.child"}, "b": {"person": "person.child"}}
        }
    )
    with pytest.raises(HomeAssistantError, match="exactly one"):
        _recipient(entry, {"person": "person.child"})
    assert _recipient(entry, {"profile_id": "a"}) == "a"


async def test_warning_does_not_replace_usage_or_repeat_event(hass):
    coordinator = ZaparooCoordinator(hass)
    coordinator.data["playtime"] = {"dailyUsageToday": "30m"}
    entry = SimpleNamespace(entry_id="entry")
    event = ZaparooEventEntity(entry, coordinator)
    event._trigger_event = Mock()
    event.async_write_ha_state = Mock()
    coordinator.handle_ws_event("playtime.limit.warning", {"remaining": "10m"})
    event._handle_coordinator_update()
    event._handle_coordinator_update()
    coordinator.connected()
    event._handle_coordinator_update()
    assert coordinator.data["playtime"]["dailyUsageToday"] == "30m"
    assert event._trigger_event.call_count == 1
    coordinator.handle_ws_event("profiles.active", {"profile": None})
    assert coordinator.data["active_profile"] is None


def test_kodi_and_console_categories():
    assert media_category({"systemId": "TVShow", "launcherId": "Kodi"}) == "video"
    assert media_category({"systemId": "SNES"}) == "gaming"
    assert media_category({"systemId": "MusicTrack", "launcherId": "Kodi"}) == "audio"
