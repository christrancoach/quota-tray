"""Original cat illustrations for the widget's cats mode, generated as inline SVG.

One cat per provider, each with its own coat, silhouette detail and accessory, drawn in a
120x120 viewBox. The pose follows an energy level derived from the weekly % left, and special
states (stale, error, reset, dead login) override it. Every pose keeps the cat's personality.

Deliberately neutral: no provider logos, marks or brand colors. Plain shapes only (no clip
paths), so Qt's SVG renderer draws it reliably.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

OUTLINE_W = 1.5

# Energy levels (from weekly % left) and special states.
AWAKE, ALERT, DROWSY, NODDING, ASLEEP, DEEP = "awake", "alert", "drowsy", "nodding", "asleep", "deep"
STALE, ERROR, RESET, DEAD = "stale", "error", "reset", "dead"
ENERGY_STATES = (AWAKE, ALERT, DROWSY, NODDING, ASLEEP)


def energy_state(left_pct: float) -> str:
    """75-100 awake, 50-75 alert, 25-50 drowsy, 10-25 nodding off, 0-10 asleep, exactly 0 deep sleep."""
    if left_pct <= 0:
        return DEEP
    if left_pct < 10:
        return ASLEEP
    if left_pct < 25:
        return NODDING
    if left_pct < 50:
        return DROWSY
    if left_pct < 75:
        return ALERT
    return AWAKE


@dataclass(frozen=True)
class CatStyle:
    key: str
    base: str            # main coat (left half for the split cat)
    other: str           # right half for the split cat; otherwise same as base
    stripe: str          # tabby / ginger stripes
    light: str           # chest, muzzle, paws
    outline: str
    nose: str
    iris_l: str
    iris_r: str
    pattern: str         # tabby | tuxedo | ginger | split
    accessory: str       # glasses | headphones | tuft | collar


CATS = {
    # Calm, bookish gray-brown tabby with round glasses.
    "claude": CatStyle("claude", base="#A39580", other="#A39580", stripe="#6B604F", light="#E3DACB",
                       outline="#4A4238", nose="#B98A82", iris_l="#B7A444", iris_r="#B7A444",
                       pattern="tabby", accessory="glasses"),
    # Focused tuxedo with rust headphones.
    "codex": CatStyle("codex", base="#2E2D31", other="#2E2D31", stripe="#2E2D31", light="#F1EDE6",
                      outline="#1A191C", nose="#D69C9C", iris_l="#D8C15A", iris_r="#D8C15A",
                      pattern="tuxedo", accessory="headphones"),
    # Chaotic ginger with one bent ear and a messy tuft.
    "grok": CatStyle("grok", base="#DC8D45", other="#DC8D45", stripe="#B5682B", light="#F6D9B6",
                     outline="#7A4418", nose="#C9736A", iris_l="#86A85C", iris_r="#86A85C",
                     pattern="ginger", accessory="tuft"),
    # Curious two-tone split face (cream / charcoal), odd eyes, mustard collar, watches a moth.
    # Not assigned to a provider yet: kept for a future one (map its key to this style).
    "curious": CatStyle("curious", base="#EDE5D8", other="#3D3A37", stripe="#3D3A37", light="#F7F2EA",
                        outline="#2A2725", nose="#C99A92", iris_l="#D9A441", iris_r="#8DB06B",
                        pattern="split", accessory="collar"),
}
FALLBACK = CATS["claude"]


@dataclass(frozen=True)
class Anim:
    """One animation frame: time in seconds and a per-cat seed. still=True draws the resting pose."""
    t: float = 0.0
    seed: int = 0
    still: bool = True


def anim_at(t: float, seed: int = 0) -> Anim:
    return Anim(t=t, seed=seed, still=False)


class Motion:
    """Time-based motion helpers. Everything returns 0 / None for a still frame (Reduce motion)."""

    def __init__(self, a: Anim):
        self.on = not a.still
        self.t = a.t + a.seed * 1.37   # stagger the cats so they never move in unison

    def wave(self, period: float, phase: float = 0.0) -> float:
        return math.sin(2 * math.pi * (self.t / period + phase)) if self.on else 0.0

    def pulse(self, period: float, dur: float, offset: float = 0.0):
        """Progress 0..1 while inside a `dur`-second event that repeats every `period` s, else None."""
        if not self.on:
            return None
        x = (self.t + offset) % period
        return x / dur if x < dur else None

    def bump(self, period: float, dur: float, offset: float = 0.0) -> float:
        """0 -> 1 -> 0 hump during the event."""
        p = self.pulse(period, dur, offset)
        return math.sin(math.pi * p) if p is not None else 0.0

    def blink(self, period: float = 3.8, dur: float = 0.16) -> bool:
        return self.pulse(period, dur, 0.9) is not None

    def nod(self, period: float = 4.5) -> float:
        """Nodding off: head sinks slowly (0 -> 1) then jerks back up."""
        if not self.on:
            return 0.0
        x = (self.t % period) / period
        return x / 0.85 if x < 0.85 else (1 - x) / 0.15


# ---------------------------------------------------------------- primitives
def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _ellipse(cx, cy, rx, ry, fill, stroke=None, sw=OUTLINE_W, extra=""):
    s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return f'<ellipse cx="{_f(cx)}" cy="{_f(cy)}" rx="{_f(rx)}" ry="{_f(ry)}" fill="{fill}"{s}{extra}/>'


def _path(d, fill="none", stroke=None, sw=OUTLINE_W, extra=""):
    s = f' stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"' if stroke else ""
    return f'<path d="{d}" fill="{fill}"{s}{extra}/>'


def _split_ellipse(cx, cy, rx, ry, left, right, outline):
    """Ellipse whose right half is a different color (the split cat), built without clip paths."""
    return (_ellipse(cx, cy, rx, ry, left) +
            _path(f"M{_f(cx)},{_f(cy - ry)} A{_f(rx)},{_f(ry)} 0 0 1 {_f(cx)},{_f(cy + ry)} Z", fill=right) +
            _ellipse(cx, cy, rx, ry, "none", outline))


def _coat_ellipse(cs: CatStyle, cx, cy, rx, ry):
    if cs.pattern == "split":
        return _split_ellipse(cx, cy, rx, ry, cs.base, cs.other, cs.outline)
    return _ellipse(cx, cy, rx, ry, cs.base, cs.outline)


def _g(content: str, transform: str = "") -> str:
    return f'<g transform="{transform}">{content}</g>' if transform else f"<g>{content}</g>"


# ---------------------------------------------------------------- head
def _eye(x, y, kind, iris, face, outline, look=(0.0, 0.0)):
    lx, ly = look
    if kind == "closed":
        return _path(f"M{_f(x-3.6)},{_f(y)} Q{_f(x)},{_f(y+2.8)} {_f(x+3.6)},{_f(y)}", stroke=outline, sw=1.3)
    if kind == "sliver":
        return (_ellipse(x, y + 1.2, 3.2, 0.9, iris) +
                _path(f"M{_f(x-3.8)},{_f(y+0.5)} Q{_f(x)},{_f(y+1.7)} {_f(x+3.8)},{_f(y+0.5)}", stroke=outline, sw=1.2))
    rx, ry, prx, pry = {"wide": (4.0, 4.5, 2.4, 3.3), "focus": (3.5, 3.4, 0.9, 2.7)}.get(kind, (3.5, 4.1, 1.3, 3.0))
    out = (_ellipse(x, y, rx, ry, iris, outline, sw=0.9) +
           _ellipse(x + lx, y + ly, prx, pry, "#161412") +
           _ellipse(x + lx - 1.0, y + ly - 1.4, 0.9, 0.9, "#FFFFFF", extra=' opacity="0.9"'))
    if kind == "half":
        out += (_path(f"M{_f(x-4.6)},{_f(y-5)} L{_f(x+4.6)},{_f(y-5)} L{_f(x+4.6)},{_f(y)} "
                      f"Q{_f(x)},{_f(y+1)} {_f(x-4.6)},{_f(y)} Z", fill=face) +
                _path(f"M{_f(x-4)},{_f(y)} Q{_f(x)},{_f(y+1)} {_f(x+4)},{_f(y)}", stroke=outline, sw=1.2))
    return out


def _ears(cs: CatStyle, droop: float = 0.0, twitch: float = 0.0) -> str:
    """Ears in head-local coords. droop (0..1) flattens them; twitch (0..1) flicks the left ear."""
    d = droop * 5
    lcol, rcol = cs.base, cs.other
    left = _path(f"M-18,-5 L{_f(-15-d)},{_f(-27+d)} L-3,-15 Z", fill=lcol, stroke=cs.outline)
    left_in = _path(f"M-15,-9 L{_f(-13.8-d*0.8)},{_f(-22+d)} L-6.5,-14.5 Z", fill=cs.nose, extra=' opacity="0.55"')
    if twitch:
        left = _g(left + left_in, f"rotate({_f(-16 * twitch)},-10,-10)")
        left_in = ""
    if cs.key == "grok":  # right ear bent over at the tip
        right = (_path("M18,-5 L15.5,-19 L3,-15 Z", fill=rcol, stroke=cs.outline) +
                 _path("M15.5,-19 L24,-17 L18.5,-11.5 Z", fill=cs.stripe, stroke=cs.outline))
        right_in = _path("M14.5,-9 L14,-16.5 L7,-14.5 Z", fill=cs.nose, extra=' opacity="0.55"')
    else:
        right = _path(f"M18,-5 L{_f(15+d)},{_f(-27+d)} L3,-15 Z", fill=rcol, stroke=cs.outline)
        right_in = _path(f"M15,-9 L{_f(13.8+d*0.8)},{_f(-22+d)} L6.5,-14.5 Z", fill=cs.nose, extra=' opacity="0.55"')
    return left + right + left_in + right_in


def _face_markings(cs: CatStyle) -> str:
    if cs.pattern in ("tabby", "ginger"):
        m = (_path("M-7,-12 L-3.5,-7.5 L0,-12 L3.5,-7.5 L7,-12", stroke=cs.stripe, sw=1.7) +
             "".join(_path(f"M{s*19.5},{y} L{s*13.5},{y+0.6}", stroke=cs.stripe, sw=1.6) for s in (-1, 1) for y in (1, 5)))
        muzzle = _ellipse(0, 7, 8.5, 5.8, cs.light)
        return m + muzzle
    if cs.pattern == "tuxedo":
        return (_path("M-2.6,-16 Q0,-6 -5,3 L5,3 Q0,-6 2.6,-16 Z", fill=cs.light) +
                _ellipse(0, 7.5, 10, 7, cs.light))
    # split: muzzle split too
    return (_ellipse(0, 7, 8.5, 5.8, "#F7F2EA") +
            _path("M0,1.2 A8.5,5.8 0 0 1 0,12.8 Z", fill="#6A6560"))


def _whiskers(cs: CatStyle) -> str:
    wild = cs.key == "grok"
    col = "#FFFFFF" if cs.pattern == "tuxedo" else cs.outline
    out = ""
    for s in (-1, 1):
        for dy, spread in ((0, -2.5), (1.6, 1.0), (3.2, 4.0 if wild else 3.0)):
            out += _path(f"M{s*6.5},{6+dy} L{s*(19 if not wild else 20)},{_f(4+dy+spread*(1.4 if wild else 1))}",
                         stroke=col, sw=0.6, extra=' opacity="0.55"')
    return out


def _glasses(cs: CatStyle, dy: float = 0.0, rot: float = 0.0) -> str:
    frame = "#3A2F27"
    g = (_ellipse(-7.5, -1 + dy, 5.9, 5.6, "#FFFFFF", frame, sw=1.4, extra=' fill-opacity="0.16"') +
         _ellipse(7.5, -1 + dy, 5.9, 5.6, "#FFFFFF", frame, sw=1.4, extra=' fill-opacity="0.16"') +
         _path(f"M-1.7,{_f(-2+dy)} Q0,{_f(-3.4+dy)} 1.7,{_f(-2+dy)}", stroke=frame, sw=1.3) +
         _path(f"M-13.4,{_f(-2+dy)} L-19,{_f(-4.5+dy)} M13.4,{_f(-2+dy)} L19,{_f(-4.5+dy)}", stroke=frame, sw=1.2))
    return _g(g, f"rotate({_f(rot)})") if rot else g


def _headphones(on_head: bool = True) -> str:
    band, cup = "#4B4A52", "#B5553C"
    if on_head:
        return (_path("M-21,-3 C-22,-33 22,-33 21,-3", stroke=band, sw=3.4) +
                f'<rect x="-26" y="-9" width="8" height="14" rx="3.5" fill="{cup}" stroke="#3A1E16" stroke-width="1.2"/>'
                f'<rect x="18" y="-9" width="8" height="14" rx="3.5" fill="{cup}" stroke="#3A1E16" stroke-width="1.2"/>')
    # resting around the neck
    return (_path("M-15,12 Q0,24 15,12", stroke=band, sw=3.2) +
            f'<rect x="-21" y="7" width="8" height="11" rx="3.5" fill="{cup}" stroke="#3A1E16" stroke-width="1.2"/>'
            f'<rect x="13" y="7" width="8" height="11" rx="3.5" fill="{cup}" stroke="#3A1E16" stroke-width="1.2"/>')


def head(cs: CatStyle, x: float, y: float, *, rot: float = 0.0, eyes: str = "open", look=(0.0, 0.0),
         droop: float = 0.0, yawn: bool = False, glasses_dy: float = 0.0, glasses_rot: float = 0.0,
         headphones: str = "on", scale: float = 1.0, blink: bool = False, twitch: float = 0.0,
         glint=None) -> str:
    if blink and eyes in ("open", "wide", "focus", "half"):
        eyes = "closed"
    parts = [_ears(cs, droop, twitch)]
    if cs.accessory == "tuft":
        parts.append(_path("M-5,-15 L-3,-22 L-0.5,-15.5 L2,-23.5 L4.5,-15 Z", fill=cs.base, stroke=cs.outline, sw=1.1))
    parts.append(_coat_ellipse(cs, 0, 0, 19.5, 16.5))
    parts.append(_face_markings(cs))
    lface = cs.base
    rface = cs.other
    parts.append(_eye(-7.5, -1, eyes, cs.iris_l, lface, cs.outline, look))
    parts.append(_eye(7.5, -1, eyes, cs.iris_r, rface, cs.outline, look))
    parts.append(_path("M-2.2,3.6 L2.2,3.6 L0,6.2 Z", fill=cs.nose))
    if yawn:
        parts.append(_ellipse(0, 9.6, 3.2, 3.6, "#7A3F3F", cs.outline, sw=1.0))
    else:
        parts.append(_path("M0,6.2 Q-2,8.8 -4.2,7.6 M0,6.2 Q2,8.8 4.2,7.6", stroke=cs.outline, sw=1.0))
    parts.append(_whiskers(cs))
    if cs.accessory == "glasses" and glasses_dy is not None:
        parts.append(_glasses(cs, glasses_dy, glasses_rot))
        if glint is not None:  # a light glint sliding across the left lens
            gx = -11 + 7 * glint
            parts.append(_path(f"M{_f(gx)},{_f(-4 + glasses_dy)} l2.6,-2.6", stroke="#FFFFFF", sw=1.3,
                               extra=' opacity="0.9"'))
    if cs.accessory == "headphones" and headphones:
        parts.append(_headphones(headphones == "on"))
    t = f"translate({_f(x)},{_f(y)})" + (f" rotate({_f(rot)})" if rot else "") + (f" scale({_f(scale)})" if scale != 1 else "")
    return _g("".join(parts), t)


# ---------------------------------------------------------------- bodies
def _breath(cx: float, base_y: float, b: float, amount: float = 0.018) -> str:
    s = 1 + amount * b
    return f"translate({_f(cx)},{_f(base_y)}) scale(1,{s:.4f}) translate({_f(-cx)},{_f(-base_y)})"


def _side_stripes(cs, cx, cy, rx, ry):
    if cs.pattern not in ("tabby", "ginger"):
        return ""
    out = ""
    for s in (-1, 1):
        for f in (-0.35, 0.05, 0.45):
            y = cy + f * ry
            x = cx + s * (rx - 1.5)
            out += _path(f"M{_f(x)},{_f(y)} q{_f(-s*6)},{_f(1.5)} {_f(-s*8)},{_f(5)}", stroke=cs.stripe, sw=2.0)
    return out


def _paws(cs, xs, y, lift=(0.0, 0.0)):
    fill = cs.light if cs.pattern == "tuxedo" else (cs.base if cs.pattern != "split" else None)
    out = ""
    for i, x in enumerate(xs):
        f = fill or (cs.base if x < 60 else cs.other)
        out += _ellipse(x, y - (lift[i] if i < len(lift) else 0), 5.6, 3.3, f, cs.outline, sw=1.2)
    return out


def _tail(cs, d, pivot, angle, width=6.5):
    col = cs.other if cs.pattern == "split" else cs.base
    t = (_path(d, stroke=cs.outline, sw=width + 2.4) + _path(d, stroke=col, sw=width))
    if cs.pattern in ("tabby", "ginger"):
        t += _path(d, stroke=cs.stripe, sw=width, extra=' stroke-dasharray="2.2,5" opacity="0.8"')
    if cs.pattern == "tuxedo":
        pass
    px, py = pivot
    return _g(t, f"rotate({_f(angle)},{_f(px)},{_f(py)})") if angle else _g(t)


def sitting(cs, cx, base_y, w, h, breath=0.0, paw_lift=(0.0, 0.0)):
    cy = base_y - h / 2
    body = _coat_ellipse(cs, cx, cy, w, h / 2)
    if cs.pattern == "tuxedo":
        body += _ellipse(cx, base_y - h * 0.5, w * 0.52, h * 0.36, cs.light)
    elif cs.pattern in ("tabby", "ginger"):
        body += _ellipse(cx, base_y - h * 0.52, w * 0.48, h * 0.33, cs.light, extra=' opacity="0.85"')
    body += _side_stripes(cs, cx, cy, w, h / 2)
    return _g(body, _breath(cx, base_y, breath, 0.035)) + _paws(cs, (cx - 7.5, cx + 7.5), base_y - 1.5, paw_lift)


def lying(cs, cx, cy, rx, ry, breath=0.0, rot=0.0):
    body = _coat_ellipse(cs, cx, cy, rx, ry)
    if cs.pattern == "tuxedo":
        body += _ellipse(cx - rx * 0.45, cy + ry * 0.35, rx * 0.35, ry * 0.45, cs.light)
    body += _side_stripes(cs, cx, cy, rx * 0.6, ry)
    tr = _breath(cx, cy + ry, breath, 0.05)
    if rot:
        tr += f" rotate({_f(rot)},{_f(cx)},{_f(cy)})"
    return _g(body, tr)


def shadow(cx, y, rx):
    return _ellipse(cx, y, rx, 3.2, "#000000", extra=' opacity="0.12"')


def zzz(big: bool, m=None) -> str:
    """Zzz drifting up and fading. Still frame: a fixed little stack."""
    col = "#8C8F96"
    sizes = (17, 13, 10) if big else (11, 9, 7)
    x0, y0 = (84, 48) if big else (88, 58)
    out = ""
    for i, size in enumerate(sizes):
        if m is not None and m.on:
            p = ((m.t / 3.0) + i / 3) % 1.0            # each z rises over 3 s, staggered
            x, y, op = x0 + p * 22, y0 - p * 38, max(0.0, 1 - p) ** 0.8
            size = size * (0.7 + 0.5 * p)
        else:
            x, y, op = x0 + i * 11, y0 - i * 13, 1.0
        letter = "Z" if big and i < 2 else "z"
        out += (f'<text x="{_f(x)}" y="{_f(y)}" font-family="Segoe UI, Arial" font-weight="700" '
                f'font-size="{_f(size)}" fill="{col}" opacity="{op:.2f}">{letter}</text>')
    return out


def music_notes(m, x: float, y: float) -> str:
    """Two notes floating up from a headphone cup."""
    col = "#7C8796"
    out = ""
    for i in range(2):
        p = ((m.t / 2.2) + i / 2) % 1.0
        nx, ny, op = x + 10 * p + 4 * math.sin(p * 6), y - 26 * p, (1 - p) * 0.95
        note = (_ellipse(0, 0, 2.3, 1.7, col, extra=' transform="rotate(-20)"') +
                _path("M2,-0.5 L2,-8 L5.5,-6.5", stroke=col, sw=1.1))
        out += f'<g transform="translate({_f(nx)},{_f(ny)})" opacity="{op:.2f}">{note}</g>'
    return out


def moth(x: float, y: float, flap: float) -> str:
    """A small neutral moth for the curious cat to watch. flap 0..1 folds the wings."""
    w = 1.0 - 0.75 * flap
    wing, dk = "#D8CFC2", "#8E8474"
    wings = (_ellipse(-3.2, -1.5, 3.4, 2.6, wing, dk, sw=0.7) + _ellipse(3.2, -1.5, 3.4, 2.6, wing, dk, sw=0.7) +
             _ellipse(-2.4, 1.8, 2.2, 1.8, wing, dk, sw=0.6) + _ellipse(2.4, 1.8, 2.2, 1.8, wing, dk, sw=0.6))
    return _g(_g(wings, f"scale({_f(w)},1)") + _ellipse(0, 0, 0.9, 2.8, "#6F665A"), f"translate({_f(x)},{_f(y)}) scale(1.5)")


def reset_token(m, ready: bool) -> str:
    """A small golden token with a circular arrow: a banked limit reset. Glows and bobs when usable now."""
    bob = 1.6 * m.wave(1.8) if (m.on and ready) else 0.0
    x, y = 16, 17 + bob
    parts = []
    if ready:
        glow = 0.28 + 0.18 * (m.wave(1.4) if m.on else 0.0)
        parts.append(_ellipse(x, y, 13, 13, "#F6D774", extra=f' opacity="{glow:.2f}"'))
    coin, rim, mark = ("#E8B84A", "#9A7420", "#7A5810") if ready else ("#D9CFB8", "#9C9380", "#8A8170")
    parts.append(_ellipse(x, y, 9, 9, coin, rim, sw=1.4))
    parts.append(_path(f"M{x+4.2},{y-2.2} A4.8,4.8 0 1 0 {x+4.6},{y+1.8}", stroke=mark, sw=1.6))
    parts.append(_path(f"M{x+4.6},{y-5.2} L{x+4.4},{y-1.8} L{x+1.2},{y-2.4}", stroke=mark, sw=1.4))
    if ready and m.on:  # a little sparkle of light traveling round the rim
        a = (m.t * 2.2) % (2 * math.pi)
        parts.append(_ellipse(x + 9 * math.cos(a), y + 9 * math.sin(a), 1.4, 1.4, "#FFF7D6", extra=' opacity="0.9"'))
    return _g("".join(parts))


def page_flip(x: float, y: float, p: float) -> str:
    """A page turning over the open book (p 0..1), right to left."""
    sx = math.cos(math.pi * p)
    page = _path("M0,-7.5 L12.5,-10.2 L12.5,-1 L0,1.6 Z", fill="#FBF7EC", stroke="#B9AE98", sw=0.7)
    return _g(page, f"translate({_f(x)},{_f(y)}) scale({sx:.3f},1)")


# ---------------------------------------------------------------- props
def book(x, y, open_=True, scale=1.0):
    cover, page, line = "#7A3B3B", "#F4EEDF", "#B9AE98"
    if open_:
        g = (_path("M-14,0 L0,3 L14,0 L14,-10 L0,-7 L-14,-10 Z", fill=cover, stroke="#4A2222", sw=1.1) +
             _path("M-12.5,-1 L0,1.8 L0,-8 L-12.5,-10.6 Z", fill=page) +
             _path("M12.5,-1 L0,1.8 L0,-8 L12.5,-10.6 Z", fill=page) +
             _path("M-10,-7 L-3,-5.6 M-10,-4.5 L-3,-3.1 M3,-5.6 L10,-7 M3,-3.1 L10,-4.5", stroke=line, sw=0.8))
    else:
        g = (f'<rect x="-13" y="-6" width="26" height="6" rx="1" fill="{cover}" stroke="#4A2222" stroke-width="1.1"/>'
             f'<rect x="-11.5" y="-5" width="23" height="2.4" fill="{page}"/>')
    return _g(g, f"translate({_f(x)},{_f(y)}) scale({_f(scale)})")


def laptop(x, y, open_=True):
    body, screen = "#9AA0A6", "#50565E"
    if open_:  # seen from behind the lid, angled toward the cat
        g = (_path("M-12,0 L12,0 L10,-19 L-10,-19 Z", fill=body, stroke="#50555B", sw=1.1) +
             _ellipse(0, -9.5, 2.2, 2.2, "#B8BDC2") +
             f'<rect x="-15" y="0" width="30" height="3" rx="1.2" fill="{screen}"/>')
    else:
        g = (f'<rect x="-14" y="-4" width="28" height="4" rx="1.5" fill="{body}" stroke="#50555B" stroke-width="1.1"/>')
    return _g(g, f"translate({_f(x)},{_f(y)})")


def yarn_ball(x, y):
    col, dk = "#C98A9B", "#9A5C6D"
    return (_ellipse(x, y, 8.5, 8.5, col, dk, sw=1.2) +
            _path(f"M{x-7},{y-3} Q{x},{y-9} {x+7},{y-2} M{x-8},{y+2} Q{x},{y-4} {x+8},{y+3} "
                  f"M{x-4},{y+7} Q{x+2},{y} {x+3},{y-8}", stroke=dk, sw=1.0))


def yarn_tangle(cx, top, bottom):
    col = "#C98A9B"
    return (_path(f"M{cx-24},{top+6} C{cx-6},{top+16} {cx+10},{top-2} {cx+24},{top+10}", stroke=col, sw=2.0) +
            _path(f"M{cx-22},{bottom-10} C{cx-4},{bottom-26} {cx+8},{bottom} {cx+26},{bottom-16}", stroke=col, sw=2.0) +
            _path(f"M{cx-18},{top+22} C{cx},{top+30} {cx+4},{top+6} {cx+20},{top+30}", stroke=col, sw=2.0) +
            _path(f"M{cx+24},{top+10} C{cx+34},{top+18} {cx+30},{bottom-4} {cx+38},{bottom-2}", stroke=col, sw=2.0))


def box_back():
    return _path("M24,82 L36,70 L84,70 L96,82 Z", fill="#A77F54", stroke="#6E5233", sw=1.2)


def box_front():
    return (f'<rect x="24" y="82" width="72" height="26" fill="#C99E6E" stroke="#6E5233" stroke-width="1.3"/>'
            + _path("M24,82 L14,74 L32,74 L40,82 Z", fill="#D7AE7F", stroke="#6E5233", sw=1.2)
            + _path("M96,82 L106,74 L88,74 L80,82 Z", fill="#D7AE7F", stroke="#6E5233", sw=1.2)
            + _path("M52,92 L68,92", stroke="#8F6E47", sw=1.0))


def cushion():
    return (_ellipse(60, 100, 36, 9, "#8D93A6", "#5D6273", sw=1.3) +
            _ellipse(60, 98, 24, 5, "#7D8396") +
            _path("M28,99 Q24,96 26,93 M92,99 Q96,96 94,93", stroke="#5D6273", sw=1.1))


def login_note():
    return _g(f'<rect x="-15" y="-11" width="30" height="21" rx="1.5" fill="#FFFDF5" stroke="#9A9486" stroke-width="1"/>'
              + _path("M-15,-11 L-11,-15 L-7,-11", fill="#E9E4D6", stroke="#9A9486", sw=0.9)
              + '<text x="0" y="3" text-anchor="middle" font-family="Segoe UI, Arial" font-size="8.5" '
                'font-weight="600" fill="#3A3A3A">log in</text>', "translate(84,70) rotate(-8)")


# ---------------------------------------------------------------- scenes
def _svg(body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120">{body}</svg>'


def _move(content: str, dx: float = 0.0, dy: float = 0.0, rot: float = 0.0, pivot=(60, 108)) -> str:
    tr = []
    if dx or dy:
        tr.append(f"translate({_f(dx)},{_f(dy)})")
    if rot:
        tr.append(f"rotate({_f(rot)},{_f(pivot[0])},{_f(pivot[1])})")
    return _g(content, " ".join(tr))


def cat_svg(key: str, state: str, anim: Anim = Anim(), reset: str | None = None) -> str:
    """Full SVG document for provider `key` in `state`. reset: None, "banked" or "ready" (usable now)."""
    svg = _cat_svg(key, state, anim)
    if reset and state != DEAD:
        svg = svg.replace("</svg>", reset_token(Motion(anim), reset == "ready") + "</svg>")
    return svg


def _cat_svg(key: str, state: str, anim: Anim) -> str:
    cs = CATS.get(key, FALLBACK)
    m = Motion(anim)
    twitch = m.bump(4.7, 0.35, 1.3)
    parts: list[str] = []

    if state == DEAD:  # empty cushion; the note flutters a little
        note = _g(login_note(), f"rotate({_f(3 * m.wave(2.2))},84,70)")
        parts += [cushion(), note]
        if cs.accessory == "glasses":
            parts.append(_g(_glasses(cs), "translate(52,95) scale(0.7) rotate(-6)"))
        return _svg("".join(parts))

    if state == STALE:  # peekaboo from the box: ducks down and pops back up, glancing around
        duck = m.bump(5.0, 1.1, 3.4)
        look = (1.3 * m.wave(2.6), 0.3)
        parts += [box_back(), head(cs, 60, 77.5 + 11 * duck, eyes="open", look=look, blink=m.blink(), twitch=twitch),
                  box_front()]
        return _svg("".join(parts))

    if state in (ASLEEP, DEEP):
        return _svg(_curled(cs, m, deep=state == DEEP))

    if state == RESET:  # stretching and yawning
        s = m.wave(2.8)
        body = [_tail(cs, "M88,72 C96,58 104,58 106,46", (88, 72), 8 * m.wave(1.9)),
                _path("M78,82 L86,106 M70,84 L74,106", stroke=cs.outline, sw=7.6),
                _path("M78,82 L86,106 M70,84 L74,106", stroke=cs.base if cs.pattern != "split" else cs.other, sw=5.4),
                lying(cs, 64, 84, 26, 12, m.wave(3.2), rot=-22 - 4 * s),
                _path("M44,94 L22,106 M50,96 L32,107", stroke=cs.outline, sw=7.6),
                _path("M44,94 L22,106 M50,96 L32,107", stroke=cs.light if cs.pattern == "tuxedo" else cs.base, sw=5.4),
                head(cs, 38, 88 + 1.5 * s, rot=8 + 3 * s, eyes="half", yawn=s > -0.2 or not m.on, droop=0.2,
                     glasses_dy=2, twitch=twitch)]
        return _svg(shadow(62, 108, 40) + "".join(body))

    if state == ERROR:  # struggling in the yarn, ball rolling away
        wr = 5 * m.wave(0.35) * m.bump(2.6, 1.2)
        cat = (_tail(cs, "M76,104 C92,104 96,90 88,82", (76, 104), 14 * m.wave(0.5)) +
               sitting(cs, 58, 108, 22, 38, m.wave(3.0)) +
               head(cs, 58, 52, rot=-6 + wr * 1.5, eyes="wide", look=(1.2 * m.wave(1.3), 0.5), blink=m.blink(), twitch=twitch) +
               yarn_tangle(58, 56, 106))
        ball = _g(yarn_ball(0, 0), f"translate({_f(98 + 2 * m.wave(3))},100) rotate({_f((m.t * 90) % 360 if m.on else 0)})")
        return _svg(shadow(58, 108, 30) + _move(cat, rot=wr, pivot=(58, 108)) + ball)

    pose = {"claude": _claude, "codex": _codex, "grok": _grok, "curious": _curious}.get(cs.key, _claude)
    return _svg(pose(cs, state, m, twitch))


def _slump(state):
    """(body height, body width, head y, head rot, eyes, ear droop) for the sitting family."""
    return {AWAKE: (40, 22, 50, 0, "open", 0.0), ALERT: (37, 23, 53, 0, "open", 0.0),
            DROWSY: (31, 25, 62, 0, "half", 0.35), NODDING: (29, 26, 69, 20, "sliver", 0.6)}[state]


def _sleepy(m: Motion, state: str):
    """Drowsy: slow dip-and-recover. Nodding: sinks further, then jerks awake."""
    if state == DROWSY:
        d = m.bump(4.2, 3.2)
        return 3.5 * d, 4 * d, m.pulse(4.2, 3.2) is not None and d > 0.8
    n = m.nod(4.5)
    return 4 * n, 10 * n, False


def _claude(cs, state, m, twitch):
    h, w, hy, rot, eyes, droop = _slump(state)
    breath = m.wave(3.4)
    tail = _tail(cs, "M78,104 C94,104 98,88 90,80", (78, 104), 9 * m.wave(2.4) if state in (AWAKE, ALERT) else 3 * m.wave(3.5))
    p = [shadow(60, 108, 30), tail, sitting(cs, 60, 108, w, h, breath)]
    if state == AWAKE:  # reading: eyes scan each line, head bobs, pages turn, glasses glint
        line = ((m.t % 2.4) / 2.4) if m.on else 0.5
        look = (-1.5 + 3.0 * line, 1.3)
        flip = m.pulse(7.0, 0.9, 2.0)
        glint = m.pulse(5.5, 0.5, 4.0)
        p.append(head(cs, 60, hy + 0.6 * m.wave(2.4), rot=2.5 * m.wave(4.8), eyes=eyes, look=look,
                      blink=m.blink(4.4), twitch=twitch, glint=glint))
        p.append(book(60, 106, open_=True))
        if flip is not None:
            p.append(page_flip(60, 106, flip))
    elif state == ALERT:  # relaxed, looking around, book set aside
        p.append(head(cs, 60, hy, rot=-4 + 4 * m.wave(7.0), eyes=eyes, look=(1.6 * m.wave(6.0), 0), blink=m.blink(),
                      twitch=twitch))
        p.append(book(96, 108, open_=False, scale=0.8))
    else:
        dy, drot, blink = _sleepy(m, state)
        p.append(book(60, 108, open_=True, scale=0.9))
        if state == DROWSY:  # glasses slipping further as the head dips
            p.append(head(cs, 60, hy + dy, rot=drot * 0.5, eyes=eyes, droop=droop, glasses_dy=3.5 + dy * 0.4,
                          blink=blink or m.blink(3.0, 0.6), twitch=twitch))
        else:
            p.append(head(cs, 58, hy + dy, rot=rot + drot, eyes=eyes, droop=droop, glasses_dy=4, glasses_rot=9))
    return "".join(p)


def _codex(cs, state, m, twitch):
    p = [shadow(62, 108, 32)]
    if state == AWAKE:  # typing to the beat, head bobbing, music floating out of the headphones
        beat = m.wave(0.8)
        lift = (max(0.0, m.wave(0.33)) * 2.4, max(0.0, m.wave(0.33, 0.5)) * 2.4)
        hx, hy, hr = 56, 66 + 1.2 * abs(beat), -10 + 3.5 * beat
        p += [_tail(cs, "M80,104 C96,104 98,92 92,86", (80, 104), 12 * m.wave(0.8)),
              sitting(cs, 64, 108, 24, 30, m.wave(3.0), paw_lift=lift),
              head(cs, hx, hy, rot=hr, eyes="focus", look=(-1.2, 1.2), blink=m.blink(), twitch=twitch),
              laptop(34, 107)]
        if m.on:
            p.append(music_notes(m, hx + 24, hy - 4))
    elif state == ALERT:  # sitting back, still nodding along
        beat = m.wave(1.2)
        p += [_tail(cs, "M80,104 C96,104 98,88 90,80", (80, 104), 8 * m.wave(1.2)),
              sitting(cs, 64, 108, 22, 37, m.wave(3.2)),
              head(cs, 64, 53, rot=-4 + 2.5 * beat, eyes="open", look=(-0.8, 0.4), blink=m.blink(), twitch=twitch),
              laptop(30, 107)]
        if m.on and m.pulse(6.0, 2.2) is not None:
            p.append(music_notes(m, 88, 50))
    elif state == DROWSY:
        dy, drot, blink = _sleepy(m, state)
        p += [_tail(cs, "M80,104 C96,104 98,92 92,86", (80, 104), 3 * m.wave(3.0)),
              sitting(cs, 64, 108, 25, 30, m.wave(3.6)),
              head(cs, 58, 64 + dy, rot=-6 - drot * 0.5, eyes="half", droop=0.35, blink=blink or m.blink(3.0, 0.6),
                   twitch=twitch),
              laptop(32, 107)]
    else:  # head sinking toward the keyboard, then jerking up
        dy, drot, _ = _sleepy(m, state)
        p += [_tail(cs, "M80,104 C96,104 98,94 94,88", (80, 104), 2 * m.wave(3.6)),
              sitting(cs, 66, 108, 26, 28, m.wave(3.8)),
              laptop(34, 107),
              head(cs, 50 - dy * 0.5, 72 + dy, rot=-18 - drot, eyes="sliver", droop=0.6)]
    return "".join(p)


def _grok(cs, state, m, twitch):
    p = [shadow(62, 108, 38)]
    if state == AWAKE:  # butt-wiggle, then pounce hop, tail lashing
        cyc = (m.t % 4.6) if m.on else 0.0
        wiggle = 2.4 * math.sin(cyc * 2 * math.pi * 5) if cyc < 2.2 else 0.0
        hop = math.sin(math.pi * (cyc - 2.2) / 0.7) if 2.2 <= cyc < 2.9 else 0.0
        slide = (-8 * (cyc - 2.2) / 0.7 if 2.2 <= cyc < 2.9 else (-8 * max(0.0, 1 - (cyc - 2.9) / 1.7) if cyc >= 2.9 else 0.0))
        rear = (_tail(cs, "M88,78 C98,62 92,50 102,40", (88, 78), 20 * m.wave(0.45)) +
                _path("M84,84 L92,106 M76,86 L80,106", stroke=cs.outline, sw=7.8) +
                _path("M84,84 L92,106 M76,86 L80,106", stroke=cs.base, sw=5.6))
        cat = (_move(rear, dx=wiggle) + lying(cs, 68 + wiggle * 0.5, 88, 26, 13, m.wave(2.0), rot=-20) +
               _path("M46,98 L28,106 M52,100 L40,107", stroke=cs.outline, sw=7.8) +
               _path("M46,98 L28,106 M52,100 L40,107", stroke=cs.base, sw=5.6) +
               head(cs, 38, 86, rot=-8, eyes="wide", look=(-1.4, 0.6), blink=m.blink(5.0), twitch=twitch))
        p.append(_move(cat, dx=slide, dy=-10 * hop))
        if not m.on or cyc < 2.2:
            p.append(_path("M104,74 l6,-2 M104,82 l7,0 M102,90 l6,2", stroke=cs.stripe, sw=1.4, extra=' opacity="0.6"'))
    elif state == ALERT:  # crouched and watching, tail twitching in bursts
        tw = 18 * m.wave(0.3) * m.bump(3.2, 1.4)
        p += [_tail(cs, "M90,100 C104,96 106,82 98,76", (90, 100), tw),
              lying(cs, 66, 96, 29, 13, m.wave(3.0)),
              _paws(cs, (40, 50), 107),
              head(cs, 42, 80, rot=-4 + 3 * m.wave(5.0), eyes="open", look=(1.4 * m.wave(3.3), 0), blink=m.blink(),
                   twitch=twitch)]
    elif state == DROWSY:
        dy, drot, blink = _sleepy(m, state)
        p += [_tail(cs, "M92,102 C104,104 108,96 106,90", (92, 102), 6 * m.wave(2.6)),
              lying(cs, 66, 99, 31, 11, m.wave(3.6)),
              _paws(cs, (28, 44), 107, (1.5 * m.bump(3.7, 0.4), 0)),
              head(cs, 42, 88 + dy * 0.6, rot=-6 + drot * 0.4, eyes="half", droop=0.35, blink=blink or m.blink(3.0, 0.6),
                   twitch=twitch)]
    else:
        dy, drot, _ = _sleepy(m, state)
        p += [_tail(cs, "M92,103 C106,106 110,100 108,94", (92, 103), 3 * m.wave(3.2)),
              lying(cs, 68, 100, 31, 10, m.wave(3.8)),
              _paws(cs, (30, 46), 107),
              head(cs, 42, 92 + dy * 0.5, rot=14 + drot, eyes="sliver", droop=0.6)]
    return "".join(p)


def _curious(cs, state, m, twitch):
    h, w, hy, rot, eyes, droop = _slump(state)
    breath = m.wave(3.4)
    p = [shadow(60, 108, 30),
         _tail(cs, "M78,104 C94,104 98,88 90,80", (78, 104), 10 * m.wave(2.2) if state in (AWAKE, ALERT) else 3 * m.wave(3.4)),
         sitting(cs, 60, 108, w, h, breath), _collar(cs, 60, 108 - h + 4, w)]
    if state == AWAKE:  # watching a moth flutter around: head tilts and eyes follow it
        a = (m.t / 5.0) * 2 * math.pi if m.on else 0.6
        mx, my = 92 + 14 * math.cos(a), 30 + 9 * math.sin(2 * a)
        dx, dy = mx - 60, my - hy
        n = max(1.0, math.hypot(dx, dy))
        tilt = 6 + 14 * math.sin(a)
        p.append(head(cs, 60, hy, rot=tilt, eyes="wide", look=(1.6 * dx / n, 1.6 * dy / n), blink=m.blink(4.6),
                      twitch=twitch))
        p.append(moth(mx, my, abs(math.sin(m.t * 14)) if m.on else 0.2))
    elif state == ALERT:
        p.append(head(cs, 60, hy, rot=9 * m.wave(5.0, 0.25) if m.on else 9, eyes="open",
                      look=(1.4 * m.wave(4.0), 0), blink=m.blink(), twitch=twitch))
    else:
        dy, drot, blink = _sleepy(m, state)
        p.append(head(cs, 60 if state == DROWSY else 58, hy + dy, rot=(rot + drot) if state == NODDING else drot * 0.5,
                      eyes=eyes, droop=droop, blink=(blink or m.blink(3.0, 0.6)) if state == DROWSY else False,
                      twitch=twitch))
    return "".join(p)


def _collar(cs, cx, y, w):
    if cs.accessory != "collar":
        return ""
    return (_path(f"M{cx-12},{y} Q{cx},{y+6} {cx+12},{y}", stroke="#C9A23F", sw=2.6) +
            _ellipse(cx, y + 4.5, 2.3, 2.3, "#E2C46A", "#8A6D1F", sw=0.8))


def _curled(cs, m, deep):
    """Curled up asleep with deep breathing, floating Zzz, ear twitches and (Grok) dream kicks."""
    b = m.wave(4.0 if deep else 3.4)
    p = [shadow(62, 108, 38)]
    if cs.accessory == "glasses":
        p.append(book(98, 108, open_=False, scale=0.7))
        p.append(_g(_glasses(cs), "translate(98,98) scale(0.55)"))
    if cs.accessory == "headphones":
        p.append(laptop(99, 108, open_=False))
    if cs.key == "grok":  # chaotic even asleep: a hind leg up, kicking while it dreams
        kick = 10 * m.wave(0.25) * m.bump(5.5, 1.3)
        leg = (_path("M78,90 L88,70", stroke=cs.outline, sw=7.8) + _path("M78,90 L88,70", stroke=cs.base, sw=5.6) +
               _ellipse(89, 68, 4.2, 3.2, cs.base, cs.outline, sw=1.1))
        p.append(_g(leg, f"rotate({_f(kick)},78,90)"))
    p.append(lying(cs, 62, 94, 32, 14, b))
    tail = cs.other if cs.pattern == "split" else cs.base
    tail_d = "M92,98 C98,110 62,112 40,106"
    tail_g = _path(tail_d, stroke=cs.outline, sw=8.6) + _path(tail_d, stroke=tail, sw=6.2)
    if cs.pattern in ("tabby", "ginger"):
        tail_g += _path(tail_d, stroke=cs.stripe, sw=6.2, extra=' stroke-dasharray="2.2,5" opacity="0.8"')
    p.append(_g(tail_g, f"rotate({_f(2.5 * m.wave(0.5) * m.bump(7.0, 1.0, 3.0))},92,98)"))
    p.append(head(cs, 40, 92 + 0.8 * b, rot=-14, eyes="closed", droop=0.5, twitch=m.bump(6.3, 0.3, 2.0),
                  glasses_dy=None if cs.accessory == "glasses" else 0.0,
                  headphones="neck" if cs.accessory == "headphones" else None))
    p.append(zzz(deep, m))
    return "".join(p)


# ---------------------------------------------------------------- provider state -> cat state
def reset_for(st) -> str | None:
    """'ready' if a banked reset is usable now, 'banked' if one is held, else None."""
    r = st.reading
    if r is None or not r.resets_left:
        return None
    return "ready" if r.reset_usable_now else "banked"


def state_for(st) -> str:
    """Map a ProviderState to a cat state. Special states override the energy level."""
    from quota_core.display import reset_since_last_read
    from quota_core.models import STATUS_ERROR, STATUS_STALE

    r = st.reading
    if r is not None and r.error and "to log in" in r.error:
        return DEAD
    if r is None or r.weekly_left_pct is None:
        return ERROR if r is not None and r.status == STATUS_ERROR else STALE
    if reset_since_last_read(r):
        return RESET
    if r.status == STATUS_STALE:
        return STALE
    if r.status == STATUS_ERROR:
        return ERROR
    return energy_state(r.weekly_left_pct)
