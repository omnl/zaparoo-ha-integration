"""Select the active profile directly through Zaparoo Core."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .profile import profile_person

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ZaparooCoordinator
    from .data import ZaparooDataConfigEntry

SHARED_PROFILE = "Shared (no profile)"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Required HA platform signature
    entry: ZaparooDataConfigEntry,
    add_entities: AddEntitiesCallback,
) -> None:
    """Add a profile selector on the root Core device."""
    add_entities([ZaparooProfileSelect(entry, entry.runtime_data.coordinator)])


class ZaparooProfileSelect(CoordinatorEntity, SelectEntity):
    """Switch by profile ID; PIN authorization remains with Core."""

    _attr_has_entity_name = True
    _attr_name = "Active profile"
    _attr_icon = "mdi:account-switch"

    def __init__(
        self, entry: ZaparooDataConfigEntry, coordinator: ZaparooCoordinator
    ) -> None:
        """Initialize the selector with a stable root-device identity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_profile_select"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    def _choices(self) -> dict[str, str | None]:
        profiles = self.coordinator.data["profiles"]
        counts = Counter(p["name"] for p in profiles)
        choices: dict[str, str | None] = {SHARED_PROFILE: None}
        for profile in profiles:
            name, profile_id = profile["name"], profile["profileId"]
            label = (
                name
                if counts[name] == 1 and name != SHARED_PROFILE
                else f"{name} [{profile_id}]"
            )
            # Names can themselves contain disambiguation suffixes.
            while label in choices:
                label = f"{label} [{profile_id}]"
            choices[label] = profile_id
        return choices

    @property
    def options(self) -> list[str]:
        """Use names where unique and IDs where needed for disambiguation."""
        return list(self._choices())

    @property
    def current_option(self) -> str | None:
        """Read actual Core state rather than predicting a successful switch."""
        profile_id = (self.coordinator.data.get("active_profile") or {}).get(
            "profileId"
        )
        return next(
            (label for label, pid in self._choices().items() if pid == profile_id), None
        )

    @property
    def available(self) -> bool:
        """Expose selection only when Core has synchronized profile data."""
        return bool(
            self.coordinator.data["connected"] and self.coordinator.data["profiles"]
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show the active ID and person; never expose switch tokens or PINs."""
        profile_id = (self.coordinator.data.get("active_profile") or {}).get(
            "profileId"
        )
        return {
            "profile_id": profile_id,
            "person": profile_person(self.entry, profile_id),
        }

    async def async_select_option(self, option: str) -> None:
        """Let Core enforce protected profiles; use the action to supply a PIN."""
        choices = self._choices()
        if option not in choices:
            msg = "The selected Zaparoo profile no longer exists"
            raise HomeAssistantError(msg)
        profile_id = choices[option]
        params = {"profileId": profile_id} if profile_id else {}
        await self.entry.runtime_data.client.send_jsonrpc("profiles.switch", params)
        await self.entry.runtime_data.client.refresh()
