"""Keepalive results reach the cards: last outcome, expired-at-run-time, paused after a dead login."""
import os
import time
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from quota_core.display import keepalive_line  # noqa: E402
from quota_core.engine import ProviderState  # noqa: E402
from quota_core.models import Reading, utcnow  # noqa: E402

NOW = time.time()


def test_line_text_and_card_visibility():
    assert keepalive_line(None) is None and keepalive_line({"last_outcome": "refreshed"}) is None
    text, show = keepalive_line({"last_run": NOW - 600, "last_outcome": "refreshed", "last_already_expired": True}, NOW)
    assert "renewed the login" in text and "(it had expired)" in text and show
    text, show = keepalive_line({"last_run": NOW - 20 * 3600, "last_outcome": "refreshed"}, NOW)
    assert not show                                  # old success: tooltip only
    text, show = keepalive_line({"last_run": NOW - 20 * 3600, "last_outcome": "timeout"}, NOW)
    assert "timed out" in text and show              # problems always show
    text, show = keepalive_line({"last_run": NOW, "dead": True}, NOW)
    assert "paused" in text and show


def test_card_and_tooltips_show_keepalive():
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication([])
    from quota_ui.cards import build_card, full_details
    r = Reading(name="Claude", weekly_used_pct=40, weekly_resets_at=utcnow() + timedelta(days=2))
    st = ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r,
                       keepalive={"last_run": NOW - 60, "last_outcome": "refreshed", "last_already_expired": True})
    card = build_card(st)
    assert any("Keepalive renewed the login" in l.text() for l in card.findChildren(QLabel))
    assert "Keepalive renewed" in card.toolTip() and "Keepalive renewed" in full_details(st)


def test_store_snapshot_carries_keepalive(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "quota-tray").mkdir()
    (tmp_path / "quota-tray" / "state.json").write_text(json.dumps({
        "version": 2, "providers": {}, "extras": {"keepalive": {"claude": {"last_run": NOW, "last_outcome": "timeout"}}}}))
    from quota_core.store import StateStore
    snap = StateStore([("claude", "Claude", "Claude Code"), ("grok", "Grok", "Grok CLI")]).snapshot()
    assert snap.providers[0].keepalive["last_outcome"] == "timeout" and snap.providers[1].keepalive is None
