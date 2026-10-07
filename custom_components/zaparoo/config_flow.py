"""Config flow for zaparoo."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN
from .profile import CONF_PROFILE_PEOPLE

if TYPE_CHECKING:
    from .data import ZaparooDataConfigEntry

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def validate_connection(
    session: aiohttp.ClientSession, host: str, port: int
) -> None:
    """Require a successful Core media reply before saving a connection."""
    address = f"[{host}]" if ":" in host else host
    async with (
        asyncio.timeout(10),
        session.ws_connect(f"ws://{address}:{port}/api/v0.1") as ws,
    ):
        rpc_id = str(uuid.uuid4())
        await ws.send_json({"jsonrpc": "2.0", "id": rpc_id, "method": "media"})
        while True:
            response = await ws.receive_json()
            if response.get("id") == rpc_id:
                if "error" in response or not isinstance(response.get("result"), dict):
                    msg = "Core connection test failed"
                    raise HomeAssistantError(msg)
                return


class ZaparooConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zaparoo."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial UI step."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        host = user_input[CONF_HOST].strip()
        port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

        # No duplicates
        for entry in self._async_current_entries():
            if entry.data.get(CONF_HOST) == host and entry.data.get(CONF_PORT) == port:
                return self.async_abort(reason="already_configured")

        title = f"{host}:{port}"

        try:
            await validate_connection(async_get_clientsession(self.hass), host, port)
        except (aiohttp.ClientError, TimeoutError, HomeAssistantError, ValueError):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        return self.async_create_entry(
            title=title, data={CONF_HOST: host, CONF_PORT: port}
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ZaparooDataConfigEntry,
    ) -> ZaparooOptionsFlow:
        """Configure local person links independently of Core settings."""
        return ZaparooOptionsFlow(config_entry)


class ZaparooOptionsFlow(config_entries.OptionsFlow):
    """Link Core profiles to HA people without modifying or activating profiles."""

    def __init__(self, entry: ZaparooDataConfigEntry) -> None:
        """Keep a compatible entry reference on supported HA versions."""
        self._entry = entry
        self.profile_id: str | None = None

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Choose one of the current Core profiles."""
        runtime = getattr(self._entry, "runtime_data", None)
        if runtime is None or not runtime.coordinator.data["connected"]:
            return self.async_abort(reason="not_connected")
        profiles = runtime.coordinator.data["profiles"]
        if not profiles:
            return self.async_abort(reason="no_profiles")
        if user_input is not None:
            self.profile_id = user_input["profile_id"]
            if self.profile_id not in {p["profileId"] for p in profiles}:
                return self.async_abort(reason="profile_removed")
            return await self.async_step_person()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("profile_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": p["profileId"],
                                    "label": f"{p['name']} [{p['profileId']}]",
                                }
                                for p in profiles
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_person(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Add, replace or clear this profile's person link."""
        profiles = self._entry.runtime_data.coordinator.data["profiles"]
        if self.profile_id not in {p["profileId"] for p in profiles}:
            return self.async_abort(reason="profile_removed")
        people = dict(self._entry.options.get(CONF_PROFILE_PEOPLE, {}))
        errors = {}
        if user_input is not None:
            person = user_input.get("person")
            if person and (
                not person.startswith("person.") or self.hass.states.get(person) is None
            ):
                errors["base"] = "invalid_person"
            else:
                if person:
                    people[self.profile_id] = person
                else:
                    people.pop(self.profile_id, None)
                return self.async_create_entry(
                    title="", data={**self._entry.options, CONF_PROFILE_PEOPLE: people}
                )
        schema = vol.Schema(
            {
                vol.Optional("person"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person")
                )
            }
        )
        return self.async_show_form(
            step_id="person",
            errors=errors,
            description_placeholders={
                "profile": next(
                    p["name"] for p in profiles if p["profileId"] == self.profile_id
                )
            },
            data_schema=self.add_suggested_values_to_schema(
                schema, {"person": people.get(self.profile_id)}
            ),
        )
