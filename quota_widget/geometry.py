"""Widget placement rules. Pure functions over plain tuples so they can be tested without Qt.

Rects are (x, y, width, height) in Qt logical (device-independent) pixels, which are consistent
across monitors with different DPI scaling.
"""
from __future__ import annotations

Rect = tuple[int, int, int, int]
MIN_VISIBLE = 48   # this much of the widget must be on some screen, both ways, to count as visible
MARGIN = 24
DEFAULT_TOP = 96   # below title bars, so a maximized window's caption buttons stay clear


def _overlap(a: Rect, b: Rect) -> tuple[int, int]:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return (max(0, min(ax + aw, bx + bw) - max(ax, bx)), max(0, min(ay + ah, by + bh) - max(ay, by)))


def is_reachable(widget: Rect, screens: list[Rect]) -> bool:
    """True if at least MIN_VISIBLE x MIN_VISIBLE of the widget lies on one screen's work area."""
    need_w, need_h = min(MIN_VISIBLE, widget[2]), min(MIN_VISIBLE, widget[3])
    return any(w >= need_w and h >= need_h for w, h in (_overlap(widget, s) for s in screens))


def default_position(size: tuple[int, int], primary: Rect) -> tuple[int, int]:
    """Top-right of the primary display's work area, below where title bars and caption buttons sit."""
    px, py, pw, _ = primary
    return (px + pw - size[0] - MARGIN, py + DEFAULT_TOP)


def recover_position(saved: tuple[int, int] | None, size: tuple[int, int],
                     screens: list[Rect], primary: Rect) -> tuple[int, int]:
    """Use the saved position if the widget would be reachable there, otherwise snap to primary.

    Covers a monitor that was unplugged, a resolution change, or a garbage widget.json.
    """
    if saved is not None:
        x, y = saved
        if is_reachable((x, y, size[0], size[1]), screens):
            return (x, y)
    return default_position(size, primary)
