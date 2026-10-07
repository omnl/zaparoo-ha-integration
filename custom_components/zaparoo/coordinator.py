"""Core state, reconnect snapshots and notification identity."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class ZaparooCoordinator(DataUpdateCoordinator):
    """Keep genuine events separate from snapshot refreshes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        """Initialize Core state without replaying historical events."""
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=None, config_entry=entry
        )
        self.data = {
            "connected": False,
            "synchronized": False,
            "media": None,
            "profiles": [],
            "active_profile": None,
            "playtime": {},
            "readers": {},
            "capabilities": [],
            "last_event_method": None,
            "last_event_params": None,
            "event_sequence": 0,
        }

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Apply RPC results without repeating historical notifications."""
        self.data["profiles"] = [
            {k: v for k, v in p.items() if k != "switchId"}
            for p in (snapshot.get("profiles") or {}).get("profiles", [])
        ]
        self.data["active_profile"] = self._safe_profile(
            snapshot.get("profiles.active")
        )
        media = snapshot.get("media") or {}
        self.data["media"] = next(
            (m for m in media.get("active", []) if m.get("slot") != "background"), None
        )
        self.data["playtime"] = snapshot.get("playtime") or {}
        self.data["capabilities"] = (snapshot.get("clients.current") or {}).get(
            "capabilities", []
        )
        readers = (snapshot.get("readers") or {}).get("readers", [])
        self.data["readers"] = {r["path"]: r for r in readers if "path" in r}
        self.async_set_updated_data(dict(self.data))

    @staticmethod
    def _safe_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
        return (
            {k: v for k, v in profile.items() if k != "switchId"} if profile else None
        )

    def handle_ws_event(self, method: str, params: dict[str, Any] | None) -> None:
        """Accept nullable parameters and current Core notification names."""
        payload = params if isinstance(params, dict) else {}
        if method == "profiles.active":
            profile = self._safe_profile(payload.get("profile"))
            old_id = (self.data.get("active_profile") or {}).get("profileId")
            new_id = (profile or {}).get("profileId")
            if old_id != new_id:
                self.data["playtime"] = {}
            self.data["active_profile"] = profile
            params = {**payload, "profile": profile}
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

    def connected(self) -> None:
        """Mark a fully synchronized connection available."""
        self.data.update(connected=True, synchronized=True)
        self.async_set_updated_data(dict(self.data))

    def disconnected(self) -> None:
        """Mark a disconnected connection unavailable."""
        self.data.update(connected=False, synchronized=False)
        self.async_set_updated_data(dict(self.data))
