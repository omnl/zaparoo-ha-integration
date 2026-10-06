"""Per-child bonus buttons use stable Zaparoo profile IDs."""

import uuid

from homeassistant.components.button import ButtonEntity

from .entity import ZaparooEntity
from .models import profile_allowance


async def async_setup_entry(hass, entry, add_entities):
    if not entry.runtime_data.client.agent:
        return
    coordinator = entry.runtime_data.coordinator
    known = set()

    def add_profiles():
        entities = []
        for profile in coordinator.data["profiles"]:
            if profile["profileId"] not in known:
                known.add(profile["profileId"])
                entities.extend(BonusButton(entry, coordinator, profile, n) for n in (15, 30, 60))
        if entities:
            add_entities(entities)

    add_profiles()
    entry.async_on_unload(coordinator.async_add_listener(add_profiles))


class BonusButton(ZaparooEntity, ButtonEntity):
    _attr_icon = "mdi:timer-plus-outline"

    def __init__(self, entry, coordinator, profile, minutes):
        super().__init__(entry, coordinator, f"bonus_{minutes}", profile)
        self.minutes = minutes
        self._attr_name = f"Add {minutes} minutes"

    @property
    def available(self):
        return (
            super().available
            and profile_allowance(self.coordinator.data["policies"], self.profile["profileId"])[0]
            is not None
        )

    async def async_press(self):
        response = await self.entry.runtime_data.client.send_jsonrpc(
            "kidsstation.add_time",
            {
                "profileId": self.profile["profileId"],
                "minutes": self.minutes,
                "requestId": str(uuid.uuid4()),
            },
        )
        self.coordinator.handle_ws_event("kidsstation.state", response["result"])
