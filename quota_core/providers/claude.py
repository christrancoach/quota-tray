"""Claude (Claude Code OAuth credentials -> api.anthropic.com/api/oauth/usage)."""
from __future__ import annotations

import os
from pathlib import Path

import logging

from ..http import raw_json, request_json
from ..models import AuthStale, ProviderError, Reading, ShapeError, utcnow
from .base import Provider, clamp_pct, missing_fields, parse_time

log = logging.getLogger(__name__)
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# Claude Code's own "at the limit" variant of the usage call. It also returns the banked resets
# (cedar_ember) and the weekly session-limit reset (juniper_tide), but only to the Claude Code
# client: any other user agent gets "ineligible: surface".
RESETS_URL = USAGE_URL + "?at_wall=1&skip_spend=1"
CLAUDE_CODE_UA = "claude-cli/2.1.280 (external, cli)"
CLEARS_LABELS = {"five_hour": "5-hour", "seven_day": "weekly", "seven_day_overage_included": "weekly overage"}


def default_credentials_path() -> Path:
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(cfg_dir) if cfg_dir else Path.home() / ".claude") / ".credentials.json"


def _scope_label(scope) -> str:
    """Name a scoped limit by its model (e.g. 'Fable') or surface."""
    if not isinstance(scope, dict):
        return "Scoped"
    for part in ("model", "surface"):
        obj = scope.get(part)
        if isinstance(obj, dict) and (obj.get("display_name") or obj.get("id")):
            return str(obj.get("display_name") or obj.get("id"))
        if isinstance(obj, str) and obj:
            return obj
    return "Scoped"


SHAPE = {"five_hour.utilization": "5-hour window", "seven_day.resets_at": "weekly reset time",
         "limits": "per-model weekly limits", "seven_day_breakdown.rows": "Claude Code / Chats split"}


def shape_warnings(data: dict) -> list[str]:
    return missing_fields(data, SHAPE)


def parse_resets(data: dict) -> list[dict]:
    """Banked resets and the weekly session reset, in the Reading.resets shape. Never redeems anything."""
    out = []
    ce = data.get("cedar_ember") if isinstance(data.get("cedar_ember"), dict) else {}
    for g in ce.get("grants") or []:
        if not isinstance(g, dict) or not isinstance(g.get("resets_left"), int) or g["resets_left"] <= 0:
            continue
        out.append({"label": g.get("label") or "Usage-limit reset", "left": g["resets_left"],
                    "expires_at": g.get("ends_at"), "usable_now": bool(g.get("usable_now")) and not g.get("paused"),
                    "clears": [CLEARS_LABELS.get(c, c) for c in g.get("clears") or []], "kind": "banked"})
    jt = data.get("juniper_tide") if isinstance(data.get("juniper_tide"), dict) else {}
    if jt.get("eligible") and jt.get("available"):
        out.append({"label": "Weekly session-limit reset", "left": 1, "expires_at": jt.get("weekly_resets_at"),
                    "usable_now": True, "clears": ["5-hour"], "kind": "session"})
    return out


def parse_usage(data: dict, name: str = "Claude") -> Reading:
    five = data.get("five_hour") if isinstance(data.get("five_hour"), dict) else None
    seven = data.get("seven_day") if isinstance(data.get("seven_day"), dict) else None
    limits = [x for x in data.get("limits") or [] if isinstance(x, dict)]

    weekly_used = weekly_reset = short_used = short_reset = None
    if seven and seven.get("utilization") is not None:
        weekly_used, weekly_reset = seven.get("utilization"), parse_time(seven.get("resets_at"))
    if five and five.get("utilization") is not None:
        short_used, short_reset = five.get("utilization"), parse_time(five.get("resets_at"))

    breakdown: dict[str, float] = {}
    for lim in limits:
        kind, pct = lim.get("kind"), lim.get("percent")
        if pct is None:
            continue
        if kind == "weekly_all" and weekly_used is None:
            weekly_used, weekly_reset = pct, parse_time(lim.get("resets_at"))
        elif kind == "session" and short_used is None:
            short_used, short_reset = pct, parse_time(lim.get("resets_at"))
        elif kind == "weekly_scoped":
            breakdown[f"{_scope_label(lim.get('scope'))} weekly"] = clamp_pct(pct)
    # Older response shape: explicit per-model weekly windows.
    for key, label in (("seven_day_opus", "Opus"), ("seven_day_sonnet", "Sonnet")):
        win = data.get(key)
        if isinstance(win, dict) and win.get("utilization") is not None and f"{label} weekly" not in breakdown:
            breakdown[f"{label} weekly"] = clamp_pct(win["utilization"])

    if weekly_used is None:
        raise ShapeError("No weekly window in Claude usage response", raw_json(data))

    detail = None
    rows = (data.get("seven_day_breakdown") or {}).get("rows") if isinstance(data.get("seven_day_breakdown"), dict) else None
    if isinstance(rows, list):
        detail = {r.get("display_name") or r.get("key"): r.get("percent") for r in rows
                  if isinstance(r, dict) and r.get("percent") is not None}

    return Reading(
        name=name,
        weekly_used_pct=clamp_pct(weekly_used),
        weekly_resets_at=weekly_reset,
        short_used_pct=clamp_pct(short_used),
        short_resets_at=short_reset,
        breakdown=breakdown or None,
        detail=detail or None,
        last_updated=utcnow(),
    )


class ClaudeProvider(Provider):
    key = "claude"
    default_name = "Claude"
    cli_name = "Claude Code"

    def fetch(self) -> Reading:
        path = Path(self.cfg["credentials_path"]) if self.cfg.get("credentials_path") else default_credentials_path()
        oauth = self.read_json_file(path).get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token and oauth.get("expiresAt") == 0:
            # Claude Code clears the login this way after the auth server rejects its refresh token.
            raise AuthStale("Claude Code login ended. Open Claude Code to log in")
        if not token:
            raise AuthStale(f"No Claude login found. {self.stale_message}")
        expires = parse_time(oauth.get("expiresAt"))
        if expires and expires <= utcnow():
            raise AuthStale(f"Claude Code token expired. {self.stale_message}")
        data = request_json(USAGE_URL, {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "quota-tray/1.0",
        }, stale_message=f"Claude rejected the token. {self.stale_message}")
        reading = parse_usage(data, self.name)
        reading.warnings = shape_warnings(data) or None
        sub = oauth.get("subscriptionType")
        reading.plan = sub.capitalize() if isinstance(sub, str) else None
        if self.cfg.get("reset_info", False):   # opt-in: presents as Claude Code (see config.py)
            try:  # best effort: a failure here never affects the usage reading
                extra = request_json(RESETS_URL, {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                                                  "User-Agent": self.cfg.get("reset_user_agent") or CLAUDE_CODE_UA},
                                     stale_message="reset info rejected")
                reading.resets = parse_resets(extra)
            except ProviderError as exc:
                log.info("claude reset info unavailable: %s", exc)
        del token
        return reading
