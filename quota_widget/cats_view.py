"""Cats display: one animated cat per provider with its name and weekly % left underneath.

Not wired into the widget menu yet (awaiting approval of the art).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from quota_core.display import SHORT_NAMES, color_for_left
from quota_core.engine import ProviderState
from quota_core.models import STATUS_OK
from quota_ui import cats
from quota_ui.cards import Theme, full_details, label

CAT_PX = 80
CAT_WORDS = {"awake": "wide awake", "alert": "alert", "drowsy": "drowsy", "nodding": "nodding off",
             "asleep": "asleep", "deep": "in deep sleep", "stale": "peeking from a box (data out of date)",
             "error": "tangled in yarn (last check failed)", "reset": "stretching (limit reset since last read)",
             "dead": "gone, with a note to log in"}
FPS = 20  # typing paws and pounces need a smoother frame rate


class CatCanvas(QWidget):
    def __init__(self, key: str, state: str, seed: int, reset: str | None = None):
        super().__init__()
        self.key, self.state, self.seed, self.reset = key, state, seed, reset
        self.anim = cats.Anim()
        self._renderer = QSvgRenderer()
        self._last_svg = ""
        self.setFixedSize(QSize(CAT_PX, CAT_PX))

    def set_anim(self, anim: cats.Anim):
        self.anim = anim
        self.update()

    def paintEvent(self, _):
        svg = cats.cat_svg(self.key, self.state, self.anim, self.reset)
        if svg != self._last_svg:          # most frames only change breathing slightly; skip identical ones
            self._renderer.load(QByteArray(svg.encode()))
            self._last_svg = svg
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._renderer.render(p, QRectF(0, 0, CAT_PX, CAT_PX))


class CatsView(QWidget):
    """Grid of cats: up to 4 across, or 2 per row when `columns=2` (narrow screens)."""

    def __init__(self, providers: list[ProviderState], theme: Theme, *, columns: int = 4, reduce_motion: bool = False):
        super().__init__()
        self.canvases: list[CatCanvas] = []
        self.reduce_motion = reduce_motion
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(8)
        for i, st in enumerate(providers):
            cell = QWidget()
            cell.setToolTip(full_details(st))
            cell.setAccessibleName(f"{st.name}: cat is {CAT_WORDS.get(cats.state_for(st), cats.state_for(st))}")
            cell.setAccessibleDescription(full_details(st))
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            canvas = CatCanvas(st.key, cats.state_for(st), seed=i, reset=cats.reset_for(st))
            self.canvases.append(canvas)
            v.addWidget(canvas, 0, Qt.AlignmentFlag.AlignHCenter)
            name = label(SHORT_NAMES.get(st.name, st.name))
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(name)
            pct = label(_pct_text(st), muted=True)
            pct.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            r = st.reading
            if r and r.status == STATUS_OK and r.weekly_left_pct is not None:
                pct.setStyleSheet(f"color: {color_for_left(r.weekly_left_pct)};")
            v.addWidget(pct)
            if r is not None and r.resets_left:
                n = r.resets_left
                rl = label(f"↻ {n} reset" + ("s" if n != 1 else "") + (" ready" if r.reset_usable_now else ""),
                           name="resetline")
                rl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                v.addWidget(rl)
            grid.addWidget(cell, i // columns, i % columns)
        self._t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        if not reduce_motion:
            self.timer.start(1000 // FPS)

    def set_reduce_motion(self, on: bool):
        self.reduce_motion = on
        if on:
            self.timer.stop()
            for c in self.canvases:
                c.set_anim(cats.Anim())
        else:
            self.timer.start(1000 // FPS)

    def _tick(self):
        t = time.monotonic() - self._t0
        for c in self.canvases:
            c.set_anim(cats.anim_at(t, c.seed))


def _pct_text(st: ProviderState) -> str:
    r = st.reading
    state = cats.state_for(st)
    if state == cats.DEAD:
        return "log in"
    if r is None or r.weekly_left_pct is None:
        return "–"
    if state == cats.RESET:
        return "reset"
    return f"{r.weekly_left_pct:.0f}% left"
