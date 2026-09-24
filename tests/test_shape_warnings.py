"""Shape self-check: missing optional fields produce warnings, one alert each, shown on cards."""
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quota_core import engine as engine_mod  # noqa: E402
from quota_core.models import Reading, utcnow  # noqa: E402
from quota_core.providers import claude, codex, grok  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mod,fixture", [(claude, "claude_usage"), (codex, "codex_usage"), (grok, "grok_credits")])
def test_real_response_shapes_raise_no_warnings(mod, fixture):
    data = load(fixture)
    if mod is codex:
        data["rate_limit_reset_credits"] = {"available_count": 1}   # present in current live responses
    assert mod.shape_warnings(data) == []


def test_missing_optional_fields_are_named():
    data = load("claude_usage")
    del data["limits"]
    data["seven_day_breakdown"] = {}
    w = claude.shape_warnings(data)
    assert len(w) == 2 and "per-model weekly limits" in w[0] and "Claude Code / Chats split" in w[1]
    cx = load("codex_usage")
    del cx["rate_limit"]["primary_window"]["limit_window_seconds"]
    assert any("window length" in m for m in codex.shape_warnings(cx))


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


class P:
    key, name, cli_name = "claude", "Claude", "Claude Code"

    def __init__(self, r):
        self.r = r

    def fetch(self):
        return self.r


def test_each_new_warning_alerts_once():
    r = Reading(name="Claude", weekly_used_pct=10, weekly_resets_at=utcnow() + timedelta(days=3),
                warnings=["'limits' is missing (per-model weekly limits)"])
    p = P(r)
    eng = engine_mod.Engine([p])
    eng._run(p, lambda _k: None)
    alerts = eng.pending_alerts()
    assert len(alerts) == 1 and "usage response changed" in alerts[0].title
    eng._run(p, lambda _k: None)
    assert eng.pending_alerts() == []                  # same warning: no repeat
    r.warnings = r.warnings + ["'seven_day.resets_at' is missing (weekly reset time)"]
    eng._run(p, lambda _k: None)
    assert len(eng.pending_alerts()) == 1              # a new one alerts once


def test_card_shows_the_warning(qapp=None):
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication([])
    from quota_core.engine import ProviderState
    from quota_ui.cards import build_card
    r = Reading(name="Claude", weekly_used_pct=10, weekly_resets_at=utcnow() + timedelta(days=3),
                warnings=["'limits' is missing (per-model weekly limits)"])
    card = build_card(ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r))
    texts = [l.text() for l in card.findChildren(QLabel)]
    assert any("usage response changed" in t for t in texts)
    assert "Response changed" in card.toolTip()
