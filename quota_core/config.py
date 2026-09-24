"""App data paths and config.json handling."""
from __future__ import annotations

import copy
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .redact import RedactingFilter

APP_NAME = "quota-tray"
MIN_POLL_SECONDS = 300  # hard floor: never poll a provider more than once per 5 minutes
# Named mutex held by whichever front end is currently the poller. Windows releases it when
# the owning process exits, so a surviving front end can take over.
LEADER_MUTEX = r"Local\QuotaTrayPollerLeader"
LEADER_RETRY_SECONDS = 10  # followers retry leadership this often (hand-off well inside 60 s)

# The project's GitHub releases feed (only the latest non-draft, non-prerelease release is considered).
DEFAULT_UPDATE_URL = "https://api.github.com/repos/christrancoach/quota-tray/releases/latest"

DEFAULT_CONFIG: dict = {
    "refresh_interval_minutes": 10,
    "thresholds": [20, 10],   # weekly % left that triggers an alert (once per reset cycle)
    "alerts": {
        "short_thresholds": [],          # 5-hour window % left, e.g. [20]; empty = off
        "resets_soon_hours": 3,          # low weekly pool resets within this many hours; 0 = off
        "reset_back": True,              # tell me when a weekly pool that ran low has reset
        "banked_reset_hint": True,       # remind me of a usable banked reset when I'm low
        "quiet_hours": {"enabled": False, "start": "22:00", "end": "08:00"},
    },
    "providers": {
        "claude": {"enabled": True, "display_name": "Claude", "credentials_path": None,
                   # Unofficial, opt-in: banked resets are only returned to Claude Code's User-Agent.
                   "reset_info": False},
        "codex": {"enabled": True, "display_name": "ChatGPT (Codex)", "auth_path": None, "sessions_dir": None},
        "grok": {"enabled": True, "display_name": "Grok", "auth_path": None},
    },
    # Update check: a GitHub "latest release" API URL, or a manifest you publish ({"version", "url", "notes"}).
    # Empty or null = off.
    "update_url": DEFAULT_UPDATE_URL,
    # Token keepalive: let the CLI refresh its own login shortly before/after it expires.
    # "cli_path" overrides where the executable is found (default: PATH lookup).
    "keepalive": {
        "claude": {"enabled": True, "cli_path": None},
        "grok": {"enabled": True, "cli_path": None},
    },
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_dir() / "config.json"


def history_path() -> Path:
    return app_dir() / "history.csv"


def state_path() -> Path:
    return app_dir() / "state.json"


def refresh_request_path() -> Path:
    """Followers touch this file to ask the poller for a refresh."""
    return app_dir() / "refresh.request"


def widget_settings_path() -> Path:
    return app_dir() / "widget.json"


def debug_log_path() -> Path:
    return app_dir() / "debug.log"


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    """Load config.json merged over defaults. Writes the defaults on first run."""
    path = config_path()
    user: dict = {}
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).warning("config.json unreadable, using defaults: %s", exc)
    else:
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return _merge(DEFAULT_CONFIG, user)


def save_setting(keys: list[str], value) -> None:
    """Set one nested value in config.json (e.g. ["keepalive", "claude", "enabled"]), keeping other edits."""
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else copy.deepcopy(DEFAULT_CONFIG)
        node = data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("could not save %s to config.json: %s", ".".join(keys), exc)


def save_provider_setting(provider: str, field: str, value) -> None:
    """Update one providers.<provider>.<field> in config.json."""
    save_setting(["providers", provider, field], value)


def keepalive_enabled(cli: str) -> bool:
    """Read fresh from disk: a toggle flipped in either front end reaches the poller process."""
    return bool(load_config().get("keepalive", {}).get(cli, {}).get("enabled", True))


def poll_interval_seconds(cfg: dict) -> int:
    try:
        minutes = float(cfg.get("refresh_interval_minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    return max(MIN_POLL_SECONDS, int(minutes * 60))


def setup_logging() -> None:
    handler = RotatingFileHandler(debug_log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
