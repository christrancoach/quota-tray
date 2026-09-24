"""Read-only view of state.json for front ends. Works the same in the poller and in followers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import MIN_POLL_SECONDS, state_path
from .engine import WAITING, ProviderState, load_provider_entries, read_state_file
from .models import STATUS_ERROR, STATUS_OK


@dataclass
class Snapshot:
    providers: list[ProviderState]
    updated_at: datetime | None = None
    poller: dict = field(default_factory=dict)   # {"frontend": "tray"|"widget", "pid": n}
    update: dict | None = None                   # {"version", "url", "notes"} when a newer release exists

    @property
    def busy(self) -> bool:
        return any(p.busy for p in self.providers)

    @property
    def last_refresh(self) -> datetime | None:
        times = [p.reading.last_updated for p in self.providers if p.reading and p.reading.status == STATUS_OK]
        return max(times) if times else None

    def seconds_until_allowed(self, now: float) -> int:
        """How long until a manual refresh could poll anything (the 5-minute floor)."""
        waits = []
        for p in self.providers:
            if not p.last_attempt or now - p.last_attempt >= MIN_POLL_SECONDS:
                return 0
            waits.append(MIN_POLL_SECONDS - (now - p.last_attempt))
        return int(min(waits)) if waits else 0


class StateStore:
    def __init__(self, provider_meta: list[tuple[str, str, str]]):
        """provider_meta: (key, display name, CLI name) in card order."""
        self.meta = provider_meta
        self._mtime: float | None = None

    def changed(self) -> bool:
        """True once per modification of state.json (mtime-based; survives atomic replace)."""
        try:
            m = state_path().stat().st_mtime_ns
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            return True
        return False

    def snapshot(self) -> Snapshot:
        data = read_state_file() or {}
        saved = load_provider_entries(data) if data else {}
        keepalive = ((data.get("extras") or {}).get("keepalive") or {}) if data else {}
        providers = []
        for key, name, cli in self.meta:
            s = saved.get(key)
            st = ProviderState(key, name, cli)
            st.keepalive = keepalive.get(key) if isinstance(keepalive.get(key), dict) else None
            if s:
                st.last_good, st.last_attempt, st.busy = s.last_good, s.last_attempt, s.busy
                st.reading = s.reading or (s.last_good.degraded(STATUS_ERROR, WAITING) if s.last_good else None)
                for r in (st.reading, st.last_good):
                    if r:
                        r.name = name
            providers.append(st)
        updated = data.get("updated_at")
        return Snapshot(providers=providers,
                        updated_at=datetime.fromisoformat(updated) if isinstance(updated, str) else None,
                        poller=data.get("poller") or {},
                        update=(data.get("extras") or {}).get("update") if data else None)
