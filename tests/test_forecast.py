"""Burn-rate forecast from history.csv."""
import csv
from datetime import timedelta

from quota_core import forecast as fc_mod
from quota_core.models import Reading, utcnow

NOW = utcnow().replace(microsecond=0)
RESET = NOW + timedelta(hours=48)


def write_history(path, points, reset=RESET, provider="claude"):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "provider", "weekly_used_pct", "weekly_resets_at",
                                           "short_used_pct", "short_resets_at", "source"])
        w.writeheader()
        for hours_ago, used in points:
            w.writerow({"timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(), "provider": provider,
                        "weekly_used_pct": used, "weekly_resets_at": reset.isoformat(), "short_used_pct": "",
                        "short_resets_at": "", "source": "cli"})
    fc_mod._cache.update(mtime=None, rows=[])


def reading(used):
    return Reading(name="Claude", weekly_used_pct=used, weekly_resets_at=RESET)


def test_runs_out_before_reset(tmp_path):
    h = tmp_path / "history.csv"
    write_history(h, [(10, 30), (5, 45)])          # +3 %/h
    f = fc_mod.forecast("claude", reading(60), path=h, now=NOW)
    assert f.runs_out and f.rate_per_hour > 2.5
    assert abs((f.runs_out_at - NOW).total_seconds() / 3600 - 40 / f.rate_per_hour) < 0.01
    assert "runs out" in f.text() and f.worrying


def test_finishes_with_some_left(tmp_path):
    h = tmp_path / "history.csv"
    write_history(h, [(20, 10), (10, 15)])         # +0.5 %/h, 48 h to reset
    f = fc_mod.forecast("claude", reading(20), path=h, now=NOW)
    assert not f.runs_out and 50 < f.left_at_reset < 60 and "~5" in f.text() and not f.worrying


def test_ignores_other_cycles_providers_and_needs_history(tmp_path):
    h = tmp_path / "history.csv"
    write_history(h, [(10, 90), (5, 95)], reset=RESET - timedelta(days=7))   # last week's cycle
    assert fc_mod.forecast("claude", reading(10), path=h, now=NOW) is None
    write_history(h, [(10, 10), (5, 30)], provider="codex")
    assert fc_mod.forecast("claude", reading(40), path=h, now=NOW) is None
    assert fc_mod.forecast("claude", reading(100), path=h, now=NOW) is None      # already empty


def test_reads_only_the_tail_of_a_large_history(tmp_path, monkeypatch):
    monkeypatch.setattr(fc_mod, "TAIL_BYTES", 2000)
    h = tmp_path / "history.csv"
    write_history(h, [(60 - i * 0.1, 10 + i * 0.05) for i in range(400)])
    f = fc_mod.forecast("claude", reading(30), path=h, now=NOW)
    assert f is not None and f.rate_per_hour > 0
