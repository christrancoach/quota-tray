"""ChatGPT (Codex CLI credentials -> chatgpt.com/backend-api/wham/usage).

Windows are classified by length (limit_window_seconds), never by primary/secondary slot.
If the API fails, the latest token_count event in the Codex session logs is used instead.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from ..http import raw_json, request_json
from ..models import SOURCE_LOG, AuthStale, ProviderError, Reading, ShapeError, classify_window, utcnow
from .base import Provider, clamp_pct, jwt_claims, missing_fields, parse_time

log = logging.getLogger(__name__)
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
LOG_MAX_AGE = timedelta(days=8)


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else Path.home() / ".codex"


def _pick(*values):
    for v in values:
        if isinstance(v, str) and v:
            return v
    return None


def read_auth(data: dict) -> tuple[str, str | None]:
    tokens = data.get("tokens") or {}
    token = _pick(tokens.get("access_token"), tokens.get("accessToken"))
    if not token:
        raise AuthStale("No ChatGPT login in Codex auth.json")
    account = _pick(tokens.get("account_id"), tokens.get("accountId"))
    if not account:
        for jwt in (tokens.get("id_token"), tokens.get("idToken"), token):
            if isinstance(jwt, str):
                auth = jwt_claims(jwt).get("https://api.openai.com/auth") or {}
                account = _pick(auth.get("chatgpt_account_id"))
                if account:
                    break
    return token, account


SHAPE = {"rate_limit.primary_window.limit_window_seconds": "window length (how weekly vs 5-hour is told apart)",
         "rate_limit.primary_window.reset_at": "reset time", "plan_type": "plan name",
         "rate_limit_reset_credits": "banked resets"}


def shape_warnings(data: dict) -> list[str]:
    return missing_fields(data, SHAPE)


def parse_reset_credits(credits: dict, usage: dict) -> list[dict]:
    """Available banked resets. 'Usable now' comes from the usage call's applicable count."""
    applicable = ((usage.get("rate_limit_reset_credits") or {}).get("applicable_available_count") or 0) > 0
    grouped: dict[tuple, dict] = {}
    for c in credits.get("credits") or []:
        if not isinstance(c, dict) or c.get("status") != "available":
            continue
        key = (c.get("title") or "Rate-limit reset", c.get("expires_at"))
        g = grouped.setdefault(key, {"label": key[0], "left": 0, "expires_at": key[1], "usable_now": applicable,
                                     "clears": ["weekly"], "kind": "banked"})
        g["left"] += 1
    return sorted(grouped.values(), key=lambda g: g["expires_at"] or "")


def plan_label(plan) -> str | None:
    if not isinstance(plan, str) or not plan:
        return None
    return {"prolite": "Pro Lite"}.get(plan, plan.capitalize())


def _windows_to_reading(windows: list[tuple[float | None, float | None, object]], name: str) -> tuple:
    """windows: (length_seconds, used_percent, reset_value). Returns weekly/short tuples."""
    weekly = short = None
    for seconds, used, reset in windows:
        kind = classify_window(seconds)
        if kind == "weekly" and weekly is None:
            weekly = (clamp_pct(used), parse_time(reset))
        elif kind == "short" and short is None:
            short = (clamp_pct(used), parse_time(reset))
    return weekly, short


def parse_usage(data: dict, name: str = "ChatGPT (Codex)") -> Reading:
    rl = data.get("rate_limit")
    if not isinstance(rl, dict):
        raise ShapeError("No rate_limit object in Codex usage response", raw_json(data))
    windows = []
    for slot in ("primary_window", "secondary_window"):
        w = rl.get(slot)
        if isinstance(w, dict):
            windows.append((w.get("limit_window_seconds"), w.get("used_percent"), w.get("reset_at")))
    weekly, short = _windows_to_reading(windows, name)
    if weekly is None:
        raise ShapeError("No weekly-length window in Codex usage response", raw_json(data))
    return Reading(
        name=name,
        weekly_used_pct=weekly[0], weekly_resets_at=weekly[1],
        short_used_pct=short[0] if short else None, short_resets_at=short[1] if short else None,
        plan=plan_label(data.get("plan_type")),
        last_updated=utcnow(),
    )


def parse_rate_limits_event(rate_limits: dict, name: str) -> tuple | None:
    windows = []
    for slot in ("primary", "secondary"):
        w = rate_limits.get(slot)
        if isinstance(w, dict) and w.get("window_minutes"):
            windows.append((float(w["window_minutes"]) * 60, w.get("used_percent"), w.get("resets_at")))
    weekly, short = _windows_to_reading(windows, name)
    return (weekly, short) if weekly else None


def read_latest_log(sessions_dir: Path, name: str) -> Reading | None:
    """Newest token_count event with rate_limits across recent rollout-*.jsonl files."""
    if not sessions_dir.is_dir():
        return None
    cutoff = utcnow() - LOG_MAX_AGE
    files = sorted(sessions_dir.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"token_count"' not in line or '"rate_limits"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") or {}
            rate_limits = payload.get("rate_limits")
            if payload.get("type") != "token_count" or not isinstance(rate_limits, dict):
                continue
            when = parse_time(event.get("timestamp"))
            if when is None or when < cutoff:
                return None
            parsed = parse_rate_limits_event(rate_limits, name)
            if not parsed:
                continue
            (w_used, w_reset), short = parsed
            return Reading(
                name=name, weekly_used_pct=w_used, weekly_resets_at=w_reset,
                short_used_pct=short[0] if short else None, short_resets_at=short[1] if short else None,
                source=SOURCE_LOG, last_updated=when,
                plan=plan_label(rate_limits.get("plan_type")),
            )
    return None


class CodexProvider(Provider):
    key = "codex"
    default_name = "ChatGPT (Codex)"
    cli_name = "Codex CLI"

    def fetch(self) -> Reading:
        try:
            return self._fetch_api()
        except ProviderError as exc:
            sessions = Path(self.cfg["sessions_dir"]) if self.cfg.get("sessions_dir") else codex_home() / "sessions"
            fallback = read_latest_log(sessions, self.name)
            if fallback is None:
                raise
            log.info("codex API failed (%s); using session log from %s", exc, fallback.last_updated)
            fallback.error = f"API unavailable ({exc}); using Codex session log"
            return fallback

    def _fetch_api(self) -> Reading:
        path = Path(self.cfg["auth_path"]) if self.cfg.get("auth_path") else codex_home() / "auth.json"
        token, account = read_auth(self.read_json_file(path))
        exp = jwt_claims(token).get("exp")
        if isinstance(exp, (int, float)) and parse_time(exp) <= utcnow():
            raise AuthStale(f"Codex token expired. {self.stale_message}")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "quota-tray/1.0"}
        if account:
            headers["ChatGPT-Account-Id"] = account
        data = request_json(USAGE_URL, headers, stale_message=f"ChatGPT rejected the token. {self.stale_message}")
        reading = parse_usage(data, self.name)
        reading.warnings = shape_warnings(data) or None
        if self.cfg.get("reset_info", True):
            try:  # best effort: never affects the usage reading
                credits = request_json(RESET_CREDITS_URL, headers, stale_message="reset credits rejected")
                reading.resets = parse_reset_credits(credits, data)
            except ProviderError as exc:
                log.info("codex reset credits unavailable: %s", exc)
        del token, headers
        return reading
