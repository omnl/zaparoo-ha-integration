"""Pure helpers for Core durations, categories, and local calendar days."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)")
_SECONDS = {
    "h": 3600,
    "m": 60,
    "s": 1,
    "ms": 0.001,
    "us": 0.000001,
    "µs": 0.000001,
    "μs": 0.000001,
    "ns": 0.000000001,
}


def duration_minutes(value):
    """Parse complete Go durations without treating malformed input as zero."""
    if not isinstance(value, str):
        return None
    if value in ("0", "0s"):
        return 0.0
    sign = -1 if value.startswith("-") else 1
    text = value.lstrip("+-")
    tokens = list(_DURATION.finditer(text))
    if not tokens or "".join(t.group(0) for t in tokens) != text:
        return None
    return round(sign * sum(float(t[1]) * _SECONDS[t[2]] for t in tokens) / 60, 2)


def media_category(media):
    """Classify media for display; v0.1 uses one combined time allowance."""
    media = media or {}
    system = media.get("systemId", "")
    if system in ("MusicTrack", "MusicAlbum", "MusicArtist", "Audio"):
        return "audio"
    if (
        system in ("Movie", "TVShow", "TVEpisode", "Video")
        or "kodi" in media.get("launcherId", "").lower()
    ):
        return "video"
    return "gaming" if system else "idle"


def profile_allowance(saved, profile_id, now=None):
    """Return today's configured limit and bonus in the agent policy timezone."""
    policy = saved.get("policies", {}).get(profile_id)
    if not policy or not policy.get("enabled"):
        return None, None
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(policy["timezone"]))
    bonus = saved.get("bonuses", {}).get(profile_id, {})
    minutes = bonus.get("minutes", 0) if bonus.get("date") == local.date().isoformat() else 0
    return policy["minutes"][local.weekday()] + minutes, minutes
