"""Draws the sidebar line icons with Pillow, so they look the same on every PC (no emoji fonts).
Run:  python assets/make_icons.py     ->  laptop_ai/assets/icon_*.png"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "laptop_ai" / "assets"
SS = 4
COLOR = (205, 216, 245, 255)


def _font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make(name: str, px: int = 28, color=COLOR, filled: bool = False) -> Image.Image:
    n = px * SS
    k = n / 28.0                                   # drawing coordinates are on a 28x28 grid
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, int(2.1 * k))

    def P(*pts):
        return [(x * k, y * k) for x, y in pts]

    def line(*pts, closed=False):
        path = P(*pts) + (P(pts[0]) if closed else [])
        d.line(path, fill=color, width=w, joint="curve")
        for x, y in (path[0], path[-1]):           # round the ends
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)

    if name == "home":
        line((3, 13), (14, 3.5), (25, 13))
        line((6, 11.5), (6, 24.5), (22, 24.5), (22, 11.5))
        line((11.5, 24.5), (11.5, 17), (16.5, 17), (16.5, 24.5))
    elif name == "chat":
        d.rounded_rectangle(P((3, 4), (25, 20)), radius=5 * k, outline=color, width=w)
        line((9, 20), (8, 25.5), (14, 20))
        line((9, 10), (19, 10))
        line((9, 14), (15, 14))
    elif name == "mic":
        if filled:
            d.rounded_rectangle(P((9.5, 2), (18.5, 16)), radius=4.5 * k, fill=color)
        else:
            d.rounded_rectangle(P((9.5, 2.5), (18.5, 16)), radius=4.5 * k, outline=color, width=w)
        d.arc(P((5.5, 8), (22.5, 21.5)), 0, 180, fill=color, width=w)
        line((14, 21.5), (14, 26))
        line((10, 26), (18, 26))
    elif name == "folder":
        line((3, 8), (3, 23), (25, 23), (25, 10.5), (13.5, 10.5), (11.5, 7), (4.5, 7), closed=False)
        line((3, 8), (4.5, 7))
    elif name == "gear":
        pts = []
        for i in range(8):
            a = i * math.pi / 4
            for da, r in ((-0.26, 9.3), (-0.17, 12.3), (0.17, 12.3), (0.26, 9.3)):
                pts.append((14 + r * math.cos(a + da), 14 + r * math.sin(a + da)))
        line(*pts, closed=True)
        d.ellipse(P((10, 10), (18, 18)), outline=color, width=w)
    elif name == "help":
        d.ellipse(P((3, 3), (25, 25)), outline=color, width=w)
        d.text((14 * k, 14.3 * k), "?", font=_font(int(15 * k)), fill=color, anchor="mm")
    elif name == "net":
        for (x, y) in ((14, 14), (5, 6), (23, 6), (5, 22), (23, 22)):
            pass
        line((14, 14), (5.5, 6)); line((14, 14), (22.5, 6)); line((14, 14), (5.5, 22)); line((14, 14), (22.5, 22))
        d.ellipse(P((10.5, 10.5), (17.5, 17.5)), fill=color)
        for (x, y) in ((5, 6), (23, 6), (5, 22), (23, 22)):
            d.ellipse(P((x - 2.6, y - 2.6), (x + 2.6, y + 2.6)), outline=color, width=w)
    elif name == "mic_big":
        return make("mic", px, color, filled=True)
    return im.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for nm in ("home", "chat", "mic", "net", "folder", "gear", "help"):
        make(nm, 28).save(OUT / f"icon_{nm}.png")
    make("mic", 72, (255, 255, 255, 255), filled=True).save(OUT / "icon_mic_big.png")
    print("saved icons to", OUT)
