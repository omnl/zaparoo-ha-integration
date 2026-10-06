"""Protocol and integration constants."""

DOMAIN = "zaparoo"
CONF_HOST, CONF_PORT = "host", "port"
DEFAULT_PORT, DEFAULT_AGENT_PORT = 7497, 7498
API_PATH = "/api/v0.1"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
EVENT_METHOD_MAP = {
    "media.started": "media_started",
    "media.stopped": "media_stopped",
    "media.indexing": "media_indexing",
    "readers.added": "reader_added",
    "readers.removed": "reader_removed",
    "tokens.added": "token_added",
    "tokens.removed": "token_removed",
    "tokens.staged": "token_staged",
    "tokens.staged.ready": "token_staged_ready",
    "profiles.active": "profile_changed",
    "profiles.data": "profile_data",
    "playtime.limit.warning": "playtime_warning",
    "playtime.limit.reached": "playtime_limit_reached",
    "playtime.extended": "playtime_extended",
    "run.failed": "run_failed",
}
TRIGGER_TYPES = list(EVENT_METHOD_MAP.values())
