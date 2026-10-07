"""Connection diagnostic that remains readable while Core is offline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .profile import ZaparooProfileEntity, async_add_profile_entities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import ZaparooCoordinator
    from .data import ZaparooDataConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Required HA platform signature
    entry: ZaparooDataConfigEntry,
    add_entities: AddEntitiesCallback,
) -> None:
    """Add the connection diagnostic."""
    coordinator = entry.runtime_data.coordinator
    add_entities([ZaparooConnectionSensor(entry, coordinator)])
    async_add_profile_entities(
        entry,
        add_entities,
        lambda profile_id: [ZaparooProfileActiveSensor(entry, coordinator, profile_id)],
    )


class ZaparooConnectionSensor(CoordinatorEntity, BinarySensorEntity):
    """Report loss of Core connectivity as off."""

    _attr_has_entity_name = True
    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_available = True

    def __init__(
        self, entry: ZaparooDataConfigEntry, coordinator: ZaparooCoordinator
    ) -> None:
        """Initialize a diagnostic on the root device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool:
        """Return whether the complete Core snapshot is available."""
        return bool(self.coordinator.data.get("connected"))


class ZaparooProfileActiveSensor(ZaparooProfileEntity, BinarySensorEntity):
    """Report whether this Core profile is active."""

    _attr_name = "Active"
    _attr_icon = "mdi:account-check"

    def __init__(
        self,
        entry: ZaparooDataConfigEntry,
        coordinator: ZaparooCoordinator,
        profile_id: str,
    ) -> None:
        """Initialize an active diagnostic on a profile device."""
        super().__init__(entry, coordinator, profile_id, "active")

    @property
    def is_on(self) -> bool:
        """Compare stable IDs rather than profile names or HA presence."""
        return (self.coordinator.data.get("active_profile") or {}).get(
            "profileId"
        ) == self.profile_id
