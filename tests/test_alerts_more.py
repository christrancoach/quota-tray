"""5-hour alerts, resets-soon, reset-back, banked-reset hint, quiet hours."""
from datetime import datetime, timedelta

import pytest

from quota_core import engine as engine_mod
from quota_core.models import Reading, utcnow
from quota_core.service import in_quiet_hours


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class P:
    key, name, cli_name = "codex", "Codex", "Codex CLI"

    def __init__(self, r):
        self.r = r

    def fetch(self):
        return self.r


def run(eng, p):
    eng._run(p, lambda _k: None)
    return [a.title for a in eng.pending_alerts()]


def reading(left, reset_in=timedelta(days=3), short_left=None, resets=None):
    return Reading(name="Codex", weekly_used_pct=100 - left, weekly_resets_at=utcnow() + reset_in,
                   short_used_pct=None if short_left is None else 100 - short_left,
                   short_resets_at=utcnow() + timedelta(hours=2) if short_left is not None else None, resets=resets)


def test_short_window_alerts_are_opt_in_and_once():
    p = P(reading(80, short_left=15))
    assert not any("5-hour" in t for t in run(engine_mod.Engine([p]), p))          # off by default
    eng = engine_mod.Engine([p], alerts={"short_thresholds": [20]})
    assert any("5-hour window left" in t for t in run(eng, p))
    assert run(eng, p) == []


def test_resets_soon_only_when_low_and_close():
    p = P(reading(15, reset_in=timedelta(hours=2)))
    titles = run(engine_mod.Engine([p], alerts={"resets_soon_hours": 3, "banked_reset_hint": False}), p)
    assert "Codex: weekly limit resets soon" in titles
    p2 = P(reading(60, reset_in=timedelta(hours=2)))
    assert not any("soon" in t for t in run(engine_mod.Engine([p2]), p2))            # not low: no alert
    p3 = P(reading(15, reset_in=timedelta(hours=10)))
    assert not any("soon" in t for t in run(engine_mod.Engine([p3], alerts={"banked_reset_hint": False}), p3))


def test_reset_back_after_running_low():
    p = P(reading(5))
    eng = engine_mod.Engine([p])
    run(eng, p)
    p.r = reading(100, reset_in=timedelta(days=10))   # next cycle, full again
    assert "Codex: weekly limit has reset" in run(eng, p)


def test_banked_reset_hint_when_low_and_usable():
    usable = [{"label": "Full reset", "left": 1, "usable_now": True}]
    p = P(reading(8, resets=usable))
    eng = engine_mod.Engine([p], alerts={"resets_soon_hours": 0})
    assert "Codex: you have a reset you can use" in run(eng, p)
    assert run(eng, p) == []


@pytest.mark.parametrize("hhmm,start,end,quiet", [
    ("23:30", "22:00", "08:00", True), ("07:59", "22:00", "08:00", True), ("08:00", "22:00", "08:00", False),
    ("12:00", "22:00", "08:00", False), ("13:00", "12:00", "14:00", True), ("15:00", "12:00", "14:00", False)])
def test_quiet_hours_span_midnight(hhmm, start, end, quiet):
    h, m = map(int, hhmm.split(":"))
    now = datetime.now().replace(hour=h, minute=m, second=0).timestamp()
    cfg = {"alerts": {"quiet_hours": {"enabled": True, "start": start, "end": end}}}
    assert in_quiet_hours(cfg, now) == quiet
    assert in_quiet_hours({"alerts": {"quiet_hours": {"enabled": False}}}, now) is False


def test_service_holds_alerts_during_quiet_hours(tmp_path, monkeypatch):
    import uuid
    from quota_core.service import CoreService
    sent = []
    t = [datetime.now().replace(hour=23, minute=0).timestamp()]
    p = P(reading(5))
    cfg = {"refresh_interval_minutes": 10, "thresholds": [20, 10],
           "alerts": {"quiet_hours": {"enabled": True, "start": "22:00", "end": "08:00"}}}
    svc = CoreService(cfg, "test", lambda title, msg: sent.append(title), leader_name=rf"Local\QT-{uuid.uuid4().hex}",
                      providers_factory=lambda _c: [p], hooks=[], clock=lambda: t[0])
    svc.tick()
    svc.engine._run(p, lambda _k: None)
    svc.tick()
    assert sent == [] and svc._held                  # held overnight
    t[0] = datetime.now().replace(hour=8, minute=5).timestamp() + 86400
    svc.tick()
    assert sent and not svc._held                    # delivered when quiet hours end
    svc.shutdown()
