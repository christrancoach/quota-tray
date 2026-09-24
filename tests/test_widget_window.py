"""Widget window behavior on Qt's offscreen platform: toggle, drag, z-order, opacity, persistence."""
import json
import os
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quota_core.engine import ProviderState  # noqa: E402
from quota_core.models import Reading, utcnow  # noqa: E402
from quota_core.store import Snapshot  # noqa: E402
from quota_widget.settings import WidgetSettings, load_settings  # noqa: E402
from quota_widget.window import COMPACT_WIDTH, EXPANDED_WIDTH, WidgetWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def snapshot():
    r = Reading(name="Claude", weekly_used_pct=40, weekly_resets_at=utcnow() + timedelta(days=2),
                short_used_pct=10, short_resets_at=utcnow() + timedelta(hours=2))
    return Snapshot(providers=[ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r)])


def mouse(win, kind, pos, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    local = QPointF(pos[0] - win.x(), pos[1] - win.y())
    ev = QMouseEvent(kind, local, QPointF(*pos), button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(win, ev)


def saved(tmp_path):
    return json.loads((tmp_path / "quota-tray" / "widget.json").read_text())


def make(qapp, **settings):
    win = WidgetWindow(WidgetSettings(**settings))
    win.render(snapshot())
    win.place_initial()
    win.show()
    qapp.processEvents()
    return win


def test_double_click_toggles_size_and_persists(qapp, isolated_appdata):
    win = make(qapp)
    assert win.width() == COMPACT_WIDTH
    x, y = win.x() + 20, win.y() + 20
    mouse(win, QEvent.Type.MouseButtonDblClick, (x, y))
    assert win.expanded and win.width() == EXPANDED_WIDTH
    assert saved(isolated_appdata)["mode"] == "expanded"
    mouse(win, QEvent.Type.MouseButtonDblClick, (x, y))
    assert not win.expanded and win.width() == COMPACT_WIDTH
    assert saved(isolated_appdata)["mode"] == "compact"
    win.close()


@pytest.mark.parametrize("mode", ["compact", "expanded", "cats"])
def test_redraw_while_visible_keeps_full_height(qapp, mode):
    """Regression: re-rendering a visible widget collapsed it to its margins (~22 px)."""
    win = make(qapp, mode=mode)
    first = win.height()
    assert first > 60
    for _ in range(3):  # the app redraws every 30 s and on every state change
        win.render(snapshot())
        qapp.processEvents()
    assert win.height() == first == win.sizeHint().height()
    win.close()


def test_toggle_while_visible_resizes_to_new_content(qapp, isolated_appdata):
    win = make(qapp)
    compact = win.height()
    win.set_mode("expanded")
    qapp.processEvents()
    assert win.height() > compact and win.height() == win.sizeHint().height()
    win.close()


def test_drag_moves_by_mouse_delta_and_persists_on_release(qapp, isolated_appdata):
    win = make(qapp, x=200, y=150)
    assert (win.x(), win.y()) == (200, 150)
    mouse(win, QEvent.Type.MouseButtonPress, (230, 170))
    mouse(win, QEvent.Type.MouseMove, (330, 240), button=Qt.MouseButton.NoButton)
    mouse(win, QEvent.Type.MouseButtonRelease, (330, 240), buttons=Qt.MouseButton.NoButton)
    assert (win.x(), win.y()) == (300, 220)
    s = saved(isolated_appdata)
    assert (s["x"], s["y"]) == (300, 220)
    win.close()


def test_click_without_moving_does_not_rewrite_position(qapp, isolated_appdata):
    win = make(qapp, x=200, y=150)
    mouse(win, QEvent.Type.MouseButtonPress, (230, 170))
    mouse(win, QEvent.Type.MouseButtonRelease, (230, 170), buttons=Qt.MouseButton.NoButton)
    assert not (isolated_appdata / "quota-tray" / "widget.json").exists()
    win.close()


def test_off_screen_saved_position_is_recovered_at_launch(qapp):
    win = make(qapp, x=-40000, y=-40000)
    screen = QApplication.primaryScreen().availableGeometry()
    assert screen.contains(win.x() + 10, win.y() + 10)
    win.close()


@pytest.mark.parametrize("mode,on_top,on_bottom", [("top", True, False), ("normal", False, False), ("desktop", False, True)])
def test_z_order_modes_set_window_flags(qapp, isolated_appdata, mode, on_top, on_bottom):
    win = make(qapp)
    win.set_z_order(mode)
    flags = win.windowFlags()
    assert bool(flags & Qt.WindowType.WindowStaysOnTopHint) == on_top
    assert bool(flags & Qt.WindowType.WindowStaysOnBottomHint) == on_bottom
    assert flags & Qt.WindowType.Tool  # never in the taskbar or Alt+Tab
    assert saved(isolated_appdata)["z_order"] == mode
    win.close()


def test_opacity_and_theme_persist(qapp, isolated_appdata):
    win = make(qapp)
    win.set_opacity(70)
    win.set_theme("light")
    assert abs(win.windowOpacity() - 0.70) < 0.01
    s = load_settings()
    assert (s.opacity, s.theme) == (70, "light")
    win.close()


def test_menu_has_every_item(qapp):
    win = make(qapp)
    texts = [a.text() for a in win.build_menu().actions() if a.text()]
    for expected in ["Always on top", "Pinned to desktop", "Compact", "Expanded", "Opacity", "Theme",
                     "Refresh now", "Keepalive", "Start with Windows", "Close"]:
        assert expected in texts, texts
    win.close()


def test_corrupt_or_invalid_widget_json_falls_back_to_defaults(isolated_appdata):
    d = isolated_appdata / "quota-tray"
    d.mkdir(parents=True, exist_ok=True)
    (d / "widget.json").write_text("{not json", encoding="utf-8")
    assert load_settings() == WidgetSettings()
    (d / "widget.json").write_text(json.dumps({"x": "a", "opacity": 33, "z_order": "sideways", "theme": 5}))
    assert load_settings() == WidgetSettings()


# ---------- per-card collapse (expanded mode) ----------
from PySide6.QtWidgets import QToolButton  # noqa: E402

from quota_ui.cards import build_card  # noqa: E402


def three_snapshot():
    def st(key, name, used):
        r = Reading(name=name, weekly_used_pct=used, weekly_resets_at=utcnow() + timedelta(days=2))
        return ProviderState(key, name, f"{name} CLI", reading=r, last_good=r)
    return Snapshot(providers=[st("claude", "Claude", 40), st("codex", "ChatGPT (Codex)", 95),
                               st("grok", "Grok", 100)])


def expanded_window(qapp, **settings):
    win = WidgetWindow(WidgetSettings(mode="expanded", **settings))
    win.render(three_snapshot())
    win.place_initial()
    win.show()
    qapp.processEvents()
    return win


def chevrons(win):
    return win.findChildren(QToolButton, "chevron")


def test_chevron_collapses_one_card_and_persists(qapp, isolated_appdata):
    win = expanded_window(qapp)
    full = win.height()
    assert len(chevrons(win)) == 3
    chevrons(win)[1].click()          # Codex
    qapp.processEvents()
    assert win.settings.collapsed == ["codex"]
    assert saved(isolated_appdata)["collapsed"] == ["codex"]
    assert win.height() < full and win.height() == win.sizeHint().height()
    collapsed_btn = [b for b in chevrons(win) if b.text() == "▸"]
    assert len(collapsed_btn) == 1
    collapsed_btn[0].click()          # expand it again
    qapp.processEvents()
    assert win.settings.collapsed == [] and win.height() == full
    win.close()


def test_expand_all_and_collapse_all(qapp, isolated_appdata):
    win = expanded_window(qapp, collapsed=["grok"])
    win.set_all_collapsed(True)
    qapp.processEvents()
    assert sorted(win.settings.collapsed) == ["claude", "codex", "grok"]
    assert all(b.text() == "▸" for b in chevrons(win))
    all_collapsed = win.height()
    win.set_all_collapsed(False)
    qapp.processEvents()
    assert win.settings.collapsed == [] and win.height() > all_collapsed
    assert saved(isolated_appdata)["collapsed"] == []
    texts = [a.text() for a in win.build_menu().actions()]
    assert "Expand all" in texts and "Collapse all" in texts
    win.close()


def test_collapse_state_does_not_change_compact_mode(qapp):
    compact_plain = WidgetWindow(WidgetSettings())
    compact_plain.render(three_snapshot())
    compact_collapsed = WidgetWindow(WidgetSettings(collapsed=["claude", "codex"]))
    compact_collapsed.render(three_snapshot())
    assert compact_plain.width() == compact_collapsed.width() == COMPACT_WIDTH
    assert compact_plain.sizeHint().height() == compact_collapsed.sizeHint().height()
    assert chevrons(compact_collapsed) == []


def test_tray_cards_have_no_chevron(qapp):
    card = build_card(three_snapshot().providers[0])
    assert card.findChildren(QToolButton, "chevron") == []


def test_collapsed_setting_validation(isolated_appdata):
    d = isolated_appdata / "quota-tray"
    d.mkdir(parents=True, exist_ok=True)
    (d / "widget.json").write_text(json.dumps({"collapsed": ["codex", 5, None, "grok"]}))
    assert load_settings().collapsed == ["codex", "grok"]
    (d / "widget.json").write_text(json.dumps({"collapsed": "codex"}))
    assert load_settings().collapsed == []


# ---------- cats mode ----------
from quota_widget.cats_view import CatCanvas, CatsView  # noqa: E402


def dbl(win):
    mouse(win, QEvent.Type.MouseButtonDblClick, (win.x() + 20, win.y() + 20))
    qapp_ = QApplication.instance()
    qapp_.processEvents()


def test_cats_mode_shows_one_cat_per_provider_with_details(qapp):
    win = WidgetWindow(WidgetSettings(mode="cats"), persist=False)
    win.render(three_snapshot())
    win.show()
    qapp.processEvents()
    canvases = win.findChildren(CatCanvas)
    assert [c.key for c in canvases] == ["claude", "codex", "grok"]
    assert [c.state for c in canvases] == ["alert", "asleep", "deep"]  # 60/5/0% left
    assert "weekly" in canvases[0].parentWidget().toolTip()  # hover shows the full card details
    assert win.height() == win.sizeHint().height() and win.width() == win.sizeHint().width()
    win.close()


def test_double_click_in_cats_toggles_cats_and_compact(qapp, isolated_appdata):
    win = make(qapp, mode="cats")
    dbl(win)
    assert win.settings.mode == "compact"
    assert saved(isolated_appdata)["compact_partner"] == "cats"
    dbl(win)
    assert win.settings.mode == "cats"
    dbl(win)
    assert win.settings.mode == "compact"
    win.close()
    # Survives a restart: compact reached from cats still double-clicks back to cats.
    from quota_widget.settings import load_settings as _load
    win2 = WidgetWindow(_load())
    win2.render(snapshot())
    win2.show()
    qapp.processEvents()
    dbl(win2)
    assert win2.settings.mode == "cats"
    win2.close()


def test_expanded_compact_double_click_unchanged_after_menu_compact(qapp, isolated_appdata):
    win = make(qapp, mode="cats")
    win.set_mode("compact", partner="expanded")   # "Compact" chosen from the menu = entered normally
    dbl(win)
    assert win.settings.mode == "expanded"
    dbl(win)
    assert win.settings.mode == "compact"
    dbl(win)
    assert win.settings.mode == "expanded"
    win.close()


def test_menu_offers_cats_and_reduce_motion(qapp):
    win = make(qapp)
    texts = [a.text() for a in win.build_menu().actions()]
    assert "Cats" in texts and "Reduce motion" in texts
    win.close()


def test_reduce_motion_freezes_animation_and_persists(qapp, isolated_appdata):
    win = make(qapp, mode="cats")
    view = win.findChildren(CatsView)[0]
    assert view.timer.isActive()
    win.set_reduce_motion(True)
    assert not view.timer.isActive() and load_settings().reduce_motion is True
    win.render(three_snapshot())                    # a redraw keeps it frozen
    qapp.processEvents()
    assert not win.findChildren(CatsView)[0].timer.isActive()
    win.close()


def test_cats_two_by_two_when_narrow(qapp):
    view = CatsView(three_snapshot().providers, win_theme(), columns=2)
    grid = view.layout()
    positions = [grid.getItemPosition(i)[:2] for i in range(grid.count())]
    assert positions == [(0, 0), (0, 1), (1, 0)]


def win_theme():
    from quota_ui.cards import DARK
    return DARK


def test_legacy_expanded_flag_migrates_to_mode(isolated_appdata):
    d = isolated_appdata / "quota-tray"
    d.mkdir(parents=True, exist_ok=True)
    (d / "widget.json").write_text(json.dumps({"expanded": True}))
    assert load_settings().mode == "expanded"
    (d / "widget.json").write_text(json.dumps({"mode": "cats", "compact_partner": "bogus", "reduce_motion": "yes"}))
    s = load_settings()
    assert (s.mode, s.compact_partner, s.reduce_motion) == ("cats", "expanded", False)


# ---------- settings dialog, live config, Win+D ----------
def test_pinned_widget_reappears_when_desktop_is_shown(qapp, monkeypatch):
    win = make(qapp, z_order="desktop")
    assert win._desktop_watch.isActive()
    shown = []
    monkeypatch.setattr(win, "_show_over_desktop", lambda: shown.append(1))
    fg = ["Chrome_WidgetWin_1"]
    monkeypatch.setattr(win, "_foreground_class", lambda: fg[0])
    win._check_desktop_shown()
    assert shown == []
    fg[0] = "WorkerW"                       # Win+D: the desktop became the foreground window
    win._check_desktop_shown()
    win._check_desktop_shown()              # only once per visit to the desktop
    assert shown == [1]
    win.set_z_order("top")
    assert not win._desktop_watch.isActive()
    win.close()


def test_settings_dialog_round_trip(qapp, isolated_appdata):
    from quota_ui.settings_dialog import SettingsDialog, parse_thresholds
    from quota_core.config import load_config
    assert parse_thresholds("20, 10%, x, 150, 5") == [20, 10, 5]
    d = SettingsDialog()
    d.names["codex"].setText("ChatGPT")
    d.enabled["grok"].setChecked(False)
    d.interval.setValue(15)
    d.short.setText("25")
    d.quiet.setChecked(True)
    d.ka_grok.setChecked(False)
    assert not d.claude_resets.isChecked()   # unofficial: off by default
    d.save()
    cfg = load_config()
    assert cfg["providers"]["codex"]["display_name"] == "ChatGPT"
    assert cfg["providers"]["grok"]["enabled"] is False
    assert cfg["refresh_interval_minutes"] == 15 and cfg["alerts"]["short_thresholds"] == [25]
    assert cfg["alerts"]["quiet_hours"]["enabled"] is True and cfg["keepalive"]["grok"]["enabled"] is False
    assert cfg["providers"]["claude"]["reset_info"] is False


@pytest.mark.parametrize("answer,expect", [("Yes", True), ("No", False)])
def test_unofficial_source_needs_confirmation(qapp, isolated_appdata, monkeypatch, answer, expect):
    from PySide6.QtWidgets import QMessageBox
    from quota_ui import settings_dialog
    from quota_core.config import load_config
    shown = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text, *a: shown.append(text) or getattr(QMessageBox.StandardButton, answer))
    d = settings_dialog.SettingsDialog()
    d.claude_resets.click()
    assert len(shown) == 1 and "Claude Code" in shown[0] and "terms" in shown[0]
    assert d.claude_resets.isChecked() is expect
    d.save()
    cfg = load_config()
    assert cfg["providers"]["claude"]["reset_info"] is expect
    d.claude_resets.click()                  # turning it off never asks
    assert len(shown) == (1 if expect else 2)


def test_service_applies_config_changes_live(qapp, isolated_appdata, monkeypatch):
    import json
    import uuid
    from quota_core import service as svc_mod
    from quota_core.config import config_path, load_config
    monkeypatch.setattr(svc_mod, "CONFIG_CHECK_SECONDS", 0)
    cfg = load_config()
    svc = svc_mod.CoreService(cfg, "t", lambda *a: None, leader_name=rf"Local\QT-{uuid.uuid4().hex}",
                              hooks=[], watch_config=True)
    svc.tick()
    assert svc.is_poller and svc.interval == 600
    data = json.loads(config_path().read_text())
    data["refresh_interval_minutes"] = 20
    data["providers"]["grok"]["display_name"] = "Grok Build"
    data["providers"]["codex"]["enabled"] = False
    import os, time
    config_path().write_text(json.dumps(data))
    os.utime(config_path(), (time.time() + 5, time.time() + 5))
    assert svc.tick() is True                          # reloaded: front end redraws
    assert svc.interval == 1200
    names = [m[1] for m in svc.store.meta]
    assert "Grok Build" in names and all(m[0] != "codex" for m in svc.store.meta)
    assert [p.key for p in svc.engine.providers] == ["claude", "grok"]
    svc.shutdown()
