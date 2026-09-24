"""Keyboard control and screen-reader text for the widget, cards, cats and the report chart."""
import os
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from quota_core.engine import ProviderState  # noqa: E402
from quota_core.models import Reading, utcnow  # noqa: E402
from quota_core.store import Snapshot  # noqa: E402
from quota_widget.settings import WidgetSettings  # noqa: E402
from quota_widget.window import WidgetWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def snap():
    r = Reading(name="Claude", weekly_used_pct=46, weekly_resets_at=utcnow() + timedelta(days=2),
                resets=[{"label": "x", "left": 1, "usable_now": True}])
    return Snapshot(providers=[ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r)])


def key(w, k, mods=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, k, mods))


def test_widget_keyboard_control(qapp):
    refreshed = []
    w = WidgetWindow(WidgetSettings(x=300, y=300), on_refresh=lambda: refreshed.append(1))
    w.render(snap()); w.place_initial(); w.show(); qapp.processEvents()
    assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus
    key(w, Qt.Key.Key_Right); key(w, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert (w.x(), w.y()) == (310, 301)
    key(w, Qt.Key.Key_Return)
    assert w.settings.mode == "expanded"
    key(w, Qt.Key.Key_R)
    assert refreshed == [1]
    w.close()


def test_widget_and_cards_speak_their_numbers(qapp):
    w = WidgetWindow(WidgetSettings(mode="expanded"), persist=False)
    w.render(snap())
    d = w.accessibleDescription()
    assert "Claude: 54 percent of the weekly limit left" in d and "1 banked reset available" in d and "Keys:" in d
    cards = [c for c in w.findChildren(QWidget) if c.accessibleName() == "Claude usage card"]
    assert cards and "54% weekly left" in cards[0].accessibleDescription()


def test_cats_have_text_equivalents(qapp):
    w = WidgetWindow(WidgetSettings(mode="cats"), persist=False)
    w.render(snap())
    names = [c.accessibleName() for c in w.findChildren(QWidget) if c.accessibleName().startswith("Claude: cat is")]
    assert names == ["Claude: cat is alert"]


def test_report_chart_keyboard(qapp, tmp_path, monkeypatch):
    from quota_ui import report_window as rw
    monkeypatch.setattr(rw.ReportWindow, "refresh_from_logs", lambda self: None)
    win = rw.ReportWindow()
    ch = win.chart
    assert ch.focusPolicy() == Qt.FocusPolicy.StrongFocus
    key(ch, Qt.Key.Key_End)
    assert ch.hover == len(ch.report.days) - 1
    key(ch, Qt.Key.Key_Left)
    assert ch.hover == len(ch.report.days) - 2
    key(ch, Qt.Key.Key_Escape)
    assert ch.hover is None
    win.close()
