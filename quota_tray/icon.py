"""Tray icon image: a colored rounded tile with the lowest weekly remaining %."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from quota_core.display import color_for_left

SIZE = 64


def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(left: float | None, size: int = SIZE) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=color_for_left(left))
    text = "–" if left is None else f"{left:.0f}"
    # Largest font that fits; "100" needs a smaller size than "5".
    for fs in range(int(size * 0.78), 8, -2):
        font = _font(fs)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        if r - l <= size * 0.9 and b - t <= size * 0.8:
            break
    draw.text(((size - (r - l)) / 2 - l, (size - (b - t)) / 2 - t), text, font=font, fill="white")
    return img


def save_ico(path: str) -> None:
    """App icon for the .exe (a neutral 'Q' tile)."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, 255, 255), radius=52, fill="#2EA043")
    font = _font(190)
    l, t, r, b = d.textbbox((0, 0), "Q", font=font)
    d.text(((256 - (r - l)) / 2 - l, (256 - (b - t)) / 2 - t), "Q", font=font, fill="white")
    img.save(path, sizes=[(s, s) for s in sizes])
