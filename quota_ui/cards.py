"""Provider cards, bars and themes shared by the tray popup and the desktop widget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from quota_core.display import (GRAY, NEUTRAL, RESET_SINCE_TEXT, ago, color_for_left, countdown, keepalive_line,
                                reset_badge,
                                reset_lines, reset_since_last_read)
from quota_core.engine import ProviderState, fmt_local
from quota_core.forecast import forecast
from quota_core.models import SOURCE_COOKIE, SOURCE_LOG, STATUS_OK, STATUS_STALE, utcnow

SOURCE_LABELS = {"cli": "CLI login", SOURCE_LOG: "Codex session log", SOURCE_COOKIE: "Browser cookie"}


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    card: str
    card_stale: str
    text: str
    muted: str
    border: str
    track: str
    warn: str
    button: str
    button_hover: str
    reset: str = "#B7791F"   # banked-reset accent (gold)


DARK = Theme("dark", bg="#1B1F24", card="#242A31", card_stale="#1F2328", text="#E6EDF3", muted="#8B949E",
             border="#30363D", track="#353C45", warn="#E3B341", button="#2D333B", button_hover="#373E47",
             reset="#E2B04A")
LIGHT = Theme("light", bg="#F6F8FA", card="#FFFFFF", card_stale="#EEF1F4", text="#1F2328", muted="#656D76",
              border="#D0D7DE", track="#E1E6EB", warn="#9A6700", button="#F3F4F6", button_hover="#E7EAED",
              reset="#9A6A00")


def stylesheet(t: Theme, panel_bg: str | None = None) -> str:
    return f"""
QWidget#panel {{ background: {panel_bg or t.bg}; border: 1px solid {t.border}; border-radius: 10px; }}
QFrame#card {{ background: {t.card}; border-radius: 8px; }}
QFrame#card[stale="true"] {{ background: {t.card_stale}; }}
QLabel {{ color: {t.text}; font-family: 'Segoe UI Variable Text', 'Segoe UI'; font-size: 12px; background: transparent; }}
QLabel[muted="true"] {{ color: {t.muted}; font-size: 11px; }}
QLabel[dim="true"] {{ color: {t.muted}; }}
QLabel#title {{ font-size: 14px; font-weight: 600; }}
QLabel#big {{ font-size: 22px; font-weight: 600; }}
QLabel#chip {{ color: {t.muted}; border: 1px solid {t.border}; border-radius: 6px; padding: 0 6px; font-size: 10px; }}
QLabel#warn {{ color: {t.warn}; font-size: 11px; }}
QLabel#reset {{ color: {t.reset}; border: 1px solid {t.reset}; border-radius: 6px; padding: 0 6px; font-size: 10px;
                font-weight: 600; }}
QLabel#resetline {{ color: {t.reset}; font-size: 11px; }}
QPushButton {{ color: {t.text}; background: {t.button}; border: 1px solid {t.border}; border-radius: 6px;
               padding: 4px 10px; font-family: 'Segoe UI'; font-size: 12px; }}
QPushButton:hover {{ background: {t.button_hover}; }}
QPushButton:disabled {{ color: {t.muted}; }}
QScrollArea {{ background: transparent; border: none; }}
QToolButton#chevron {{ color: {t.muted}; background: transparent; border: none; font-size: 14px;
                       padding: 0; min-width: 16px; max-width: 16px; }}
QToolButton#chevron:hover {{ color: {t.text}; }}
"""


def label(text: str = "", *, name: str | None = None, muted: bool = False, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    if muted:
        lbl.setProperty("muted", True)
    lbl.setWordWrap(wrap)
    return lbl


class Bar(QWidget):
    """A rounded bar showing remaining %. Height is fixed per bar type."""

    def __init__(self, left: float | None, color: str, height: int, track: str = DARK.track):
        super().__init__()
        self.left, self.color, self.track = left, color, track
        self.setAccessibleName(f"{left:.0f} percent left" if left is not None else "no data")
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.track))
        p.drawRoundedRect(r, rad, rad)
        if self.left:
            w = max(r.height(), r.width() * max(0.0, min(100.0, self.left)) / 100)
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(QRectF(r.x(), r.y(), w, r.height()), rad, rad)


def row(*widgets, stretch_after: int | None = None) -> QHBoxLayout:
    r = QHBoxLayout()
    r.setContentsMargins(0, 0, 0, 0)
    r.setSpacing(6)
    for i, w in enumerate(widgets):
        r.addWidget(w)
        if stretch_after == i:
            r.addStretch(1)
    return r


def chevron(collapsed: bool, on_toggle: Callable[[], None]) -> QToolButton:
    b = QToolButton()
    b.setObjectName("chevron")
    b.setText("▸" if collapsed else "▾")
    b.setToolTip("Expand" if collapsed else "Collapse")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(lambda _checked=False: on_toggle())  # clicked passes `checked`; don't forward it
    return b


def build_card(st: ProviderState, theme: Theme = DARK, on_toggle: Callable[[], None] | None = None) -> QFrame:
    """Full card. With on_toggle (the widget) the header gets a collapse chevron; the tray passes none."""
    r = st.reading
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(5)

    fresh = bool(r and r.status == STATUS_OK)
    chips = ([chevron(False, on_toggle)] if on_toggle else []) + [label(st.name, name="title")]
    if r and r.plan:
        chips.append(label(r.plan, name="chip"))
    if r and reset_badge(r):
        chip = label(f"↻ {r.resets_left} reset" + ("s" if r.resets_left != 1 else ""), name="reset")
        chip.setToolTip("\n".join(reset_lines(r)))
        chips.append(chip)
    source = label(SOURCE_LABELS.get(r.source, r.source) if r else "", muted=True)
    lay.addLayout(row(*chips, source, stretch_after=len(chips) - 1))

    if r is None or r.weekly_used_pct is None:
        msg = r.error if r and r.error else "Waiting for first refresh…"
        lay.addWidget(label(msg, name="warn" if r and r.error else None, muted=not (r and r.error), wrap=True))
        return card

    passed = reset_since_last_read(r)
    left = r.weekly_left_pct
    bar_color = color_for_left(left) if fresh else GRAY

    # Weekly headline.
    if passed:
        big = label(RESET_SINCE_TEXT, name="title", wrap=True)
        big.setStyleSheet(f"color: {NEUTRAL};")
        lay.addWidget(big)
        lay.addWidget(Bar(None, NEUTRAL, 10, theme.track))
    else:
        big = label(f"{left:.0f}%", name="big")
        if not fresh:
            big.setProperty("dim", True)
        sub = label("weekly left", muted=True)
        reset = label(f"resets in {countdown(r.weekly_resets_at)} · {fmt_local(r.weekly_resets_at)}"
                      if r.weekly_resets_at else "", muted=True)
        hrow = row(big, sub, reset, stretch_after=1)
        hrow.setAlignment(sub, Qt.AlignmentFlag.AlignBottom)
        hrow.setAlignment(reset, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(hrow)
        lay.addWidget(Bar(left, bar_color, 10, theme.track))
        fc = forecast(st.key, r) if fresh else None
        if fc and fc.text():
            lay.addWidget(label(fc.text(), name="warn" if fc.worrying else None, muted=not fc.worrying, wrap=True))

    # Short window (hidden when the provider has none).
    if r.short_used_pct is not None and not passed:
        short_passed = r.short_resets_at is not None and r.short_resets_at <= utcnow()
        sl = r.short_left_pct
        txt = ("5-hour window: reset since last read" if short_passed and not fresh else
               f"5-hour: {sl:.0f}% left" + (f" · resets in {countdown(r.short_resets_at)}" if r.short_resets_at else ""))
        lay.addSpacing(2)
        lay.addWidget(label(txt, muted=True))
        lay.addWidget(Bar(None if short_passed and not fresh else sl, color_for_left(sl) if fresh else GRAY, 5, theme.track))

    # Secondary bars: per-model limits (Claude), products (Grok).
    if r.breakdown and not passed:
        lay.addSpacing(2)
        for name, used in r.breakdown.items():
            bl = max(0.0, 100.0 - used)
            lay.addLayout(row(label(name, muted=True), label(f"{bl:.0f}% left", muted=True), stretch_after=0))
            lay.addWidget(Bar(bl, color_for_left(bl) if fresh else GRAY, 4, theme.track))

    for line in reset_lines(r):
        lay.addWidget(label(line, name="resetline", wrap=True))
    kl = keepalive_line(st.keepalive)
    if kl and kl[1]:
        lay.addWidget(label(kl[0], muted=True, wrap=True))
    if r.warnings:
        w = label("⚠ The provider's usage response changed; some details may be missing", name="warn", wrap=True)
        w.setToolTip("\n".join(r.warnings))
        lay.addWidget(w)

    # Footer: freshness, errors, re-login hint.
    if fresh:
        if r.error:  # e.g. Codex served from session log
            lay.addWidget(label(r.error, muted=True, wrap=True))
        lay.addWidget(label(f"Updated {ago(r.last_updated)}", muted=True))
    else:
        as_of = f"as of {fmt_local(r.last_updated, '%a %d %b %H:%M')}"
        if r.status == STATUS_STALE:
            action = "log in" if r.error and "to log in" in r.error else "refresh"
            hint = f"Open {st.cli_name} to {action} · {as_of}"
        else:
            hint = f"{r.error} · last success {as_of}"
        lay.addWidget(label(hint, name="warn", wrap=True))

    card.setToolTip(hover_detail(st))
    card.setAccessibleName(f"{st.name} usage card")
    card.setAccessibleDescription(full_details(st))
    if not fresh:
        card.setProperty("stale", True)
    return card


def build_collapsed_card(st: ProviderState, theme: Theme, on_toggle: Callable[[], None]) -> QFrame:
    """One line: chevron, provider name, thin weekly bar, weekly % left. Same colors and stale styling."""
    r = st.reading
    card = QFrame()
    card.setObjectName("card")
    lay = QHBoxLayout(card)
    lay.setContentsMargins(12, 7, 12, 7)
    lay.setSpacing(8)
    fresh = bool(r and r.status == STATUS_OK)
    name = label(st.name, name="title")
    if r is None or r.weekly_left_pct is None:
        pct, bar = label("–", muted=True), Bar(None, GRAY, 4, theme.track)
    elif reset_since_last_read(r):
        pct, bar = label("reset", muted=True), Bar(None, NEUTRAL, 4, theme.track)
        pct.setStyleSheet(f"color: {NEUTRAL};")
    else:
        left = r.weekly_left_pct
        pct = label(f"{left:.0f}%", name="title")
        pct.setStyleSheet(f"color: {color_for_left(left) if fresh else theme.muted};")
        bar = Bar(left, color_for_left(left) if fresh else GRAY, 4, theme.track)
    if not fresh:
        name.setProperty("dim", True)
        card.setProperty("stale", True)
    pct.setMinimumWidth(40)
    pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(chevron(True, on_toggle))
    lay.addWidget(name)
    if r is not None and reset_badge(r):
        lay.addWidget(label(reset_badge(r), name="reset"))
    lay.addWidget(bar, 1, Qt.AlignmentFlag.AlignVCenter)
    lay.addWidget(pct)
    card.setToolTip(hover_detail(st))
    card.setAccessibleName(f"{st.name} usage, collapsed")
    card.setAccessibleDescription(full_details(st))
    return card


def hover_detail(st: ProviderState) -> str:
    r = st.reading
    if r is None:
        return ""
    tip = []
    if r.detail:
        tip.append("Weekly usage by product:")
        tip += [f"  {k}: {v:.0f}%" for k, v in r.detail.items()]
    if r.breakdown:
        tip.append("Secondary limits (used):")
        tip += [f"  {k}: {v:.0f}%" for k, v in r.breakdown.items()]
    fc = forecast(st.key, r) if r.status == STATUS_OK else None
    if fc and fc.text():
        tip.append(fc.text())
    tip += reset_lines(r)
    tip += [f"⚠ Response changed: {w}" for w in r.warnings or []]
    kl = keepalive_line(st.keepalive)
    if kl:
        tip.append(kl[0])
    if r.error:
        tip.append(f"Last error: {r.error}")
    tip.append(f"Source: {SOURCE_LABELS.get(r.source, r.source)}")
    return "\n".join(tip)


def full_details(st: ProviderState) -> str:
    """Everything a full card shows, as tooltip text (used where there is no card, e.g. cats mode)."""
    r = st.reading
    title = st.name + (f" ({r.plan})" if r and r.plan else "")
    if r is None or r.weekly_left_pct is None:
        return f"{title}\n{r.error if r and r.error else 'Waiting for first refresh'}"
    lines = [title]
    if reset_since_last_read(r):
        lines.append(RESET_SINCE_TEXT)
    else:
        weekly = f"{r.weekly_left_pct:.0f}% weekly left"
        if r.weekly_resets_at:
            weekly += f" · resets {fmt_local(r.weekly_resets_at)} (in {countdown(r.weekly_resets_at)})"
        lines.append(weekly)
        if r.short_left_pct is not None:
            short = f"5-hour: {r.short_left_pct:.0f}% left"
            if r.short_resets_at:
                short += f" · resets in {countdown(r.short_resets_at)}"
            lines.append(short)
        for name, used in (r.breakdown or {}).items():
            lines.append(f"{name}: {max(0.0, 100.0 - used):.0f}% left")
    fc = forecast(st.key, r) if r.status == STATUS_OK else None
    if fc and fc.text():
        lines.append(fc.text())
    lines += reset_lines(r)
    lines += [f"⚠ Response changed: {w}" for w in r.warnings or []]
    kl = keepalive_line(st.keepalive)
    if kl:
        lines.append(kl[0])
    if r.detail:
        lines.append("Weekly usage by product: " + ", ".join(f"{k} {v:.0f}%" for k, v in r.detail.items()))
    if r.status == STATUS_OK:
        lines.append(f"Updated {ago(r.last_updated)}")
    elif r.status == STATUS_STALE:
        action = "log in" if r.error and "to log in" in r.error else "refresh"
        lines.append(f"Open {st.cli_name} to {action} · as of {fmt_local(r.last_updated, '%a %d %b %H:%M')}")
    else:
        lines.append(f"{r.error} · last success {fmt_local(r.last_updated, '%a %d %b %H:%M')}")
    lines.append(f"Source: {SOURCE_LABELS.get(r.source, r.source)}")
    return "\n".join(lines)
