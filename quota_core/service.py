"""CoreService: the one object a front end talks to.

Call tick() about once a second from the UI thread. Exactly one running front end holds the
leader mutex and runs the Engine (polling, keepalive, the 5-minute floor, alerts). The others are followers: they only read state.json, and retry for leadership every
LEADER_RETRY_SECONDS, so they take over within that time if the poller exits.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .config import (LEADER_MUTEX, LEADER_RETRY_SECONDS, config_path, load_config, poll_interval_seconds,
                     refresh_request_path)
from . import updates
from .engine import Alert, Engine, PreFetchHook
from .keepalive import KeepaliveHook
from .leader import LeaderLock
from .providers import build_providers
from .store import Snapshot, StateStore

log = logging.getLogger(__name__)
CONFIG_CHECK_SECONDS = 3
UPDATE_CHECK_SECONDS = 24 * 3600


def in_quiet_hours(cfg: dict, now: float) -> bool:
    """True inside the configured quiet hours (may span midnight). Alerts are held, not dropped."""
    from datetime import datetime
    q = ((cfg.get("alerts") or {}).get("quiet_hours") or {})
    if not q.get("enabled"):
        return False
    try:
        sh, sm = (int(x) for x in str(q.get("start", "22:00")).split(":"))
        eh, em = (int(x) for x in str(q.get("end", "08:00")).split(":"))
    except ValueError:
        return False
    t = datetime.fromtimestamp(now)
    cur, start, end = t.hour * 60 + t.minute, sh * 60 + sm, eh * 60 + em
    return start <= cur < end if start <= end else (cur >= start or cur < end)


class CoreService:
    def __init__(self, cfg: dict, frontend: str, notify: Callable[[str, str], None], *,
                 leader_name: str = LEADER_MUTEX, retry_seconds: float = LEADER_RETRY_SECONDS,
                 providers_factory: Callable[[dict], list] = build_providers,
                 hooks: list[PreFetchHook] | None = None, clock: Callable[[], float] = time.time,
                 watch_config: bool = False):
        self.cfg = cfg
        self.watch_config = watch_config          # front ends pass True: config.json edits apply live
        self._cfg_mtime = self._config_mtime() if watch_config else None
        self._cfg_checked = 0.0
        self.frontend = frontend
        self.notify = notify
        self.providers_factory = providers_factory
        self.hooks = [KeepaliveHook()] if hooks is None else hooks
        self.clock = clock
        self.retry_seconds = retry_seconds
        self.interval = poll_interval_seconds(cfg)
        self.lock = LeaderLock(leader_name)
        self.engine: Engine | None = None
        meta = [(p.key, p.name, p.cli_name) for p in providers_factory(cfg)]
        self.store = StateStore(meta)
        self._last_try = -1e18
        self._last_cycle = -1e18
        self._request_seen: float | None = None
        self._held: list = []   # alerts waiting for quiet hours to end
        self._last_update_check = -1e18

    @property
    def is_poller(self) -> bool:
        return self.engine is not None

    def tick(self) -> bool:
        """Advance leadership/polling. Returns True when state.json changed since the last tick."""
        now = self.clock()
        reloaded = self._maybe_reload_config(now)
        if self.engine is None and now - self._last_try >= self.retry_seconds:
            self._last_try = now
            if self.lock.try_acquire():
                self._become_poller()
        if self.engine is not None:
            if now - self._last_cycle >= self.interval:
                self._last_cycle = now
                self.engine.poll()
            if self._refresh_requested():
                self.engine.poll()
            if self.cfg.get("update_url") and now - self._last_update_check >= UPDATE_CHECK_SECONDS:
                self._last_update_check = now
                threading.Thread(target=self._check_update, args=(self.engine,), name="update-check",
                                 daemon=True).start()
            self._held += self.engine.pending_alerts()
            if self._held and not in_quiet_hours(self.cfg, self.clock()):
                held, self._held = self._held, []
                for alert in held:
                    try:
                        self.notify(alert.title, alert.message)
                    except Exception as exc:  # a notification failure must never break polling
                        log.warning("notify failed: %s", exc)
        return self.store.changed() or reloaded

    # ---------- live config ----------
    @staticmethod
    def _config_mtime():
        try:
            return config_path().stat().st_mtime_ns
        except OSError:
            return None

    def _maybe_reload_config(self, now: float) -> bool:
        if not self.watch_config or time.monotonic() - self._cfg_checked < CONFIG_CHECK_SECONDS:
            return False
        self._cfg_checked = time.monotonic()
        m = self._config_mtime()
        if m == self._cfg_mtime:
            return False
        self._cfg_mtime = m
        self.apply_config(load_config())
        return True

    def apply_config(self, cfg: dict):
        """Apply new settings without a restart: interval, providers (names, enabled, paths), alerts."""
        log.info("%s: config.json changed, applying", self.frontend)
        self.cfg = cfg
        self.interval = poll_interval_seconds(cfg)
        old_mtime = self.store._mtime
        self.store = StateStore([(p.key, p.name, p.cli_name) for p in self.providers_factory(cfg)])
        self.store._mtime = old_mtime
        if self.engine is not None:
            self.engine.shutdown()
            self._make_engine()

    def _become_poller(self):
        log.info("%s became the poller", self.frontend)
        # A fresh Engine loads state.json, so it resumes the previous poller's last-good data,
        # alert history and per-provider attempt times (the 5-minute floor carries over).
        self._make_engine()
        self.engine.save_state()  # announce the new poller in state.json right away
        self._request_seen = self._request_mtime()  # don't replay requests made before we led
        self._last_cycle = -1e18

    def _check_update(self, engine):
        """Poller only, so both front ends never both notify. Result goes into state.json for the menus."""
        found = updates.check(self.cfg.get("update_url"))
        if found is None:
            return
        engine.extras["update"] = {"version": found.version, "url": found.url, "notes": found.notes}
        if f"update|{found.version}" not in engine.notified:
            engine.notified.add(f"update|{found.version}")
            engine.queue_alert(Alert(title=f"Quota Tray {found.version} is available",
                                     message=found.notes or "Use Download update… in the menu."))
        engine.save_state()

    def _make_engine(self):
        self.engine = Engine(self.providers_factory(self.cfg), thresholds=self.cfg.get("thresholds", [20, 10]),
                             hooks=self.hooks, owner=self.frontend, clock=self.clock,
                             alerts=self.cfg.get("alerts") or {})

    def _request_mtime(self) -> float | None:
        try:
            return refresh_request_path().stat().st_mtime_ns
        except OSError:
            return None

    def _refresh_requested(self) -> bool:
        m = self._request_mtime()
        if m is not None and m != self._request_seen:
            self._request_seen = m
            return True
        return False

    def request_refresh(self) -> str:
        """Manual refresh. The poller polls directly; a follower asks the poller via a request file."""
        wait = self.snapshot().seconds_until_allowed(self.clock())
        if wait:
            return f"Checked recently. Next refresh in {max(1, round(wait / 60))} min"
        if self.engine is not None:
            self.engine.poll()
        else:
            refresh_request_path().write_text(str(self.clock()), encoding="utf-8")
        return "Refreshing…"

    def snapshot(self) -> Snapshot:
        return self.store.snapshot()

    def shutdown(self):
        if self.engine is not None:
            self.engine.shutdown()
            self.engine = None
        self.lock.release()
