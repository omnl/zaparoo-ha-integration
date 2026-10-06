"""Runtime data owned by one Zaparoo config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from homeassistant.loader import Integration

    from .coordinator import ZaparooCoordinator
    from .websocket_client import ZaparooWebSocket


@dataclass
class ZaparooData:
    """Connection, coordinator, and loaded integration."""

    client: ZaparooWebSocket
    coordinator: ZaparooCoordinator
    integration: Integration


type ZaparooDataConfigEntry = ConfigEntry[ZaparooData]
