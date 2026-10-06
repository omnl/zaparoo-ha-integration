"""Zaparoo notification sensor."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.zaparoo.const import DOMAIN
from custom_components.zaparoo.coordinator import ZaparooCoordinator

from .models import duration_minutes

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.zaparoo.data import ZaparooDataConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Reqired
    entry: ZaparooDataConfigEntry,
    add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor for this config entry."""
    coordinator = entry.runtime_data.coordinator
    host = entry.runtime_data.client.host
    add_entities(
        [
            ZaparooNotificationSensor(entry, coordinator, host),
            ZaparooConnectedSensor(entry, coordinator, host),
            ZaparooMediaSensor(entry, coordinator, host),
            ZaparooProfileSensor(entry, coordinator),
            ZaparooPlaytimeSensor(entry, coordinator, "dailyUsageToday"),
            ZaparooPlaytimeSensor(entry, coordinator, "dailyRemaining"),
        ]
    )


class ZaparooNotificationSensor(CoordinatorEntity, SensorEntity):
    """Sensor that displays the most recent Zaparoo notification."""

    _attr_icon = "mdi:satellite-uplink"
    _attr_native_unit_of_measurement = None

    def __init__(
        self,
        entry: ZaparooDataConfigEntry,
        coordinator: ZaparooCoordinator,
        host: str,
    ) -> None:
        """Init the sensor."""
        super().__init__(coordinator)

        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_events"
        self._attr_name = f"Zaparoo Notification ({host})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Zaparoo",
            manufacturer="Zaparoo",
        )

    @property
    def native_value(self) -> str:
        """Display the last notification type (e.g., 'media.started')."""
        return str(self.coordinator.data.get("last_event_method"))

    @property
    def extra_state_attributes(self) -> Any:
        """Expose event parameters."""
        return self.coordinator.data.get("last_event_params") or {}


class ZaparooConnectedSensor(CoordinatorEntity, SensorEntity):
    """Sensor that displays the most recent Zaparoo notification."""

    _attr_icon = "mdi:connection"
    _attr_native_unit_of_measurement = None

    def __init__(  # noqa: D107
        self,
        entry: ZaparooDataConfigEntry,
        coordinator: ZaparooCoordinator,
        host: str,
    ) -> None:
        super().__init__(coordinator)

        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_connected"
        self._attr_name = f"Zaparoo Connected ({host})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Zaparoo",
            manufacturer="Zaparoo",
        )

    @property
    def state(self) -> bool:
        """Display the last event type (e.g., 'media.started')."""
        return bool(self.coordinator.data.get("connected"))


class ZaparooMediaSensor(CoordinatorEntity[ZaparooCoordinator], SensorEntity):
    """Sensor that exposes currently playing media."""

    _attr_has_entity_name = True
    _attr_name = "Media"
    _attr_icon = "mdi:gamepad-variant"

    def __init__(
        self, entry: ZaparooDataConfigEntry, coordinator: ZaparooCoordinator, host: str
    ) -> None:
        """Initialize the Zaparoo media sensor."""
        super().__init__(coordinator)

        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_media"
        self._attr_name = f"Zaparoo Media ({host})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Zaparoo",
            manufacturer="Zaparoo",
        )

    @property
    def native_value(self) -> str | None:
        """Return the media name currently playing."""
        media = self.coordinator.data.get("media")
        if not media:
            return None
        return media.get("mediaName")

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return the full state of media."""
        media = self.coordinator.data.get("media")
        if not media:
            return None

        return media


class ZaparooProfileSensor(CoordinatorEntity, SensorEntity):
    """Expose the current Core profile without private switch tokens."""

    _attr_has_entity_name = True
    _attr_name = "Active profile"
    _attr_icon = "mdi:account"

    def __init__(
        self, entry: ZaparooDataConfigEntry, coordinator: ZaparooCoordinator
    ) -> None:
        """Initialize the profile sensor on the Core device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_active_profile"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def native_value(self) -> str | None:
        """Return the active profile's display name."""
        return (self.coordinator.data.get("active_profile") or {}).get("name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose safe profile IDs for explicit switching actions."""
        profile = self.coordinator.data.get("active_profile") or {}
        return {
            "profile_id": profile.get("profileId"),
            "profiles": self.coordinator.data.get("profiles", []),
        }


class ZaparooPlaytimeSensor(CoordinatorEntity, SensorEntity):
    """Display current Core accounting for the active profile."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(
        self, entry: ZaparooDataConfigEntry, coordinator: ZaparooCoordinator, field: str
    ) -> None:
        """Initialize a duration sensor on the Core device."""
        super().__init__(coordinator)
        self.field = field
        self._attr_unique_id = f"{entry.entry_id}_{field}"
        self._attr_name = (
            "Media time today" if field == "dailyUsageToday" else "Daily time remaining"
        )
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def native_value(self) -> float | None:
        """Return Core's duration without inventing zero for missing limits."""
        return duration_minutes(
            self.coordinator.data.get("playtime", {}).get(self.field)
        )
