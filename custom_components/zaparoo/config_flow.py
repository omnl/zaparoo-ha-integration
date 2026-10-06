"""Config flow for zaparoo."""

import asyncio
import uuid

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN

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
