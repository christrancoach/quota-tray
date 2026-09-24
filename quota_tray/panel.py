"""Tray popup panel: one card per provider, opened from the tray icon."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

from quota_core.engine import ProviderState
from quota_ui.cards import DARK, build_card, label, row, stylesheet

WIDTH = 380


class Panel(QWidget):
    refresh_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(WIDTH)
        self.hidden_at = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.body = QWidget()
        self.body.setObjectName("panel")
        self.body.setStyleSheet(stylesheet(DARK))
        outer.addWidget(self.body)

        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        head = label("LLM quota", name="title")
        self.status = label("", muted=True)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_clicked.emit)
        lay.addLayout(row(head, self.status, self.refresh_btn, stretch_after=1))

        self.cards_host = QWidget()
        self.cards = QVBoxLayout(self.cards_host)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.cards_host)
        self.cards_host.setStyleSheet("background: transparent;")
        lay.addWidget(self.scroll)

    def render(self, states: list[ProviderState], status: str):
        self.status.setText(status)
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for st in states:
            self.cards.addWidget(build_card(st, DARK))
        self.cards.addStretch(1)
        self._fit()
        QTimer.singleShot(0, self._fit)  # re-measure once cards added while visible are shown

    def _fit(self):
        self.cards_host.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        want = self.cards_host.sizeHint().height() + 58
        self.setFixedHeight(min(want, avail.height() - 24))

    def toggle(self):
        if self.isVisible():
            self.hide()
        elif time.monotonic() - self.hidden_at > 0.3:  # the tray click itself deactivated us
            self.show_near_tray()

    def show_near_tray(self):
        self._fit()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = min(max(QCursor.pos().x() - WIDTH // 2, avail.left() + 12), avail.right() - WIDTH - 12)
        y = avail.bottom() - self.height() - 12
        if QCursor.pos().y() < avail.top() + avail.height() / 2:  # taskbar at top
            y = avail.top() + 12
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.activateWindow()
        _force_foreground(self)

    def event(self, e):
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
            self.hidden_at = time.monotonic()
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(e)


def _force_foreground(w: QWidget):
    """Tray-launched windows don't always get focus on Windows; without it, click-away can't close us."""
    try:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(int(w.winId()))
    except (OSError, AttributeError):
        pass
