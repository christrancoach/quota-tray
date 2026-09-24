"""Display rules shared by the tray icon, tooltip and panel. Pure functions, no UI toolkit."""
from __future__ import annotations

from datetime import datetime

from .models import STATUS_OK, Reading, utcnow

GREEN, AMBER, RED, NEUTRAL, GRAY = "#2EA043", "#D29922", "#E5534B", "#6E7681", "#8B949E"
TOOLTIP_MAX = 127  # Windows NOTIFYICONDATA szTip limit (128 incl. NUL)
SHORT_NAMES = {"ChatGPT (Codex)": "ChatGPT"}
RESET_SINCE_TEXT = "Reset since last read, likely near 0% used"


def color_for_left(left: float | None) -> str:
    if left is None:
        return GRAY
    if left > 50:
        return GREEN
    if left >= 20:
        return AMBER
    return RED


def reset_since_last_read(r: Reading | None, now: datetime | None = None) -> bool:
    """A non-fresh reading whose weekly reset has already passed: the number is meaningless now."""
    return bool(r and r.status != STATUS_OK and r.weekly_used_pct is not None and r.reset_passed(now))


def countdown(dt: datetime | None, now: datetime | None = None) -> str:
    if dt is None:
        return ""
    secs = int((dt - (now or utcnow())).total_seconds())
    if secs <= 0:
        return "now"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def ago(dt: datetime | None, now: datetime | None = None) -> str:
    if dt is None:
        return "never"
    secs = int(((now or utcnow()) - dt).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return dt.astimezone().strftime("%a %d %b %H:%M")


def icon_value(readings: list[Reading | None], now: datetime | None = None) -> float | None:
    """Lowest weekly remaining % across providers with a meaningful number."""
    vals = [r.weekly_left_pct for r in readings
            if r and r.weekly_left_pct is not None and not reset_since_last_read(r, now)]
    return min(vals) if vals else None


def tooltip_line(r: Reading, short_name: bool = False, with_reset: bool = True, now: datetime | None = None) -> str:
    name = SHORT_NAMES.get(r.name, r.name) if short_name else r.name
    if r.weekly_left_pct is None:
        return f"{name}: {'login needed' if r.status == 'stale' else 'no data'}"
    if reset_since_last_read(r, now):
        return f"{name} reset, likely ~full"
    stale = "" if r.status == STATUS_OK else " (old)"
    if not with_reset or not r.weekly_resets_at:
        return f"{name} {r.weekly_left_pct:.0f}% left{stale}"
    return f"{name} {r.weekly_left_pct:.0f}% weekly left, resets {r.weekly_resets_at.astimezone():%a %H:%M}{stale}"


def tooltip(readings: list[Reading], now: datetime | None = None) -> str:
    """One line per provider, shortened step by step to fit the Windows 127-char limit."""
    if not readings:
        return "Quota Tray"
    for short_name, with_reset, compact in ((False, True, False), (True, True, False), (True, True, True), (True, False, True)):
        lines = [tooltip_line(r, short_name, with_reset, now) for r in readings]
        if compact:
            lines = [ln.replace(" weekly left, resets ", " · ").replace(" left", "") for ln in lines]
        text = "\n".join(lines)
        if len(text) <= TOOLTIP_MAX:
            return text
    return text[:TOOLTIP_MAX]


def reset_badge(r) -> str | None:
    """Short marker for banked resets, e.g. '↻1', or None."""
    n = r.resets_left if r is not None else 0
    return f"↻{n}" if n else None


def reset_lines(r) -> list[str]:
    """One line per banked reset: '↻ 1 reset available: Full reset · usable now · use by Thu 22 Oct'."""
    from datetime import datetime
    out = []
    for g in (r.resets or []) if r is not None else []:
        left = int(g.get("left") or 0)
        if left <= 0:
            continue
        parts = [f"↻ {left} reset{'s' if left != 1 else ''} available: {g.get('label') or 'limit reset'}"]
        parts.append("usable now" if g.get("usable_now") else "usable when you hit a limit")
        if g.get("clears"):
            parts.append("clears " + ", ".join(g["clears"]))
        exp = g.get("expires_at")
        if exp:
            try:
                parts.append("use by " + datetime.fromisoformat(exp.replace("Z", "+00:00")).astimezone().strftime("%a %d %b"))
            except ValueError:
                pass
        out.append(" · ".join(parts))
    return out


KEEPALIVE_OUTCOMES = {
    "refreshed": "renewed the login",
    "not_refreshed": "ran, but the login didn't renew",
    "timeout": "timed out",
    "error": "couldn't start the CLI",
    "login_cleared": "found the login had ended",
}


def keepalive_line(ka: dict | None, now: float | None = None) -> tuple[str, bool] | None:
    """(text, show_on_card) for the last keepalive run, or None if it never ran. The card shows it when
    something went wrong or it ran in the last 12 hours; tooltips always include it."""
    import time
    from datetime import datetime
    if not ka or not ka.get("last_run"):
        return None
    if ka.get("dead"):
        return "Keepalive paused until you log in again", True
    outcome = ka.get("last_outcome") or "unknown"
    when = datetime.fromtimestamp(float(ka["last_run"])).strftime("%a %H:%M")
    text = f"Keepalive {KEEPALIVE_OUTCOMES.get(outcome, outcome)} {when}"
    if outcome == "refreshed" and ka.get("last_already_expired"):
        text += " (it had expired)"
    problem = outcome != "refreshed"
    age = (now or time.time()) - float(ka["last_run"])
    return text, problem or age < 12 * 3600


def spoken_summary(states) -> str:
    """Screen-reader summary of all providers, one sentence each."""
    parts = []
    for st in states:
        r = st.reading
        if r is None or r.weekly_left_pct is None:
            parts.append(f"{st.name}: no data yet.")
            continue
        s = f"{st.name}: {r.weekly_left_pct:.0f} percent of the weekly limit left"
        if r.weekly_resets_at:
            s += f", resets {r.weekly_resets_at.astimezone():%A %H:%M}"
        if r.status != "ok":
            s += ", data is out of date"
        if r.resets_left:
            s += f", {r.resets_left} banked reset{'s' if r.resets_left != 1 else ''} available"
        parts.append(s + ".")
    return " ".join(parts)
