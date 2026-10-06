"""Connection state is a binary sensor, including when offline."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import ZaparooEntity


async def async_setup_entry(hass, entry, add_entities):
    add_entities([ConnectionSensor(entry, entry.runtime_data.coordinator, "connected")])


class ConnectionSensor(ZaparooEntity, BinarySensorEntity):
    _attr_name = "Connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return bool(self.coordinator.data.get("synchronized"))
