"""Core state and per-profile KidsStation allowances."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTime

from .entity import ZaparooEntity
from .models import duration_minutes, media_category, profile_allowance


async def async_setup_entry(hass, entry, add_entities):
    coordinator = entry.runtime_data.coordinator
    add_entities(
        [
            StateSensor(entry, coordinator, key)
            for key in (
                "events",
                "media",
                "active_profile",
                "daily_usage",
                "daily_remaining",
                "category",
            )
        ]
    )
    known = set()

    def add_profiles():
        entities = []
        for profile in coordinator.data["profiles"]:
            if profile["profileId"] not in known:
                known.add(profile["profileId"])
                entities.extend(
                    ProfileSensor(entry, coordinator, key, profile)
                    for key in ("daily_limit", "bonus")
                )
        if entities:
            add_entities(entities)

    add_profiles()
    entry.async_on_unload(coordinator.async_add_listener(add_profiles))


class StateSensor(ZaparooEntity, SensorEntity):
    """A current Core value; warning events do not replace playtime state."""

    def __init__(self, entry, coordinator, key):
        super().__init__(entry, coordinator, key)
        self.key = key
        self._attr_name = {
            "events": "Notification",
            "media": "Media",
            "active_profile": "Active profile",
            "daily_usage": "Media time today",
            "daily_remaining": "Daily time remaining",
            "category": "Media category",
        }[key]
        if key in ("daily_usage", "daily_remaining"):
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self):
        data = self.coordinator.data
        if self.key == "events":
            return data.get("last_event_method")
        if self.key == "active_profile":
            return (data.get("active_profile") or {}).get("name")
        if self.key == "media":
            return (data.get("media") or {}).get("mediaName")
        if self.key == "category":
            return media_category(data.get("media"))
        field = "dailyUsageToday" if self.key == "daily_usage" else "dailyRemaining"
        return duration_minutes(data["playtime"].get(field))

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if self.key == "events":
            return (
                data.get("last_event_params")
                if isinstance(data.get("last_event_params"), dict)
                else {}
            )
        if self.key == "media":
            return data.get("media") or {}
        if self.key == "active_profile":
            return data.get("active_profile") or {}
        return {"source": "zaparoo_playtime"} if self.key.startswith("daily_") else {}


class ProfileSensor(ZaparooEntity, SensorEntity):
    """Today's allowance or bonus for a profile linked to an HA person."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, entry, coordinator, key, profile):
        super().__init__(entry, coordinator, key, profile)
        self.key = key
        self._attr_name = "Daily allowance" if key == "daily_limit" else "Bonus today"

    @property
    def native_value(self):
        values = profile_allowance(self.coordinator.data["policies"], self.profile["profileId"])
        return values[0 if self.key == "daily_limit" else 1]
