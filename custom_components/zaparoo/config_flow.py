"""Tested connections and options for HA-person links and weekly schedules."""

import asyncio
import uuid

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_PATH, DEFAULT_AGENT_PORT, DEFAULT_PORT, DOMAIN, WEEKDAYS


async def validate_connection(session, data):
    """Require a successful RPC reply before saving a connection."""
    host = f"[{data['host']}]" if ":" in data["host"] else data["host"]
    headers = {"Authorization": f"Bearer {data['token']}"} if data.get("token") else {}
    async with (
        asyncio.timeout(10),
        session.ws_connect(f"ws://{host}:{data['port']}{API_PATH}", headers=headers) as ws,
    ):
        rpc_id = str(uuid.uuid4())
        await ws.send_json({"jsonrpc": "2.0", "id": rpc_id, "method": "clients.current"})
        while True:
            response = await ws.receive_json()
            if response.get("id") == rpc_id:
                if "error" in response or "result" not in response:
                    raise HomeAssistantError("Connection test failed")
                if not isinstance(response["result"], dict):
                    raise HomeAssistantError("Invalid client capabilities")
                if data.get("transport") == "agent" and "profiles.manage" not in response[
                    "result"
                ].get("capabilities", []):
                    raise HomeAssistantError("The agent cannot manage profiles")
                return response["result"]


class ZaparooConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self.transport = user_input["transport"]
            return await self.async_step_connection()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("transport", default="agent"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["agent", "direct"], translation_key="transport"
                        )
                    ),
                }
            ),
        )

    async def async_step_connection(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = {**user_input, "transport": self.transport, "host": user_input["host"].strip()}
            try:
                await validate_connection(async_get_clientsession(self.hass), data)
            except aiohttp.WSServerHandshakeError as err:
                errors["base"] = "invalid_auth" if err.status in (401, 403) else "cannot_connect"
            except (aiohttp.ClientError, TimeoutError, HomeAssistantError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{data['host']}:{data['port']}")
                self._abort_if_unique_id_configured()
                for entry in self._async_current_entries():
                    if (entry.data["host"], entry.data["port"]) == (data["host"], data["port"]):
                        return self.async_abort(reason="already_configured")
                return self.async_create_entry(title=f"KidsStation ({data['host']})", data=data)
        schema = {
            vol.Required("host"): str,
            vol.Required(
                "port", default=DEFAULT_AGENT_PORT if self.transport == "agent" else DEFAULT_PORT
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            (
                vol.Required("token")
                if self.transport == "agent"
                else vol.Optional("token", default="")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
        return self.async_show_form(
            step_id="connection", data_schema=vol.Schema(schema), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KidsStationOptionsFlow(config_entry)


class KidsStationOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry):
        self._entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            self.profile_id = user_input["profile_id"]
            return await self.async_step_profile()
        profiles = self._entry.runtime_data.coordinator.data["profiles"]
        if not profiles:
            return self.async_abort(reason="no_profiles")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("profile_id"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": p["profileId"], "label": p["name"]} for p in profiles
                            ]
                        )
                    ),
                }
            ),
        )

    async def async_step_profile(self, user_input=None):
        existing = self._entry.options.get("profile_options", {})
        current = existing.get(self.profile_id, {})
        agent = self._entry.runtime_data.client.agent
        errors = {}
        if user_input is not None:
            options = {"person": user_input.get("person")}
            if agent:
                options.update(
                    enabled=user_input["enabled"], minutes=[user_input[d] for d in WEEKDAYS]
                )
                try:
                    await self._entry.runtime_data.client.send_jsonrpc(
                        "kidsstation.policy.set",
                        {
                            "profileId": self.profile_id,
                            "timezone": self.hass.config.time_zone,
                            "enabled": options["enabled"],
                            "minutes": options["minutes"],
                        },
                    )
                except (HomeAssistantError, TimeoutError):
                    errors["base"] = "cannot_connect"
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        **self._entry.options,
                        "profile_options": {**existing, self.profile_id: options},
                    },
                )
        schema = {
            vol.Optional("person"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person")
            )
        }
        if agent:
            schema[vol.Required("enabled", default=current.get("enabled", True))] = bool
            values = current.get("minutes", [60, 60, 90, 60, 90, 120, 120])
            for i, day in enumerate(WEEKDAYS):
                schema[vol.Required(day, default=values[i])] = vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=1440)
                )
        return self.async_show_form(
            step_id="profile",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), {"person": current.get("person")}
            ),
            errors=errors,
        )
