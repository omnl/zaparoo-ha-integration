"""Shared entity availability and device identity."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class ZaparooEntity(CoordinatorEntity):
    """A device becomes available after state synchronization."""

    _attr_has_entity_name = True

    def __init__(self, entry, coordinator, key, profile=None):
        super().__init__(coordinator)
        self.entry = entry
        self.profile = profile
        suffix = f"_{profile['profileId']}" if profile else ""
        self._attr_unique_id = f"{entry.entry_id}{suffix}_{key}"
        identifier = f"{entry.entry_id}{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            name=profile["name"] if profile else f"KidsStation ({entry.data['host']})",
            manufacturer="KidsStation / Zaparoo",
            via_device=(DOMAIN, entry.entry_id) if profile else None,
        )

    @property
    def available(self):
        return bool(self.coordinator.data.get("synchronized"))

    @property
    def extra_state_attributes(self):
        if not self.profile:
            return {}
        profile_id = self.profile["profileId"]
        return {
            "profile_id": profile_id,
            "person": self.entry.options.get("profile_options", {})
            .get(profile_id, {})
            .get("person"),
        }
