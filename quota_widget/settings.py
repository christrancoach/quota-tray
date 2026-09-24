"""widget.json: position, display mode, collapsed cards, opacity, z-order, theme, motion. Validated on load."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field

from quota_core.config import widget_settings_path

log = logging.getLogger(__name__)
Z_ORDERS = ("top", "normal", "desktop")      # always on top / normal window / pinned behind windows
THEMES = ("system", "dark", "light")
OPACITIES = (100, 85, 70, 50)
MODES = ("compact", "expanded", "cats")


@dataclass
class WidgetSettings:
    x: int | None = None          # logical pixels; None = default spot on the primary display
    y: int | None = None
    mode: str = "compact"
    # Where double-click goes from compact: "expanded" normally, "cats" if compact was reached
    # by double-clicking in cats mode.
    compact_partner: str = "expanded"
    reduce_motion: bool = False
    opacity: int = 100
    z_order: str = "normal"
    theme: str = "system"
    collapsed: list[str] = field(default_factory=list)   # provider keys collapsed in expanded mode

    @property
    def expanded(self) -> bool:
        return self.mode == "expanded"

    @property
    def position(self) -> tuple[int, int] | None:
        return (self.x, self.y) if self.x is not None and self.y is not None else None


def load_settings() -> WidgetSettings:
    s = WidgetSettings()
    try:
        raw = json.loads(widget_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return s
    if not isinstance(raw, dict):
        return s
    if isinstance(raw.get("x"), int) and isinstance(raw.get("y"), int):
        s.x, s.y = raw["x"], raw["y"]
    if raw.get("mode") in MODES:
        s.mode = raw["mode"]
    elif raw.get("expanded") is True:  # widget.json from before cats mode
        s.mode = "expanded"
    if raw.get("compact_partner") in ("expanded", "cats"):
        s.compact_partner = raw["compact_partner"]
    if isinstance(raw.get("reduce_motion"), bool):
        s.reduce_motion = raw["reduce_motion"]
    if raw.get("opacity") in OPACITIES:
        s.opacity = raw["opacity"]
    if raw.get("z_order") in Z_ORDERS:
        s.z_order = raw["z_order"]
    if raw.get("theme") in THEMES:
        s.theme = raw["theme"]
    if isinstance(raw.get("collapsed"), list):
        s.collapsed = [k for k in raw["collapsed"] if isinstance(k, str)]
    return s


def save_settings(s: WidgetSettings) -> None:
    path = widget_settings_path()
    try:
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not save widget.json: %s", exc)
