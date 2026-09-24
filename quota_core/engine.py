"""The poller: fetches providers, merges with last good data, persists state, raises alerts.

Only the leader process runs an Engine (see service.py). Everything it knows is written to
state.json, so a follower front end can render it and a new leader can resume from it.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from .config import MIN_POLL_SECONDS, history_path, state_path
from .models import (STATUS_ERROR, STATUS_OK, STATUS_STALE, AuthStale, Reading, ShapeError, utcnow)
from .redact import redact

log = logging.getLogger(__name__)
STATE_VERSION = 2
HISTORY_FIELDS = ["timestamp", "provider", "weekly_used_pct", "weekly_resets_at",
                  "short_used_pct", "short_resets_at", "source"]
WAITING = "Waiting for first refresh"


@dataclass
class ProviderState:
    key: str
    name: str
    cli_name: str
    reading: Reading | None = None      # what the card shows (fresh, or last good marked stale/error)
    last_good: Reading | None = None
    last_attempt: float = 0.0           # wall-clock epoch seconds; persisted so the floor survives hand-offs
    busy: bool = False
    keepalive: dict | None = None       # last keepalive run for this CLI (display only; from state.json extras)


@dataclass
class Alert:
    title: str
    message: str


class PreFetchHook(Protocol):
    """Runs in the worker thread right before a provider is fetched (e.g. the token keepalive).

    Hooks must not raise; if one does, the error is logged and the fetch goes ahead anyway.
    A hook may also define attach(engine): it is called once, and gives the hook access to
    engine.extras (persisted in state.json), engine.save_state() and engine.queue_alert().
    """

    def before_fetch(self, provider, state: ProviderState) -> None: ...


def write_json_atomic(path: Path, data: dict) -> None:
    """tmp + replace, retrying briefly: on Windows a reader holding the file blocks os.replace."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"could not replace {path}")


class Engine:
    def __init__(self, providers: list, thresholds=(20, 10), *, hooks: list[PreFetchHook] | None = None,
                 owner: str = "", clock: Callable[[], float] = time.time, alerts: dict | None = None):
        self.providers = providers
        self.thresholds = sorted({int(t) for t in thresholds}, reverse=True)
        self.alert_cfg = alerts or {}
        self.hooks = list(hooks or [])
        self.owner = owner
        self.clock = clock
        self.states: dict[str, ProviderState] = {p.key: ProviderState(p.key, p.name, p.cli_name) for p in providers}
        self.notified: set[str] = set()
        self.extras: dict[str, dict] = {}   # per-hook persisted data, e.g. extras["keepalive"]
        self._queued_alerts: list[Alert] = []
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fetch")
        self._load_state()
        for hook in self.hooks:
            if hasattr(hook, "attach"):
                hook.attach(self)

    # ---------- polling ----------
    def due(self, key: str, now: float | None = None) -> bool:
        st = self.states[key]
        now = self.clock() if now is None else now
        return not st.busy and (st.last_attempt == 0 or now - st.last_attempt >= MIN_POLL_SECONDS)

    def seconds_until_allowed(self) -> int:
        now = self.clock()
        if any(self.due(k, now) for k in self.states):
            return 0
        waits = [MIN_POLL_SECONDS - (now - s.last_attempt) for s in self.states.values() if s.last_attempt]
        return max(0, int(min(waits))) if waits else 0

    def poll(self, on_done: Callable[[str], None] | None = None) -> list[str]:
        """Start fetches for every provider that is due. on_done(key) runs in a worker thread."""
        started = []
        with self._lock:
            for p in self.providers:
                if not self.due(p.key):
                    continue
                st = self.states[p.key]
                st.busy, st.last_attempt = True, self.clock()
                started.append(p)
            if started:
                self._save_state()
        for p in started:
            self._pool.submit(self._run, p, on_done or (lambda _k: None))
        return [p.key for p in started]

    def _run(self, provider, on_done):
        st = self.states[provider.key]
        for hook in self.hooks:
            try:
                hook.before_fetch(provider, st)
            except Exception as exc:
                log.warning("%s: pre-fetch hook %s failed: %r", provider.key, type(hook).__name__, exc)
        try:
            reading = provider.fetch()
            err = None
        except AuthStale as exc:
            reading, err = None, (STATUS_STALE, str(exc))
        except ShapeError as exc:
            log.warning("%s: unexpected response shape: %s | raw: %s", provider.key, exc, redact(exc.raw or ""))
            reading, err = None, (STATUS_ERROR, f"Unexpected response ({exc})")
        except Exception as exc:  # never let one provider take the app down
            log.warning("%s: fetch failed: %r", provider.key, exc)
            reading, err = None, (STATUS_ERROR, str(exc) or exc.__class__.__name__)
        try:
            self._apply(provider.key, reading, err)
        finally:
            on_done(provider.key)

    def _apply(self, key: str, reading: Reading | None, err: tuple[str, str] | None):
        with self._lock:
            st = self.states[key]
            st.busy = False
            # A log-fallback reading older than what we already have is not an improvement.
            if reading and st.last_good and reading.last_updated < st.last_good.last_updated:
                err, reading = (STATUS_ERROR, reading.error or "Only older data available"), None
            if reading:
                reading.status = STATUS_OK
                self._check_reset_back(st, reading)
                st.reading = st.last_good = reading
                self._append_history(key, reading)
            elif st.last_good:
                st.reading = st.last_good.degraded(*err)
            else:
                st.reading = Reading(name=st.name, weekly_used_pct=None, weekly_resets_at=None,
                                     status=err[0], error=err[1], last_updated=utcnow())
            self._save_state()

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def save_state(self):
        with self._lock:
            self._save_state()

    def queue_alert(self, alert: Alert):
        """For hooks: deliver an alert through the normal path (poller only, once)."""
        with self._lock:
            self._queued_alerts.append(alert)

    # ---------- alerts ----------
    def _check_reset_back(self, st: ProviderState, new: Reading):
        """A weekly pool that ran low has started a new cycle: say it's back."""
        old = st.last_good
        if not self.alert_cfg.get("reset_back", True) or old is None or old.weekly_left_pct is None:
            return
        if not (old.weekly_resets_at and new.weekly_resets_at and new.weekly_left_pct is not None):
            return
        low = max(self.thresholds) if self.thresholds else 20
        new_cycle = new.weekly_resets_at - old.weekly_resets_at > timedelta(days=1)
        if new_cycle and old.weekly_left_pct < low and new.weekly_left_pct >= 50:   # genuinely back
            self._queued_alerts.append(Alert(title=f"{st.name}: weekly limit has reset",
                                             message=f"Back to {new.weekly_left_pct:.0f}% left."))

    def pending_alerts(self) -> list[Alert]:
        """Crossed thresholds not yet announced this reset cycle. Marks them announced."""
        with self._lock:
            alerts, self._queued_alerts = self._queued_alerts, []
            for st in self.states.values():   # response-shape warnings: once each, ever
                for w in (st.reading.warnings or []) if st.reading else []:
                    k = f"shape|{st.key}|{w}"
                    if k not in self.notified:
                        self.notified.add(k)
                        alerts.append(Alert(title=f"{st.name}: usage response changed",
                                            message=f"Some details may be missing: {w}. See debug.log."))
                        log.warning("%s: response shape changed: %s", st.key, w)
            for st in self.states.values():
                r = st.reading
                if not r or r.status != STATUS_OK or r.weekly_left_pct is None:
                    continue
                cycle = r.weekly_resets_at.strftime("%Y%m%d%H") if r.weekly_resets_at else "none"
                crossed = [t for t in self.thresholds if r.weekly_left_pct < t]
                if not crossed:
                    continue
                lowest = min(crossed)
                new = [t for t in crossed if f"{st.key}|{t}|{cycle}" not in self.notified]
                for t in crossed:
                    self.notified.add(f"{st.key}|{t}|{cycle}")
                if new:
                    alerts.append(Alert(
                        title=f"{st.name}: {r.weekly_left_pct:.0f}% weekly left",
                        message=f"Below {lowest}% of the weekly pool." + (
                            f" Resets {r.weekly_resets_at.astimezone():%a %H:%M}." if r.weekly_resets_at else "")))
            alerts += self._extra_alerts()
            if alerts:
                self._save_state()
        return alerts

    def _extra_alerts(self) -> list[Alert]:
        """5-hour thresholds, 'resets soon' and the banked-reset hint (caller holds the lock)."""
        out = []
        cfg = self.alert_cfg
        now = utcnow()
        low = max(self.thresholds) if self.thresholds else 20
        short_t = sorted({int(t) for t in cfg.get("short_thresholds") or []}, reverse=True)
        soon_h = float(cfg.get("resets_soon_hours", 3) or 0)
        for st in self.states.values():
            r = st.reading
            if not r or r.status != STATUS_OK:
                continue
            cycle = r.weekly_resets_at.strftime("%Y%m%d%H") if r.weekly_resets_at else "none"
            if short_t and r.short_left_pct is not None:
                s_cycle = r.short_resets_at.strftime("%Y%m%d%H%M") if r.short_resets_at else "none"
                crossed = [t for t in short_t if r.short_left_pct < t]
                new = [t for t in crossed if f"{st.key}|short|{t}|{s_cycle}" not in self.notified]
                for t in crossed:
                    self.notified.add(f"{st.key}|short|{t}|{s_cycle}")
                if new:
                    when = f" Resets {r.short_resets_at.astimezone():%H:%M}." if r.short_resets_at else ""
                    out.append(Alert(title=f"{st.name}: {r.short_left_pct:.0f}% of the 5-hour window left",
                                     message=f"Below {min(crossed)}% of the 5-hour window.{when}"))
            left = r.weekly_left_pct
            if left is None or left >= low:
                continue
            if soon_h and r.weekly_resets_at and timedelta(0) < r.weekly_resets_at - now <= timedelta(hours=soon_h):
                k = f"{st.key}|soon|{cycle}"
                if k not in self.notified:
                    self.notified.add(k)
                    mins = int((r.weekly_resets_at - now).total_seconds() // 60)
                    out.append(Alert(title=f"{st.name}: weekly limit resets soon",
                                     message=f"{left:.0f}% left, resets in {mins // 60}h {mins % 60}m "
                                             f"({r.weekly_resets_at.astimezone():%a %H:%M})."))
            if cfg.get("banked_reset_hint", True) and r.reset_usable_now:
                k = f"{st.key}|banked|{cycle}"
                if k not in self.notified:
                    self.notified.add(k)
                    out.append(Alert(title=f"{st.name}: you have a reset you can use",
                                     message=f"{left:.0f}% weekly left. Use the banked reset in the CLI "
                                             "if you need more now."))
        return out

    # ---------- persistence ----------
    def _append_history(self, key: str, r: Reading):
        path: Path = history_path()
        try:
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
                if new:
                    w.writeheader()
                w.writerow({
                    "timestamp": r.last_updated.isoformat(), "provider": key,
                    "weekly_used_pct": r.weekly_used_pct,
                    "weekly_resets_at": r.weekly_resets_at.isoformat() if r.weekly_resets_at else "",
                    "short_used_pct": "" if r.short_used_pct is None else r.short_used_pct,
                    "short_resets_at": r.short_resets_at.isoformat() if r.short_resets_at else "",
                    "source": r.source,
                })
        except OSError as exc:
            log.warning("history write failed: %s", exc)

    def _save_state(self):
        data = {
            "version": STATE_VERSION,
            "updated_at": utcnow().isoformat(),
            "poller": {"frontend": self.owner, "pid": os.getpid()},
            "providers": {k: {
                "reading": s.reading.to_json() if s.reading else None,
                "last_good": s.last_good.to_json() if s.last_good else None,
                "last_attempt": s.last_attempt,
                "busy": s.busy,
            } for k, s in self.states.items()},
            "notified": sorted(self.notified)[-200:],
            "extras": self.extras,
        }
        try:
            write_json_atomic(state_path(), data)
        except OSError as exc:
            log.warning("state write failed: %s", exc)

    def _load_state(self):
        data = read_state_file()
        if not data:
            return
        self.notified = set(data.get("notified") or [])
        extras = data.get("extras")
        self.extras = extras if isinstance(extras, dict) else {}
        now = self.clock()
        for key, saved in load_provider_entries(data).items():
            st = self.states.get(key)
            if not st:
                continue
            st.last_good = saved.last_good
            st.last_attempt = saved.last_attempt
            if st.last_good:
                st.last_good.name = st.name
            # Inside the 5-minute floor the saved reading is as fresh as anything we are allowed
            # to fetch, so show it as-is. Otherwise show it grayed until this poller refreshes it.
            if saved.reading and saved.last_attempt and now - saved.last_attempt < MIN_POLL_SECONDS:
                st.reading = saved.reading
                st.reading.name = st.name
            elif st.last_good:
                st.reading = st.last_good.degraded(STATUS_ERROR, WAITING)


def read_state_file() -> dict | None:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _reading(raw) -> Reading | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Reading.from_json(raw)
    except (KeyError, ValueError, TypeError):
        return None


def load_provider_entries(data: dict) -> dict[str, ProviderState]:
    """Parse the providers section of state.json (v2), or the v1 {"last_good": {...}} layout."""
    out: dict[str, ProviderState] = {}
    if data.get("version", 1) >= 2:
        for key, e in (data.get("providers") or {}).items():
            if not isinstance(e, dict):
                continue
            out[key] = ProviderState(key, key, "", reading=_reading(e.get("reading")),
                                     last_good=_reading(e.get("last_good")),
                                     last_attempt=float(e.get("last_attempt") or 0.0), busy=bool(e.get("busy")))
    else:
        for key, raw in (data.get("last_good") or {}).items():
            out[key] = ProviderState(key, key, "", last_good=_reading(raw))
    return out


def fmt_local(dt: datetime | None, fmt: str = "%a %H:%M") -> str:
    return dt.astimezone().strftime(fmt) if dt else "?"
