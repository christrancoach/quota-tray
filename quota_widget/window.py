"""Desktop widget window: compact rows or expanded cards over a state snapshot.

Frameless, rounded, semi-transparent; hidden from the taskbar and Alt+Tab (Qt.Tool).
Drag with the left button, double-click to switch compact/expanded (or compact/cats when cats mode
is in use), right-click for the menu.
All choices persist to widget.json.
"""
from __future__ import annotations

import ctypes
from typing import Callable

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication
from PySide6.QtWidgets import QGridLayout, QMenu, QVBoxLayout, QWidget

from quota_core import startup
from quota_core.config import keepalive_enabled, save_setting
from quota_core.display import (GRAY, NEUTRAL, SHORT_NAMES, ago, color_for_left, countdown, reset_badge,
                                reset_since_last_read, spoken_summary)
from quota_core.engine import ProviderState
from quota_core.models import STATUS_OK, STATUS_STALE
from quota_core.store import Snapshot
from quota_ui.cards import (DARK, LIGHT, Bar, Theme, build_card, build_collapsed_card, hover_detail, label,
                            stylesheet)

from quota_ui.report_window import open_report
from quota_ui import app_actions
from quota_ui.settings_dialog import open_settings

from .cats_view import CatsView
from .geometry import is_reachable, recover_position
from .settings import OPACITIES, WidgetSettings, save_settings

COMPACT_WIDTH, EXPANDED_WIDTH = 300, 380
NARROW_SCREEN = 600  # px; below this cats mode lays out 2x2
BODY_ALPHA = 0.86  # background translucency; the opacity setting applies on top of this
RUN_VALUE, RUN_SCRIPT = "QuotaWidget", "run_widget.pyw"
HWND_BOTTOM, SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 1, 0x1, 0x2, 0x10
HWND_TOP, SWP_SHOWWINDOW, SW_SHOWNOACTIVATE = 0, 0x40, 4
DESKTOP_CLASSES = {"Progman", "WorkerW"}   # the desktop, as the foreground window after Win+D
KEY_HELP = ("Keys: Enter or Space switches the view, arrow keys move the widget (Shift for small steps), "
            "R refreshes, the Menu key or Shift+F10 opens the menu.")


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def system_theme() -> Theme:
    scheme = QGuiApplication.styleHints().colorScheme()
    return LIGHT if scheme == Qt.ColorScheme.Light else DARK


def compact_row(grid: QGridLayout, r_idx: int, st: ProviderState, theme: Theme) -> None:
    """name | weekly % left | thin weekly bar | reset countdown (two grid rows: text, then bar)."""
    r = st.reading
    fresh = bool(r and r.status == STATUS_OK)
    badge = reset_badge(r) if r is not None else None
    name = label(SHORT_NAMES.get(st.name, st.name) + (f"  {badge}" if badge else ""))
    name.setToolTip(hover_detail(st))

    if r is None or r.weekly_left_pct is None:
        pct, when, bar = label("–", muted=True), label("login needed" if r and r.status == STATUS_STALE
                                                         else "waiting…", muted=True), Bar(None, GRAY, 4, theme.track)
    elif reset_since_last_read(r):
        pct = label("reset", muted=True)
        pct.setStyleSheet(f"color: {NEUTRAL};")
        when, bar = label("since last read", muted=True), Bar(None, NEUTRAL, 4, theme.track)
    else:
        left = r.weekly_left_pct
        pct = label(f"{left:.0f}%", name="title")
        pct.setStyleSheet(f"color: {color_for_left(left) if fresh else theme.muted};")
        if fresh:
            when = label(countdown(r.weekly_resets_at), muted=True)
        else:  # stale: say how old the number is instead of a countdown
            when = label(f"as of {ago(r.last_updated)}", name="warn")
        bar = Bar(left, color_for_left(left) if fresh else GRAY, 4, theme.track)
    if not fresh:
        name.setProperty("dim", True)

    pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    when.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    base = r_idx * 2
    grid.addWidget(name, base, 0)
    grid.addWidget(pct, base, 1)
    grid.addWidget(when, base, 2)
    grid.addWidget(bar, base + 1, 0, 1, 3)


class WidgetWindow(QWidget):
    def __init__(self, settings: WidgetSettings | None = None, *,
                 on_refresh: Callable[[], None] = lambda: None, on_close: Callable[[], None] = lambda: None,
                 persist: bool = True):
        super().__init__(None)
        self.settings = settings or WidgetSettings()
        self.on_refresh, self.on_close, self.persist = on_refresh, on_close, persist
        self.status = ""
        self._drag_from: QPoint | None = None
        self._drag_moved = False
        self._snapshot: Snapshot | None = None
        self._cats: CatsView | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # keyboard control once clicked or focused
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setObjectName("panel")
        outer.addWidget(self.body)
        self.content = QVBoxLayout(self.body)
        self._apply_flags()
        self.setWindowOpacity(self.settings.opacity / 100)
        self._desktop_watch = QTimer(self)          # pinned mode: come back after Win+D
        self._desktop_watch.timeout.connect(self._check_desktop_shown)
        self._desktop_was_foreground = False
        self._sync_desktop_watch()
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _s: self._rerender())
        QGuiApplication.instance().screenRemoved.connect(lambda _s: QTimer.singleShot(0, self.ensure_on_screen))

    # ---------- settings ----------
    @property
    def expanded(self) -> bool:
        return self.settings.expanded

    @property
    def theme(self) -> Theme:
        return {"dark": DARK, "light": LIGHT}.get(self.settings.theme) or system_theme()

    def _save(self):
        if self.persist:
            save_settings(self.settings)

    def _apply_flags(self):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint  # Tool: no taskbar button, no Alt+Tab
        if self.settings.z_order == "top":
            flags |= Qt.WindowType.WindowStaysOnTopHint
        elif self.settings.z_order == "desktop":
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        visible = self.isVisible()
        self.setWindowFlags(flags)
        if visible:
            self.show()
        self._lower_if_pinned()

    def _lower_if_pinned(self):
        """Pinned mode: keep the widget under normal windows even after it is clicked."""
        if self.settings.z_order == "desktop" and self.isVisible():
            try:
                ctypes.windll.user32.SetWindowPos(int(self.winId()), HWND_BOTTOM, 0, 0, 0, 0,
                                                  SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            except (OSError, AttributeError):
                pass

    def set_z_order(self, mode: str):
        self.settings.z_order = mode
        self._apply_flags()
        self._sync_desktop_watch()
        self._save()

    # ---------- pinned to desktop: survive Win+D ----------
    def _sync_desktop_watch(self):
        if self.settings.z_order == "desktop":
            self._desktop_watch.start(700)
        else:
            self._desktop_watch.stop()

    @staticmethod
    def _foreground_class() -> str:
        try:
            u32 = ctypes.windll.user32
            buf = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(u32.GetForegroundWindow(), buf, 64)
            return buf.value
        except (OSError, AttributeError):
            return ""

    def _check_desktop_shown(self):
        """When the desktop itself becomes the foreground window (Win+D, or clicking the wallpaper),
        show the pinned widget on top of it without taking focus."""
        on_desktop = self._foreground_class() in DESKTOP_CLASSES
        if on_desktop and not self._desktop_was_foreground:
            self._show_over_desktop()
        self._desktop_was_foreground = on_desktop

    def _show_over_desktop(self):
        if not self.isVisible():
            self.show()
        try:
            hwnd = int(self.winId())
            u32 = ctypes.windll.user32
            u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            u32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        except (OSError, AttributeError):
            pass

    def set_opacity(self, pct: int):
        self.settings.opacity = pct
        self.setWindowOpacity(pct / 100)
        self._save()

    def set_mode(self, mode: str, partner: str | None = None):
        """Switch display mode. `partner` is where double-click from compact leads back to."""
        if partner:
            self.settings.compact_partner = partner
        if mode == self.settings.mode and not partner:
            return
        self.settings.mode = mode
        self._rerender()
        self.ensure_on_screen()
        self._save()

    def toggle_by_double_click(self):
        """cats <-> compact when cats mode is in play; otherwise expanded <-> compact, as before."""
        mode = self.settings.mode
        if mode == "cats":
            self.set_mode("compact", partner="cats")
        elif mode == "expanded":
            self.set_mode("compact", partner="expanded")
        else:
            self.set_mode(self.settings.compact_partner)

    def set_reduce_motion(self, on: bool):
        self.settings.reduce_motion = on
        if self._cats is not None:
            self._cats.set_reduce_motion(on)
        self._save()

    def toggle_collapsed(self, key: str):
        c = self.settings.collapsed
        self.settings.collapsed = [k for k in c if k != key] if key in c else c + [key]
        self._rerender()
        self._save()

    def set_all_collapsed(self, collapsed: bool):
        keys = [p.key for p in self._snapshot.providers] if self._snapshot else []
        self.settings.collapsed = keys if collapsed else []
        self._rerender()
        self._save()

    def set_theme(self, theme: str):
        self.settings.theme = theme
        self._rerender()
        self._save()

    # ---------- placement ----------
    @staticmethod
    def _screens() -> tuple[list, tuple]:
        rects = []
        for s in QGuiApplication.screens():
            g = s.availableGeometry()
            rects.append((g.x(), g.y(), g.width(), g.height()))
        p = QGuiApplication.primaryScreen().availableGeometry()
        return rects, (p.x(), p.y(), p.width(), p.height())

    def place_initial(self):
        """Saved position if it is still on some screen, otherwise the primary display."""
        screens, primary = self._screens()
        x, y = recover_position(self.settings.position, (self.width(), self.height()), screens, primary)
        self.move(x, y)

    def ensure_on_screen(self):
        screens, primary = self._screens()
        geo = (self.x(), self.y(), self.width(), self.height())
        if not is_reachable(geo, screens):
            self.move(*recover_position(None, (self.width(), self.height()), screens, primary))
            self._remember_position()

    def _remember_position(self):
        self.settings.x, self.settings.y = self.x(), self.y()
        self._save()

    # ---------- rendering ----------
    def _rerender(self):
        if self._snapshot:
            self.render(self._snapshot, self.status)

    def render(self, snap: Snapshot, status: str = ""):
        self._snapshot, self.status = snap, status
        t = self.theme
        self.body.setStyleSheet(stylesheet(t, panel_bg=_rgba(t.bg, BODY_ALPHA)))
        while self.content.count():
            item = self.content.takeAt(0)
            if item.widget():
                _discard(item.widget())
            elif item.layout():
                _clear(item.layout())
        self._cats = None
        if self.settings.mode == "cats":
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.content.setContentsMargins(12, 10, 12, 8)
            self.content.setSpacing(4)
            self._cats = CatsView(snap.providers, t, columns=self._cat_columns(),
                                  reduce_motion=self.settings.reduce_motion)
            self.content.addWidget(self._cats)
        elif self.expanded:
            self.setFixedWidth(EXPANDED_WIDTH)
            self.content.setContentsMargins(10, 10, 10, 8)
            self.content.setSpacing(8)
            for st in snap.providers:
                toggle = (lambda k=st.key: self.toggle_collapsed(k))
                self.content.addWidget(build_collapsed_card(st, t, toggle) if st.key in self.settings.collapsed
                                       else build_card(st, t, on_toggle=toggle))
        else:
            self.setFixedWidth(COMPACT_WIDTH)
            self.content.setContentsMargins(14, 12, 14, 8)
            self.content.setSpacing(0)
            host = QWidget()
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(0, 1)
            for i, st in enumerate(snap.providers):
                compact_row(grid, i, st, t)
                grid.setRowMinimumHeight(i * 2 + 1, 12)
            self.content.addWidget(host)
        foot = status or ("Refreshing…" if snap.busy else (
            f"Updated {snap.last_refresh.astimezone():%H:%M}" if snap.last_refresh else "No data yet"))
        footer = label(foot, muted=True)
        footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.content.addSpacing(4)
        self.content.addWidget(footer)
        self.setAccessibleName("Quota widget")
        self.setAccessibleDescription(spoken_summary(snap.providers) + " " + KEY_HELP)
        self._fit_height()
        # Widgets added to an already-visible window are shown on the next event-loop pass, and
        # until then Qt leaves them out of sizeHint(). Size again once they are really there,
        # otherwise a redraw while visible collapses the window to its margins.
        QTimer.singleShot(0, self._fit_height)

    def _cat_columns(self) -> int:
        """Four across normally; 2x2 when the widget sits on a narrow (e.g. portrait) screen."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        return 2 if screen.availableGeometry().width() < NARROW_SCREEN else 4

    def _fit_height(self):
        if self.settings.mode == "cats":  # cats mode sizes to its content both ways
            w = self.sizeHint().width()
            if w > 0:
                self.setFixedWidth(w)
        h = self.sizeHint().height()
        if h > 0:
            self.setFixedHeight(h)

    # ---------- mouse ----------
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = None
            self.toggle_by_double_click()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._drag_moved = False

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            self._drag_moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag_from is not None:
            self._drag_from = None
            if self._drag_moved:
                self._remember_position()
        self._lower_if_pinned()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self._lower_if_pinned)

    # ---------- menu ----------
    def build_menu(self) -> QMenu:
        m = QMenu(self)

        def radio(menu: QMenu, choices, current, setter):
            group = QActionGroup(menu)
            for value, text in choices:
                a = QAction(text, menu, checkable=True, checked=(value == current))
                a.triggered.connect(lambda _c=False, v=value: setter(v))
                group.addAction(a)
                menu.addAction(a)

        radio(m, [("top", "Always on top"), ("normal", "Normal window"), ("desktop", "Pinned to desktop")],
              self.settings.z_order, self.set_z_order)
        m.addSeparator()
        radio(m, [("compact", "Compact"), ("expanded", "Expanded"), ("cats", "Cats")], self.settings.mode,
              lambda v: self.set_mode(v, partner="expanded" if v == "compact" else None))
        m.addAction("Expand all", lambda: self.set_all_collapsed(False))
        m.addAction("Collapse all", lambda: self.set_all_collapsed(True))
        motion = QAction("Reduce motion", m, checkable=True, checked=self.settings.reduce_motion)
        motion.triggered.connect(lambda checked: self.set_reduce_motion(bool(checked)))
        m.addAction(motion)
        opacity = m.addMenu("Opacity")
        radio(opacity, [(p, f"{p}%") for p in OPACITIES], self.settings.opacity, self.set_opacity)
        theme = m.addMenu("Theme")
        radio(theme, [("light", "Light"), ("dark", "Dark"), ("system", "Follow system")],
              self.settings.theme, self.set_theme)
        m.addSeparator()
        m.addAction("Refresh now", self.on_refresh)
        m.addAction("Usage report…", open_report)
        m.addAction("Settings…", open_settings)
        update = self._snapshot.update if self._snapshot else None
        if update:
            m.addAction(f"Download update {update['version']}…", lambda: app_actions.open_update(update))
        if app_actions.can_install():
            m.addAction("Install to this PC…", lambda: app_actions.run_install("QuotaWidget.exe"))
        keep = m.addMenu("Keepalive")
        for cli, text in (("claude", "Claude Code"), ("grok", "Grok CLI")):
            a = QAction(text, keep, checkable=True, checked=keepalive_enabled(cli))
            a.triggered.connect(lambda checked, c=cli: save_setting(["keepalive", c, "enabled"], bool(checked)))
            keep.addAction(a)
        start = QAction("Start with Windows", m, checkable=True, checked=startup.is_enabled(RUN_VALUE))
        start.triggered.connect(lambda checked: startup.set_enabled(RUN_VALUE, bool(checked), RUN_SCRIPT))
        m.addAction(start)
        m.addSeparator()
        m.addAction("Close", self.on_close)
        return m

    def contextMenuEvent(self, e):
        self.build_menu().exec(e.globalPos())

    # ---------- keyboard ----------
    def keyPressEvent(self, e):
        key, shift = e.key(), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        step = 1 if shift else 10
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
        if key in moves:
            dx, dy = moves[key]
            self.move(self.x() + dx, self.y() + dy)
            self._remember_position()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_by_double_click()
        elif key == Qt.Key.Key_Menu or (key == Qt.Key.Key_F10 and shift):
            self.build_menu().exec(self.mapToGlobal(self.rect().center()))
        elif key == Qt.Key.Key_R:
            self.on_refresh()
        else:
            super().keyPressEvent(e)


def _discard(w: QWidget):
    """Detach now (so it stops counting as a child) and delete on the next event-loop pass."""
    w.setParent(None)
    w.deleteLater()


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            _discard(item.widget())
        elif item.layout():
            _clear(item.layout())
