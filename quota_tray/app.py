"""Tray front end: pystray icon (own thread) + Qt popup panel (main thread) over quota_core."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import pystray
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from quota_core import startup
from quota_core.config import app_dir, keepalive_enabled, load_config, save_setting, setup_logging
from quota_core.display import icon_value, tooltip
from quota_core.leader import single_instance
from quota_core.service import CoreService

from quota_ui.report_window import open_report
from quota_ui import app_actions
from quota_ui.settings_dialog import open_settings

from .icon import render
from .panel import Panel

log = logging.getLogger(__name__)
INSTANCE_MUTEX = r"Local\QuotaTraySingleInstance"
RUN_VALUE, RUN_SCRIPT = "QuotaTray", "run_tray.pyw"
TICK_MS = 1000
REDRAW_SECONDS = 30  # keeps countdowns, tooltip and "reset since" states current


class Bridge(QObject):
    """pystray callbacks run on the tray thread; they reach the Qt thread only through these signals."""
    show_panel = Signal()
    refresh = Signal()
    report = Signal()
    settings = Signal()
    install = Signal()
    update = Signal()
    quit = Signal()


class TrayApp:
    def __init__(self):
        self.cfg = load_config()
        self.bridge = Bridge()
        self.icon = pystray.Icon("quota-tray", render(None), "Quota Tray", menu=self._menu())
        self.core = CoreService(self.cfg, "tray", notify=lambda title, msg: self.icon.notify(msg, title), watch_config=True)
        self.panel = Panel()
        self.message = ""          # transient status (e.g. "Checked recently…")
        self.message_until = 0.0
        self._last_redraw = 0.0

        self.bridge.show_panel.connect(self.toggle_panel)
        self.bridge.refresh.connect(self.manual_refresh)
        self.bridge.quit.connect(self.quit)
        self.bridge.report.connect(open_report)
        self.bridge.settings.connect(open_settings)
        self.bridge.install.connect(lambda: app_actions.run_install("QuotaTray.exe"))
        self.bridge.update.connect(lambda: app_actions.open_update(self._update))
        self._update = None
        self.panel.refresh_clicked.connect(self.manual_refresh)

        self._tray_thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)

    def _menu(self):
        b = self.bridge
        return pystray.Menu(
            pystray.MenuItem("Show usage", lambda: b.show_panel.emit(), default=True),
            pystray.MenuItem("Refresh now", lambda: b.refresh.emit()),
            pystray.MenuItem("Usage report…", lambda: b.report.emit()),
            pystray.MenuItem("Settings…", lambda: b.settings.emit()),
            pystray.MenuItem(lambda _: f"Download update {self._update['version']}…" if self._update else "",
                             lambda: b.update.emit(), visible=lambda _: bool(self._update)),
            pystray.MenuItem("Install to this PC…", lambda: b.install.emit(),
                             visible=lambda _: app_actions.can_install()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Keepalive", pystray.Menu(
                pystray.MenuItem("Claude Code", lambda: toggle_keepalive("claude"),
                                 checked=lambda _: keepalive_enabled("claude")),
                pystray.MenuItem("Grok CLI", lambda: toggle_keepalive("grok"),
                                 checked=lambda _: keepalive_enabled("grok")),
            )),
            pystray.MenuItem("Start with Windows", self._toggle_startup,
                             checked=lambda _: startup.is_enabled(RUN_VALUE)),
            pystray.MenuItem("Open data folder", lambda: os.startfile(app_dir())),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: b.quit.emit()),
        )

    def _toggle_startup(self, icon, _item):
        try:
            startup.set_enabled(RUN_VALUE, not startup.is_enabled(RUN_VALUE), RUN_SCRIPT)
        except OSError as exc:
            log.warning("start-with-Windows toggle failed: %s", exc)
        icon.update_menu()

    def start(self):
        self._tray_thread.start()
        self.tick()
        self.timer.start(TICK_MS)

    def tick(self):
        changed = self.core.tick()
        if changed or time.monotonic() - self._last_redraw >= REDRAW_SECONDS:
            self.refresh_views()

    def status_text(self, snap) -> str:
        if self.message and time.monotonic() < self.message_until:
            return self.message
        if snap.busy:
            return "Refreshing…"
        return f"Updated {snap.last_refresh.astimezone():%H:%M}" if snap.last_refresh else ""

    def toggle_panel(self):
        if not self.panel.isVisible():
            snap = self.core.snapshot()
            self.panel.render(snap.providers, self.status_text(snap))
        self.panel.toggle()

    def manual_refresh(self):
        self.message, self.message_until = self.core.request_refresh(), time.monotonic() + 8
        if not self.panel.isVisible():
            snap = self.core.snapshot()
            self.panel.render(snap.providers, self.status_text(snap))
            self.panel.show_near_tray()
        self.refresh_views()

    def refresh_views(self):
        self._last_redraw = time.monotonic()
        snap = self.core.snapshot()
        readings = [s.reading for s in snap.providers if s.reading]
        self.icon.icon = render(icon_value(readings))
        self.icon.title = tooltip(readings)
        if snap.update != self._update:          # show/hide "Download update…"
            self._update = snap.update
            self.icon.update_menu()
        if self.panel.isVisible():
            self.panel.render(snap.providers, self.status_text(snap))

    def quit(self):
        self.timer.stop()
        self.core.shutdown()
        try:
            self.icon.stop()
        finally:
            QApplication.quit()


def toggle_keepalive(cli: str) -> None:
    """Stored in config.json; the poller (tray or widget) re-reads it before every keepalive run."""
    save_setting(["keepalive", cli, "enabled"], not keepalive_enabled(cli))


def main() -> int:
    if app_actions.handle_uninstall_arg():     # run by Apps & features
        return 0
    guard = single_instance(INSTANCE_MUTEX)
    if guard is None:
        return 0
    setup_logging()
    log.info("quota-tray starting")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    tray = TrayApp()
    tray.start()
    code = app.exec()
    log.info("quota-tray exiting")
    return code
