"""Push state, authoritative snapshots, and weekly policy synchronization."""

from __future__ import annotations

import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZaparooCoordinator(DataUpdateCoordinator):
    """Keep genuine events separate from snapshot refreshes."""

    def __init__(self, hass, entry=None):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None, config_entry=entry)
        self.entry, self.client = entry, None
        self.data = {
            "connected": False,
            "synchronized": False,
            "media": None,
            "profiles": [],
            "active_profile": None,
            "playtime": {},
            "readers": {},
            "capabilities": [],
            "policies": {},
            "last_event_method": None,
            "last_event_params": None,
            "event_sequence": 0,
        }

    def set_snapshot(self, snapshot):
        """Apply RPC results without repeating historical notifications."""
        if "kidsstation.state" in snapshot:
            self._agent_state(snapshot["kidsstation.state"])
        self.data["profiles"] = [
            {k: v for k, v in p.items() if k != "switchId"}
            for p in (snapshot.get("profiles") or {}).get("profiles", [])
        ]
        self.data["active_profile"] = snapshot.get("profiles.active")
        media = snapshot.get("media") or {}
        self.data["media"] = next(
            (m for m in media.get("active", []) if m.get("slot") != "background"), None
        )
        self.data["playtime"] = snapshot.get("playtime") or {}
        self.data["capabilities"] = (snapshot.get("clients.current") or {}).get("capabilities", [])
        readers = (snapshot.get("readers") or {}).get("readers", [])
        self.data["readers"] = {r["path"]: r for r in readers if "path" in r}
        self.async_set_updated_data(dict(self.data))

    def _agent_state(self, state):
        state = state or {}
        self.data["policies"] = state.get("policies") or {}
        self.data["agent_connected"] = bool(state.get("synchronized"))
        if not state.get("synchronized"):
            self.data.update(connected=False, synchronized=False)
        else:
            self.data.update(connected=True, synchronized=True)
            core = {
                k: state[k]
                for k in (
                    "profiles",
                    "profiles.active",
                    "playtime",
                    "media",
                    "clients.current",
                    "readers",
                )
                if k in state
            }
            if core:
                self.set_snapshot(core)

    def handle_ws_event(self, method, params):
        """Accept nullable parameters and current Core notification names."""
        if method == "kidsstation.state":
            self._agent_state(params)
            self.async_set_updated_data(dict(self.data))
            return
        payload = params if isinstance(params, dict) else {}
        if method == "profiles.active":
            self.data["active_profile"] = payload.get("profile")
        elif method == "media.started":
            self.data["media"] = payload
        elif method == "media.stopped":
            self.data["media"] = None
        elif method == "media.indexing":
            self.data["indexing"] = payload
        elif method == "readers.added" and "path" in payload:
            self.data["readers"][payload["path"]] = payload
        elif method == "readers.removed":
            self.data["readers"].pop(payload.get("path"), None)
        elif method == "tokens.added":
            self.data["last_token"] = payload
        elif method == "tokens.removed":
            self.data["last_token"] = None
        self.data["last_event_method"], self.data["last_event_params"] = method, params
        self.data["event_sequence"] += 1
        self.async_set_updated_data(dict(self.data))

    def connected(self):
        self.data.update(connected=True, synchronized=True)
        self.async_set_updated_data(dict(self.data))

    def disconnected(self):
        self.data.update(connected=False, synchronized=False)
        self.async_set_updated_data(dict(self.data))

    async def push_policies(self):
        """Send complete plans; Batocera schedules without HA."""
        if not self.client or not self.client.agent or not self.entry:
            return
        for profile_id, options in self.entry.options.get("profile_options", {}).items():
            reply = await self.client.send_jsonrpc(
                "kidsstation.policy.set",
                {
                    "profileId": profile_id,
                    "enabled": options.get("enabled", True),
                    "timezone": self.hass.config.time_zone,
                    "minutes": options["minutes"],
                },
            )
            self.handle_ws_event("kidsstation.state", reply["result"])
