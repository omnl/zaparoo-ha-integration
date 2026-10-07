"""Stable Core profile identity and shared profile entity discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ZaparooCoordinator
    from .data import ZaparooDataConfigEntry

CONF_PROFILE_PEOPLE = "profile_people"


def profile_identifier(entry: ZaparooDataConfigEntry, profile_id: str) -> str:
    """Scope a profile device to its Core instance."""
    return f"{entry.entry_id}_profile_{profile_id}"


def profile_person(entry: ZaparooDataConfigEntry, profile_id: str | None) -> str | None:
    """Return a local HA-person link without switching or changing Core."""
    return entry.options.get(CONF_PROFILE_PEOPLE, {}).get(profile_id)


def profile_parent_device(entry: ZaparooDataConfigEntry) -> dict[str, Any]:
    """Use the supported parent-link format on each Home Assistant version."""
    if "via_device_id" in DeviceInfo.__annotations__:
        return {"via_device_id": entry.runtime_data.device_id}
    return {"via_device": (DOMAIN, entry.entry_id)}


def find_profile(
    coordinator: ZaparooCoordinator, profile_id: str
) -> dict[str, Any] | None:
    """Look up the current profile rather than retaining stale metadata."""
    return next(
        (p for p in coordinator.data["profiles"] if p["profileId"] == profile_id), None
    )


def resolve_profile(entry: ZaparooDataConfigEntry, data: dict[str, Any]) -> str:
    """Resolve an explicit ID or one unambiguous person within this instance."""
    known = {p["profileId"] for p in entry.runtime_data.coordinator.data["profiles"]}
    if data.get("profile_id"):
        if data["profile_id"] in known:
            return data["profile_id"]
        msg = "The requested Zaparoo profile no longer exists"
        raise HomeAssistantError(msg)
    if data.get("person"):
        matches = [pid for pid in known if profile_person(entry, pid) == data["person"]]
        if len(matches) == 1:
            return matches[0]
        msg = "Choose a person linked to exactly one profile on this Zaparoo instance"
        raise HomeAssistantError(msg)
    msg = "Choose an existing profile_id or a linked HA person"
    raise HomeAssistantError(msg)


@callback
def async_add_profile_entities(
    entry: ZaparooDataConfigEntry,
    add_entities: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
) -> None:
    """Discover profiles after setup and reconnect without creating duplicates."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def add_profiles() -> None:
        entities = []
        for profile in coordinator.data["profiles"]:
            profile_id = profile["profileId"]
            if profile_id not in known:
                known.add(profile_id)
                entities.extend(factory(profile_id))
        if entities:
            add_entities(entities)

    add_profiles()
    entry.async_on_unload(coordinator.async_add_listener(add_profiles))


class ZaparooProfileEntity(CoordinatorEntity):
    """An entity on a Core profile device, with live identity and person metadata."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ZaparooDataConfigEntry,
        coordinator: ZaparooCoordinator,
        profile_id: str,
        key: str,
    ) -> None:
        """Initialize a stable entity ID independent of profile names."""
        super().__init__(coordinator)
        self.entry = entry
        self.profile_id = profile_id
        self._attr_unique_id = f"{profile_identifier(entry, profile_id)}_{key}"

    @property
    def profile(self) -> dict[str, Any] | None:
        """Return the current profile or none after removal."""
        return find_profile(self.coordinator, self.profile_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the profile to its parent Core device."""
        return DeviceInfo(
            identifiers={(DOMAIN, profile_identifier(self.entry, self.profile_id))},
            name=(self.profile or {}).get("name", self.profile_id),
            manufacturer="Zaparoo",
            **profile_parent_device(self.entry),
        )

    @property
    def available(self) -> bool:
        """Mark removed profiles and disconnected Core instances unavailable."""
        return bool(self.coordinator.data["connected"] and self.profile is not None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose safe identity for HA automations."""
        return {
            "profile_id": self.profile_id,
            "person": profile_person(self.entry, self.profile_id),
        }
