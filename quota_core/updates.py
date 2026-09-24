"""Update check (config.json "update_url").

The URL is either GitHub's "latest release" API for the project's repository,
    https://api.github.com/repos/<owner>/<repo>/releases/latest
or a small manifest you publish anywhere:
    {"version": "1.3.0", "url": "https://example.com/QuotaTray-1.3.0.zip", "notes": "What changed"}
The app only tells you and opens the release page; it never downloads or runs anything itself.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass

from . import __version__

log = logging.getLogger(__name__)
PLACEHOLDER = "/OWNER/REPO/"


@dataclass
class Update:
    version: str
    url: str
    notes: str = ""


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


def is_configured(url: str | None) -> bool:
    return bool(url) and url.startswith("https://") and PLACEHOLDER not in url


def _normalize(data) -> dict | None:
    """GitHub release JSON or a manifest -> {"version", "url", "notes"}."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("tag_name"), str):   # GitHub releases API
        if data.get("draft") or data.get("prerelease"):
            return None
        return {"version": data["tag_name"], "url": data.get("html_url"), "notes": data.get("body")}
    return data if isinstance(data.get("version"), str) else None


def check(url: str | None, current: str = __version__, timeout: float = 15) -> Update | None:
    """Newer version from the feed, or None (off, placeholder, offline, malformed or not newer)."""
    if not is_configured(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"quota-tray/{current}",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _normalize(json.loads(r.read()))
    except Exception as exc:
        log.info("update check failed: %s", exc)
        return None
    if data is None or parse_version(data["version"]) <= parse_version(current):
        return None
    link = data.get("url") if isinstance(data.get("url"), str) and data["url"].startswith("https://") else url
    return Update(data["version"].lstrip("vV"), link, str(data.get("notes") or "")[:300])
