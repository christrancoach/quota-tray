"""Provider plugin base class and shared parsing helpers."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

from ..models import AuthStale, Reading


class Provider:
    key: str = ""
    default_name: str = ""
    cli_name: str = ""  # used in "Open <CLI> to refresh"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = cfg.get("display_name") or self.default_name

    @property
    def stale_message(self) -> str:
        return f"Open {self.cli_name} to refresh"

    def fetch(self) -> Reading:  # pragma: no cover - interface
        raise NotImplementedError

    def read_json_file(self, path: Path) -> dict:
        """Read a CLI credential file read-only. Missing/unreadable counts as stale login."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError as exc:
            raise AuthStale(f"No credentials at {path}. {self.stale_message}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthStale(f"Can't read {path.name}. {self.stale_message}") from exc
        if not isinstance(data, dict):
            raise AuthStale(f"Unexpected {path.name} format. {self.stale_message}")
        return data


def parse_time(value) -> datetime | None:
    """Epoch seconds/ms, ISO-8601 (with or without Z), or None -> aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e11:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(value).strip()
        if not s:
            return None
        # Python 3.12 fromisoformat handles most forms; trim >6 fractional digits (Grok uses 7).
        s = s.replace("Z", "+00:00")
        if "." in s:
            head, _, rest = s.partition(".")
            frac = "".join(ch for ch in rest if ch.isdigit())
            tz = rest[len(frac):]
            s = f"{head}.{frac[:6]}{tz}"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verification (only to read exp / account id)."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
        return data if isinstance(data, dict) else {}
    except (IndexError, ValueError):
        return {}


def num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):  # Grok wraps numbers as {"val": n}
        return num(value.get("val"))
    return None


def clamp_pct(v: float | None) -> float | None:
    if v is None:
        return None
    return max(0.0, min(100.0, float(v)))


def missing_fields(data, expected: dict[str, str]) -> list[str]:
    """Shape self-check. expected maps dotted paths ("rate_limit.primary_window.used_percent", "[]" = first list
    item) to what the app uses them for; returns one warning per path that is absent."""
    out = []
    for path, meaning in expected.items():
        node = data
        for part in path.split("."):
            if part == "[]":
                node = node[0] if isinstance(node, list) and node else None
            else:
                node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if node is None:
            out.append(f"'{path}' is missing ({meaning})")
    return out
