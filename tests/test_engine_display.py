from datetime import timedelta

import pytest

from quota_core import engine as engine_mod
from quota_core.display import TOOLTIP_MAX, color_for_left, icon_value, reset_since_last_read, tooltip
from quota_core.models import STATUS_ERROR, STATUS_OK, STATUS_STALE, AuthStale, Reading, utcnow
from quota_core.redact import redact


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def reading(name="Claude", used=40.0, reset_in=timedelta(days=3), **kw):
    return Reading(name=name, weekly_used_pct=used, weekly_resets_at=utcnow() + reset_in, **kw)


class FakeProvider:
    cli_name = "Fake CLI"

    def __init__(self, key, result):
        self.key, self.name, self.result = key, key.title(), result

    def fetch(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run_sync(eng, key):
    eng._run(next(p for p in eng.providers if p.key == key), lambda _k: None)


def test_colors():
    assert color_for_left(51) == "#2EA043"
    assert color_for_left(50) == color_for_left(20) == "#D29922"
    assert color_for_left(19.9) == "#E5534B"


def test_stale_keeps_last_good_and_marks_it():
    p = FakeProvider("claude", reading(used=30))
    eng = engine_mod.Engine([p])
    run_sync(eng, "claude")
    assert eng.states["claude"].reading.status == STATUS_OK
    good_time = eng.states["claude"].reading.last_updated
    p.result = AuthStale("Open Fake CLI to refresh")
    run_sync(eng, "claude")
    r = eng.states["claude"].reading
    assert r.status == STATUS_STALE and r.weekly_used_pct == 30 and r.last_updated == good_time


def test_error_without_history_does_not_crash():
    eng = engine_mod.Engine([FakeProvider("grok", RuntimeError("boom"))])
    run_sync(eng, "grok")
    r = eng.states["grok"].reading
    assert r.status == STATUS_ERROR and r.weekly_used_pct is None and "boom" in r.error


def test_reset_since_last_read():
    r = reading(reset_in=timedelta(hours=-1)).degraded(STATUS_STALE, "x")
    assert reset_since_last_read(r)
    assert not reset_since_last_read(reading(reset_in=timedelta(hours=-1)))  # fresh readings are trusted
    assert icon_value([r, reading(used=70)]) == 30


def test_poll_floor_five_minutes():
    eng = engine_mod.Engine([FakeProvider("claude", reading())])
    assert eng.due("claude", now=1000)
    eng.states["claude"].last_attempt = 1000
    assert not eng.due("claude", now=1000 + 299)
    assert eng.due("claude", now=1000 + 300)


def test_alerts_once_per_threshold_per_cycle():
    p = FakeProvider("codex", reading(name="Codex", used=85))
    eng = engine_mod.Engine([p], thresholds=[20, 10])
    run_sync(eng, "codex")
    assert len(eng.pending_alerts()) == 1          # 15% left: crosses 20
    assert eng.pending_alerts() == []              # not repeated
    p.result = reading(name="Codex", used=95)
    run_sync(eng, "codex")
    alerts = eng.pending_alerts()
    assert len(alerts) == 1 and "10%" in alerts[0].message
    p.result = reading(name="Codex", used=95, reset_in=timedelta(days=10))  # new cycle
    run_sync(eng, "codex")
    assert len(eng.pending_alerts()) == 1


def test_state_persists_across_restart():
    eng = engine_mod.Engine([FakeProvider("claude", reading(used=12))])
    run_sync(eng, "claude")
    eng2 = engine_mod.Engine([FakeProvider("claude", reading())])
    r = eng2.states["claude"].reading
    assert r.weekly_used_pct == 12 and r.status == STATUS_ERROR  # shown grayed until first refresh


def test_tooltip_fits_windows_limit():
    rs = [reading("Claude", 38), reading("ChatGPT (Codex)", 95), reading("Grok", 100),
          reading("A fourth provider", 0)]
    text = tooltip(rs)
    assert len(text) <= TOOLTIP_MAX and text.count("\n") == 3
    assert text.startswith("Claude 62%")


def test_redaction():
    s = redact('{"access_token": "ya29.FAKEtestTOKEN123456", "auth": "Bearer sk-ant-oat01-FAKEabcdefg"} eyJGQUtF.eyJGQUtFX3Rlc3Q.sig me@x.com')
    assert "FAKEtestTOKEN" not in s and "FAKEabcdefg" not in s and "eyJGQUtFX3Rlc3Q" not in s and "me@x.com" not in s
    assert "ya29.F" in s  # first 6 chars kept
