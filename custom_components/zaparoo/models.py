"""Parse Core's Go duration values for HA duration sensors."""

from __future__ import annotations

import re

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


def duration_minutes(value: str | None) -> float | None:
    """Return minutes, or unknown for incomplete or invalid durations."""
    if not isinstance(value, str):
        return None
    if value in ("0", "0s"):
        return 0.0
    sign = -1 if value.startswith("-") else 1
    text = value.removeprefix("-").removeprefix("+")
    tokens = list(_DURATION.finditer(text))
    if not tokens or "".join(t.group(0) for t in tokens) != text:
        return None
    return round(sign * sum(float(t[1]) * _SECONDS[t[2]] for t in tokens) / 60, 2)
