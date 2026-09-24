"""Burn-rate forecast from history.csv: at the current pace, will a weekly pool run out before it resets?"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import history_path
from .models import Reading, utcnow

WINDOW = timedelta(hours=72)        # fit on recent readings so the pace reflects current behavior
MIN_SPAN = timedelta(hours=1)
TAIL_BYTES = 400_000                # history only grows; read just the recent end


@dataclass
class Forecast:
    rate_per_hour: float                  # weekly % used per hour
    runs_out_at: datetime | None          # set when the pool empties before it resets
    left_at_reset: float | None           # % left at reset otherwise

    def text(self) -> str:
        if self.runs_out_at is not None:
            return f"At this pace: runs out {self.runs_out_at.astimezone():%a %H:%M}, before the reset"
        if self.left_at_reset is not None:
            return f"On pace to finish the week with ~{self.left_at_reset:.0f}% left"
        return ""

    @property
    def runs_out(self) -> bool:
        return self.runs_out_at is not None

    @property
    def worrying(self) -> bool:
        return self.runs_out or (self.left_at_reset is not None and self.left_at_reset < 10)


_cache: dict = {"mtime": None, "rows": []}


def _rows(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except OSError:
        return []
    if _cache["mtime"] == (st.st_mtime_ns, st.st_size):
        return _cache["rows"]
    with open(path, "rb") as fh:
        start = max(0, st.st_size - TAIL_BYTES)
        fh.seek(start)
        data = fh.read().decode("utf-8", "replace")
    if start:
        data = data.split("\n", 1)[1] if "\n" in data else ""
        header = "timestamp,provider,weekly_used_pct,weekly_resets_at,short_used_pct,short_resets_at,source\n"
        data = header + data
    rows = list(csv.DictReader(io.StringIO(data)))
    _cache.update(mtime=(st.st_mtime_ns, st.st_size), rows=rows)
    return rows


def forecast(key: str, reading: Reading | None, path: Path | None = None, now: datetime | None = None) -> Forecast | None:
    """None when there isn't enough recent history in the current cycle to say anything useful."""
    if reading is None or reading.weekly_used_pct is None or reading.weekly_resets_at is None:
        return None
    now = now or utcnow()
    reset = reading.weekly_resets_at
    if reset <= now or reading.weekly_used_pct >= 100:   # nothing left to forecast
        return None
    pts = []
    for r in _rows(path or history_path()):
        if r.get("provider") != key or not r.get("weekly_used_pct") or not r.get("weekly_resets_at"):
            continue
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            rs = datetime.fromisoformat(r["weekly_resets_at"])
            used = float(r["weekly_used_pct"])
        except ValueError:
            continue
        if abs((rs - reset).total_seconds()) > 3600 or now - ts > WINDOW:   # same cycle, recent only
            continue
        pts.append(((ts - now).total_seconds() / 3600, used))
    pts.append((0.0, reading.weekly_used_pct))
    if len(pts) < 3 or (pts[-1][0] - min(p[0] for p in pts)) < MIN_SPAN.total_seconds() / 3600:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    var = sum((p[0] - mx) ** 2 for p in pts)
    rate = sum((p[0] - mx) * (p[1] - my) for p in pts) / var if var else 0.0
    rate = max(0.0, rate)
    hours_to_reset = (reset - now).total_seconds() / 3600
    used_now = reading.weekly_used_pct
    if rate > 0 and used_now + rate * hours_to_reset >= 100:
        return Forecast(rate, now + timedelta(hours=(100 - used_now) / rate), None)
    return Forecast(rate, None, max(0.0, 100 - (used_now + rate * hours_to_reset)))
