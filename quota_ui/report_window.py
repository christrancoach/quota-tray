"""Usage report window: API-price estimate of what the CLIs' token usage would cost.

Opens instantly from the local index, then refreshes the index (and, weekly, the public price
list) in a background thread and redraws.
"""
from __future__ import annotations

import math
import os
import threading
from datetime import date

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from quota_core.config import app_dir
from quota_core.usage.index import update as update_index
from quota_core.usage.pricing import PriceBook, litellm_path, overrides_path, refresh_litellm
from quota_core.usage.report import ORDER, PROVIDERS, Report, build, fmt_money, fmt_tokens

from .cards import DARK, LIGHT, Theme

# Categorical slots 1-4 of the validated reference palette, in fixed order (adjacent-pair CVD-safe for the
# stacked chart in both modes; slots 1-3 also all-pairs).
SERIES = {"light": {"claude": "#2a78d6", "codex": "#eb6834", "grok": "#1baf7a", "gemini": "#eda100"},
          "dark": {"claude": "#3987e5", "codex": "#d95926", "grok": "#199e70", "gemini": "#c98500"}}
RANGES = [("7 days", 7), ("30 days", 30), ("90 days", 90), ("All time", None)]


def current_theme() -> Theme:
    return LIGHT if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light else DARK


def series_colors(theme: Theme) -> dict[str, str]:
    return SERIES["light" if theme is LIGHT else "dark"]


def _nice_ceiling(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _money_axis(v: float) -> str:
    return f"${v:,.0f}" if v >= 10 or v == 0 else f"${v:,.2f}"


def _day_label(d: str) -> str:
    return date.fromisoformat(d).strftime("%b %d").upper()


class DailyChart(QWidget):
    """Stacked area of daily cost per provider, with a hover crosshair and tooltip."""

    def __init__(self, theme: Theme):
        super().__init__()
        self.theme = theme
        self.report: Report | None = None
        self.hover: int | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # arrow keys step through days
        self.setAccessibleName("Daily cost chart")

    def set_report(self, rep: Report):
        self.report, self.hover = rep, None
        costs = [sum(rep.daily[p][i] for p in rep.daily) for i in range(len(rep.days))]
        if costs and max(costs) > 0:
            peak = max(range(len(costs)), key=costs.__getitem__)
            self.setAccessibleDescription(
                f"Daily cost from {_day_label(rep.days[0].key)} to {_day_label(rep.days[-1].key)}. "
                f"Highest day {date.fromisoformat(rep.days[peak].key):%A %d %B}: {fmt_money(costs[peak])}. "
                "Use the left and right arrow keys to read each day; the table below lists every day.")
        self.update()

    def keyPressEvent(self, e):
        if not self.report or not self.report.days:
            return super().keyPressEvent(e)
        n = len(self.report.days)
        cur = self.hover if self.hover is not None else n - 1
        k = e.key()
        if k == Qt.Key.Key_Left:
            self._set_hover(max(0, cur - 1))
        elif k == Qt.Key.Key_Right:
            self._set_hover(min(n - 1, cur + 1) if self.hover is not None else n - 1)
        elif k == Qt.Key.Key_Home:
            self._set_hover(0)
        elif k == Qt.Key.Key_End:
            self._set_hover(n - 1)
        elif k == Qt.Key.Key_Escape:
            self._set_hover(None)
        else:
            return super().keyPressEvent(e)
        if self.hover is not None:   # announce the day for screen readers
            i = self.hover
            total = sum(self.report.daily[p][i] for p in self.report.daily)
            self.setAccessibleDescription(f"{date.fromisoformat(self.report.days[i].key):%A %d %B}: {fmt_money(total)}")

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.update()

    # geometry
    def _plot(self) -> QRectF:
        return QRectF(64, 10, max(10, self.width() - 64 - 14), max(10, self.height() - 10 - 26))

    def _x(self, i: int, n: int, plot: QRectF) -> float:
        return plot.center().x() if n <= 1 else plot.left() + plot.width() * i / (n - 1)

    def _series(self):
        rep = self.report
        colors = series_colors(self.theme)
        return [(p, PROVIDERS[p], colors[p], rep.daily[p]) for p in ORDER if any(rep.daily.get(p, []))]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        plot = self._plot()
        rep = self.report
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        if self.hasFocus():   # visible keyboard focus
            p.setPen(QPen(QColor(t.muted), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        if rep is None or not rep.days:
            return
        n = len(rep.days)
        series = self._series()
        totals = [sum(s[3][i] for s in series) for i in range(n)]
        step = _nice_ceiling((max(totals) if totals else 0) / 4)   # round tick steps: $500, $1,000...
        ticks = max(1, math.ceil((max(totals) if totals else 0) / step))
        ymax = step * ticks

        def y(v):
            return plot.bottom() - plot.height() * (v / ymax)

        # recessive grid + y labels
        p.setPen(QPen(QColor(t.border), 1))
        for k in range(ticks + 1):
            v = step * k
            yy = y(v)
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(0, yy - 8, plot.left() - 8, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _money_axis(v))
            p.setPen(QPen(QColor(t.border), 1))
        # x labels: first, middle, last
        p.setPen(QColor(t.muted))
        for i in sorted({0, n // 2, n - 1}):
            x = self._x(i, n, plot)
            align = Qt.AlignmentFlag.AlignLeft if i == 0 else (Qt.AlignmentFlag.AlignRight if i == n - 1
                                                                else Qt.AlignmentFlag.AlignHCenter)
            w = 70
            left = x if i == 0 else (x - w if i == n - 1 else x - w / 2)
            p.drawText(QRectF(left, plot.bottom() + 6, w, 16), align, _day_label(rep.days[i].key))
        if not series:
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "No usage in this range")
            return

        # stacked bands, then 2 px surface gaps along each boundary
        cum = [0.0] * n
        uppers = []
        for key, name, color, vals in series:
            upper = [cum[i] + vals[i] for i in range(n)]
            path = QPainterPath(QPointF(self._x(0, n, plot), y(upper[0])))
            for i in range(1, n):
                path.lineTo(self._x(i, n, plot), y(upper[i]))
            for i in range(n - 1, -1, -1):
                path.lineTo(self._x(i, n, plot), y(cum[i]))
            path.closeSubpath()
            c = QColor(color)
            c.setAlpha(225)
            p.fillPath(path, QBrush(c))
            uppers.append(upper)
            cum = upper
        p.setPen(QPen(QColor(t.bg), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for upper in uppers[:-1]:
            p.drawPolyline([QPointF(self._x(i, n, plot), y(upper[i])) for i in range(n)])

        # hover crosshair + tooltip
        if self.hover is not None:
            i = self.hover
            x = self._x(i, n, plot)
            p.setPen(QPen(QColor(t.muted), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            for (key, name, color, vals), upper in zip(series, uppers):
                if vals[i] > 0:
                    p.setPen(QPen(QColor(t.bg), 2))
                    p.setBrush(QColor(color))
                    p.drawEllipse(QPointF(x, y(upper[i])), 4.5, 4.5)
            lines = [(name, color, vals[i]) for key, name, color, vals in series if vals[i] > 0]
            self._tooltip(p, x, plot, rep.days[i].key, lines, totals[i])

    def _tooltip(self, p: QPainter, x: float, plot: QRectF, day: str, lines, total: float):
        t = self.theme
        w, row = 190, 18
        h = 30 + row * (len(lines) + 1)
        left = x + 12 if x + 12 + w < plot.right() else x - 12 - w
        box = QRectF(left, plot.top() + 6, w, h)
        p.setPen(QPen(QColor(t.border), 1))
        p.setBrush(QColor(t.card))
        p.drawRoundedRect(box, 8, 8)
        bold = QFont("Segoe UI", 9)
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(QColor(t.text))
        p.drawText(QRectF(box.left() + 10, box.top() + 6, w - 20, 18), Qt.AlignmentFlag.AlignLeft,
                   date.fromisoformat(day).strftime("%a %d %b %Y"))
        p.setFont(QFont("Segoe UI", 9))
        yy = box.top() + 28
        for name, color, v in lines:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(box.left() + 10, yy + 4, 10, 10), 2, 2)
            p.setPen(QColor(t.text))
            p.drawText(QRectF(box.left() + 26, yy, 90, row), Qt.AlignmentFlag.AlignVCenter, name)
            p.drawText(QRectF(box.left() + 10, yy, w - 20, row), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fmt_money(v))
            yy += row
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(box.left() + 26, yy, 90, row), Qt.AlignmentFlag.AlignVCenter, "Total")
        p.setPen(QColor(t.text))
        p.drawText(QRectF(box.left() + 10, yy, w - 20, row), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   fmt_money(total))

    def mouseMoveEvent(self, e):
        if not self.report or not self.report.days:
            return
        plot = self._plot()
        n = len(self.report.days)
        if not plot.adjusted(-20, 0, 20, 0).contains(e.position()):   # generous hit area
            self._set_hover(None)
            return
        frac = 0.5 if n <= 1 else (e.position().x() - plot.left()) / plot.width()
        self._set_hover(max(0, min(n - 1, round(frac * (n - 1)))))

    def leaveEvent(self, _):
        self._set_hover(None)

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.update()


def _swatch(color: str, size: int = 10) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.end()
    return QIcon(pm)


def _label(text="", size=10, bold=False, color=None, wrap=False) -> QLabel:
    lbl = QLabel(text)
    f = QFont("Segoe UI")
    f.setPointSizeF(size)
    f.setBold(bold)
    lbl.setFont(f)
    if color:
        lbl.setStyleSheet(f"color: {color};")
    lbl.setWordWrap(wrap)
    return lbl


class _Bridge(QObject):
    progress = Signal(int, int)
    done = Signal(str)


class ReportWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("Usage report")
        self.resize(1120, 900)
        self.theme = current_theme()
        self.days: int | None = 30
        self.view = "model"
        self.report: Report | None = None
        self._bridge = _Bridge()
        self._bridge.progress.connect(self._on_progress)
        self._bridge.done.connect(self._on_done)
        self._worker: threading.Thread | None = None
        self._build_ui()
        self.reload()
        self.refresh_from_logs()

    # ---------- layout ----------
    def _build_ui(self):
        t = self.theme
        self.setStyleSheet(f"""
            QWidget#root {{ background: {t.bg}; }}
            QFrame#card {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 10px; }}
            QLabel {{ color: {t.text}; background: transparent; }}
            QPushButton#seg {{ color: {t.muted}; background: transparent; border: 1px solid {t.border};
                               padding: 4px 12px; font-family: 'Segoe UI'; font-size: 12px; }}
            QPushButton#seg:checked {{ color: {t.text}; background: {t.card}; font-weight: 600; }}
            QPushButton#link {{ color: {t.muted}; background: transparent; border: none; text-decoration: underline;
                                font-family: 'Segoe UI'; font-size: 11px; padding: 0; }}
            QTableWidget {{ background: transparent; border: none; color: {t.text}; font-family: 'Segoe UI';
                            font-size: 13px; }}
            QTableWidget::item {{ border-bottom: 1px solid {t.border}; padding: 6px 4px; }}
            QHeaderView {{ background: transparent; border: none; }}
            QTableCornerButton::section {{ background: transparent; border: none; }}
            QHeaderView::section {{ background: transparent; color: {t.muted}; border: none;
                                    border-bottom: 1px solid {t.border}; padding: 6px 4px;
                                    font-family: 'Segoe UI'; font-size: 12px; }}
            QScrollArea {{ background: {t.bg}; border: none; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        root = QWidget()
        root.setObjectName("root")
        scroll.setWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(24, 18, 24, 24)
        lay.setSpacing(18)

        # top bar: status + range filter (filters in one row above the charts)
        bar = QHBoxLayout()
        self.status = _label("", 9, color=t.muted)
        bar.addWidget(self.status, 1)
        self.range_group = QButtonGroup(self)
        for text, days in RANGES:
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(days == self.days)
            b.clicked.connect(lambda _c=False, d=days: self.set_range(d))
            self.range_group.addButton(b)
            bar.addWidget(b)
        lay.addLayout(bar)

        # hero + providers | chart
        top = QHBoxLayout()
        top.setSpacing(28)
        left = QVBoxLayout()
        self.hero = _label("$0.00", 30, bold=True)
        self.hero_sub = _label("", 10, color=t.muted)
        left.addWidget(self.hero)
        left.addWidget(self.hero_sub)
        left.addSpacing(10)
        self.providers_box = QVBoxLayout()
        self.providers_box.setSpacing(14)
        left.addLayout(self.providers_box)
        left.addStretch(1)
        leftw = QWidget()
        leftw.setLayout(left)
        leftw.setFixedWidth(400)
        top.addWidget(leftw)
        right = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(_label("Daily cost", 11, bold=True))
        head.addStretch(1)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(14)
        head.addLayout(self.legend)
        right.addLayout(head)
        self.chart = DailyChart(t)
        right.addWidget(self.chart, 1)
        top.addLayout(right, 1)
        lay.addLayout(top)

        # totals
        lay.addWidget(_label("Totals", 11, bold=True))
        self.tiles = QGridLayout()
        self.tiles.setHorizontalSpacing(24)
        lay.addLayout(self.tiles)

        # breakdown (the table view of the chart)
        bh = QHBoxLayout()
        bh.addWidget(_label("Breakdown", 11, bold=True))
        bh.addStretch(1)
        self.view_group = QButtonGroup(self)
        for text, key in (("Model", "model"), ("Day", "day")):
            b = QPushButton(text)
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(key == self.view)
            b.clicked.connect(lambda _c=False, k=key: self.set_view(k))
            self.view_group.addButton(b)
            bh.addWidget(b)
        lay.addLayout(bh)
        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.table)

        # footnote
        self.note = _label("", 9, color=t.muted, wrap=True)
        lay.addWidget(self.note)
        links = QHBoxLayout()
        edit = QPushButton("Edit prices (pricing.json)")
        edit.setObjectName("link")
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.clicked.connect(self._open_prices)
        links.addWidget(edit)
        links.addStretch(1)
        lay.addLayout(links)

    # ---------- data ----------
    def set_range(self, days):
        self.days = days
        self.reload()

    def set_view(self, view):
        self.view = view
        self._fill_table()

    def reload(self):
        self.report = build(self.days, prices=PriceBook())
        self._render()

    def refresh_from_logs(self):
        if self._worker and self._worker.is_alive():
            return
        self.status.setText("Updating from logs…")

        def work():
            try:
                refresh_litellm()
                stats = update_index(lambda i, n: self._bridge.progress.emit(i, n))
                self._bridge.done.emit(f"{stats['changed']} log files read")
            except Exception as exc:  # a broken log must never take the app down
                self._bridge.done.emit(f"Update failed: {exc}")

        self._worker = threading.Thread(target=work, name="usage-index", daemon=True)
        self._worker.start()

    def _on_progress(self, i, n):
        self.status.setText(f"Updating from logs… {i:,} of {n:,} files")

    def _on_done(self, msg):
        self.reload()
        from datetime import datetime
        self.status.setText(f"Updated {datetime.now():%H:%M} · {msg}")

    # ---------- rendering ----------
    def _render(self):
        rep, t = self.report, self.theme
        colors = series_colors(t)
        span = "all time" if self.days is None else f"last {self.days} days"
        self.hero.setText(fmt_money(rep.cost))
        self.hero_sub.setText(f"{rep.sessions:,} sessions · API estimate · {span}")
        _clear(self.providers_box)
        for line in rep.providers:
            box = QVBoxLayout()
            box.setSpacing(2)
            row = QHBoxLayout()
            dot = QLabel()
            dot.setPixmap(_swatch(colors.get(line.key, t.muted)).pixmap(10, 10))
            row.addWidget(dot)
            row.addWidget(_label(PROVIDERS.get(line.key, line.key), 12))
            row.addWidget(_label(f"{line.sessions:,} sessions", 9, color=t.muted))
            row.addStretch(1)
            row.addWidget(_label(fmt_money(line.cost), 12, bold=True))
            box.addLayout(row)
            box.addWidget(_label(f"{rep.share(line.cost):.1%} of cost · {fmt_tokens(line.tokens.total)} tokens",
                                 10, color=t.muted))
            self.providers_box.addLayout(box)
        _clear(self.legend)
        for key in ORDER:
            if any(rep.daily.get(key, [])):
                item = QHBoxLayout()
                item.setSpacing(5)
                sw = QLabel()
                sw.setPixmap(_swatch(colors[key]).pixmap(10, 10))
                item.addWidget(sw)
                item.addWidget(_label(PROVIDERS[key], 9, color=t.muted))
                self.legend.addLayout(item)
        self.chart.set_report(rep)
        _clear(self.tiles)
        tok = rep.tokens
        tiles = [("Processed tokens", fmt_tokens(tok.total)), ("Cache reads", fmt_tokens(tok.cache_read)),
                 ("Cache writes", fmt_tokens(tok.cache_write)), ("Uncached input", fmt_tokens(tok.uncached)),
                 ("Output", fmt_tokens(tok.output)), ("Cache savings", fmt_money(rep.cache_savings))]
        for col, (name, value) in enumerate(tiles):
            self.tiles.addWidget(_label(name, 10, color=t.muted), 0, col)
            self.tiles.addWidget(_label(value, 15, bold=False), 1, col)
        self._fill_table()
        notes = ["Costs are an estimate at API list prices, not what you pay: your subscriptions cost a fixed amount.",
                 "Grok costs come from the Grok CLI's own records; Claude Code and Codex are priced from "
                 + ("LiteLLM's public price list" if PriceBook().has_list else "no price list yet (offline?)")
                 + " plus your overrides.",
                 "Claude Code side calls that aren't written to transcripts (e.g. session titles) aren't included."]
        if rep.unpriced_models:
            notes.append("No price for: " + ", ".join(rep.unpriced_models) + ". Their tokens are counted, their cost "
                         "isn't. Add them to pricing.json to include them.")
        self.note.setText("\n".join(notes))

    def _fill_table(self):
        rep, t = self.report, self.theme
        if rep is None:
            return
        colors = series_colors(t)
        tb = self.table
        if self.view == "model":
            headers = ["Model", "Cost", "Share", "Tokens"]
            rows = [(m.key + ("  (no price)" if not m.priced else ""), colors.get(m.provider), fmt_money(m.cost),
                     f"{rep.share(m.cost):.1%}", fmt_tokens(m.tokens.total)) for m in rep.models]
        else:
            headers = ["Day", "Cost", "Sessions", "Tokens"]
            rows = [(date.fromisoformat(d.key).strftime("%a %d %b %Y"), None, fmt_money(d.cost), f"{d.sessions:,}",
                     fmt_tokens(d.tokens.total)) for d in reversed(rep.days) if d.tokens.total]
        tb.clear()
        tb.setColumnCount(4)
        tb.setHorizontalHeaderLabels(headers)
        tb.setRowCount(len(rows))
        for r, (name, color, *vals) in enumerate(rows):
            item = QTableWidgetItem(name)
            if color:
                item.setIcon(_swatch(color))
            tb.setItem(r, 0, item)
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                tb.setItem(r, c, it)
        hh = tb.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            tb.setColumnWidth(c, 150)
            tb.horizontalHeaderItem(c).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tb.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tb.resizeRowsToContents()
        height = tb.horizontalHeader().height() + sum(tb.rowHeight(r) for r in range(tb.rowCount())) + 4
        tb.setFixedHeight(height)

    def _open_prices(self):
        path = overrides_path()
        if not path.exists():
            path.write_text('{\n  "_example-model": {"input": 1.0, "output": 5.0, "cache_read": 0.1, '
                            '"cache_write": 1.25}\n}\n', encoding="utf-8")
        os.startfile(str(app_dir()))


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)   # detach now; delete on the next event-loop pass
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())


_window: ReportWindow | None = None


def open_report() -> ReportWindow:
    """One report window per app; re-opening brings it to the front and refreshes."""
    global _window
    if _window is None:
        _window = ReportWindow()
    else:
        _window.refresh_from_logs()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window
