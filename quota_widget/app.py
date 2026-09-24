"""Widget front end: runs on its own, with or without the tray app."""
from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from quota_core.config import load_config, setup_logging
from quota_core.leader import single_instance
from quota_core.service import CoreService

from .settings import load_settings
from .window import WidgetWindow

log = logging.getLogger(__name__)
INSTANCE_MUTEX = r"Local\QuotaWidgetSingleInstance"
TICK_MS = 1000
REDRAW_SECONDS = 30


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#2EA043"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(0, 0, 64, 64), 13, 13)
    p.setPen(QColor("white"))
    f = p.font()
    f.setPixelSize(44)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "Q")
    p.end()
    return QIcon(pm)


class WidgetApp:
    def __init__(self):
        self.core = CoreService(load_config(), "widget", notify=self.notify, watch_config=True)
        self.window = WidgetWindow(load_settings(), on_refresh=self.refresh, on_close=QApplication.quit)
        self.message, self.message_until = "", 0.0
        self._last_redraw = 0.0
        self._toast: QSystemTrayIcon | None = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)

    def notify(self, title: str, message: str):
        """Toasts when the widget is the poller. Windows needs a notification-area icon to show them,
        so one is shown briefly for each alert."""
        if self._toast is None:
            self._toast = QSystemTrayIcon(app_icon())
            self._toast.setToolTip("Quota Widget")
        self._toast.show()
        self._toast.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 10000)
        QTimer.singleShot(15000, self._toast.hide)

    def status(self, snap) -> str:
        if self.message and time.monotonic() < self.message_until:
            return self.message
        return ""

    def start(self):
        self.core.tick()
        self.redraw()
        self.window.place_initial()
        self.window.show()
        self.window._lower_if_pinned()
        self.timer.start(TICK_MS)

    def tick(self):
        if self.core.tick() or time.monotonic() - self._last_redraw >= REDRAW_SECONDS:
            self.redraw()

    def redraw(self):
        self._last_redraw = time.monotonic()
        snap = self.core.snapshot()
        self.window.render(snap, self.status(snap))

    def refresh(self):
        self.message, self.message_until = self.core.request_refresh(), time.monotonic() + 8
        self.redraw()

    def shutdown(self):
        self.timer.stop()
        self.core.shutdown()


def main() -> int:
    from quota_ui import app_actions
    if app_actions.handle_uninstall_arg():
        return 0
    guard = single_instance(INSTANCE_MUTEX)
    if guard is None:
        return 0
    setup_logging()
    log.info("quota-widget starting")
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    widget = WidgetApp()
    app.aboutToQuit.connect(widget.shutdown)
    widget.start()
    code = app.exec()
    log.info("quota-widget exiting")
    return code
