"""Usage report window on Qt's offscreen platform, with synthetic logs and a tiny price list."""
import json
import os
import time
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from quota_core.usage import index as ix  # noqa: E402
from quota_core.usage.report import fmt_money  # noqa: E402
from quota_ui import report_window as rw  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def logs(tmp_path, monkeypatch):
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini"))
    monkeypatch.setattr(rw, "refresh_litellm", lambda *a, **k: False)   # never touch the network in tests
    (appdata / "quota-tray").mkdir(parents=True)
    (appdata / "quota-tray" / "litellm_prices.json").write_text(json.dumps({
        "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6,
                          "cache_read_input_token_cost": 0.5e-6}}))
    proj = tmp_path / "claude" / "projects" / "p1"
    proj.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    lines = []
    for i, model in enumerate(["claude-opus-5", "claude-opus-5", "claude-mystery"]):
        lines.append(json.dumps({"type": "assistant", "sessionId": f"s{i}", "requestId": f"r{i}", "timestamp": now,
                                 "message": {"id": f"m{i}", "model": model, "usage": {
                                     "input_tokens": 100, "cache_read_input_tokens": 10000,
                                     "cache_creation_input_tokens": 0, "output_tokens": 1000}}}))
    (proj / "a.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def window(monkeypatch):
    monkeypatch.setattr(rw.ReportWindow, "refresh_from_logs", lambda self: None)
    return rw.ReportWindow()


def test_report_window_shows_totals_and_breakdown(qapp, logs, monkeypatch):
    ix.update()
    w = window(monkeypatch)
    w.show()
    qapp.processEvents()
    per_call = 100 * 5e-6 + 10000 * 0.5e-6 + 1000 * 25e-6
    assert w.hero.text() == fmt_money(2 * per_call)
    assert "3 sessions" in w.hero_sub.text() and "API estimate" in w.hero_sub.text()
    assert w.table.rowCount() == 2                                   # opus-5 and the unpriced model
    assert "(no price)" in w.table.item(1, 0).text()
    assert "claude-mystery" in w.note.text()
    w.set_view("day")
    assert w.table.rowCount() == 1 and w.table.horizontalHeaderItem(0).text() == "Day"
    w.set_range(7)
    assert "last 7 days" in w.hero_sub.text()
    w.chart._set_hover(len(w.chart.report.days) - 1)
    w.chart.grab()                                                    # paints the hover tooltip without error
    w.close()


def test_background_refresh_indexes_and_redraws(qapp, logs):
    w = rw.ReportWindow()                                             # starts the real worker thread
    deadline = time.monotonic() + 15
    while not w.status.text().startswith("Updated") and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    assert w.status.text().startswith("Updated"), w.status.text()
    assert w.hero.text() != "$0.00"
    w.close()


def test_widget_menu_offers_the_report(qapp, logs):
    from quota_widget.window import WidgetWindow
    texts = [a.text() for a in WidgetWindow(persist=False).build_menu().actions()]
    assert "Usage report…" in texts
