"""Minimal JSON-over-HTTPS helper that maps failures onto provider exceptions."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .models import AuthStale, ClientRefused, ProviderError, ShapeError
from .redact import redact

log = logging.getLogger(__name__)
TIMEOUT = 15


def request_json(url: str, headers: dict, *, method: str = "GET", body: bytes | None = None,
                 stale_message: str = "Credentials rejected") -> dict:
    req = urllib.request.Request(url, headers=headers, method=method, data=body)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code == 403 and "SUBSCRIPTION_REQUIRED" in detail:
            log.info("%s -> HTTP 403: %s", url, redact(detail))
            raise ClientRefused("Server refused this client (403 SUBSCRIPTION_REQUIRED)") from exc
        if exc.code in (401, 403):
            log.info("%s -> HTTP %s: %s", url, exc.code, redact(detail))
            raise AuthStale(stale_message) from exc
        raise ProviderError(f"HTTP {exc.code} from {url.split('?')[0]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Network error: {getattr(exc, 'reason', exc)}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ShapeError("Response was not JSON", raw) from exc
    if not isinstance(data, dict):
        raise ShapeError("Response was not a JSON object", raw)
    return data


def raw_json(data: dict) -> str:
    return json.dumps(data)[:20000]
