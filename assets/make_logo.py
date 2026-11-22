"""Draws the app logo (an AI chip) with Pillow. Run:  python assets/make_logo.py
Outputs laptop_ai/assets/logo_big.png (glowing ring + chip) and logo_small.png (chip only)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "laptop_ai" / "assets"
OUT.mkdir(parents=True, exist_ok=True)
SS = 4                                   # draw 4x bigger, then shrink = smooth edges


def font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_chip(d: ImageDraw.ImageDraw, cx, cy, size, color=(150, 190, 255, 255), text="AI"):
    half = size / 2
    body = size * 0.62 / 2
    w = max(2, int(size * 0.045))
    d.rounded_rectangle([cx - body, cy - body, cx + body, cy + body],
                        radius=size * 0.09, outline=color, width=w, fill=(10, 16, 32, 255))
    pin_len, pin_w, gap = size * 0.13, max(2, int(size * 0.04)), size * 0.17
    for i in (-1.5, -0.5, 0.5, 1.5):                         # 4 pins on every side
        o = i * gap
        d.rounded_rectangle([cx + o - pin_w / 2, cy - half + size * 0.02, cx + o + pin_w / 2, cy - body + w],
                            radius=pin_w / 2, fill=color)
        d.rounded_rectangle([cx + o - pin_w / 2, cy + body - w, cx + o + pin_w / 2, cy + half - size * 0.02],
                            radius=pin_w / 2, fill=color)
        d.rounded_rectangle([cx - half + size * 0.02, cy + o - pin_w / 2, cx - body + w, cy + o + pin_w / 2],
                            radius=pin_w / 2, fill=color)
        d.rounded_rectangle([cx + body - w, cy + o - pin_w / 2, cx + half - size * 0.02, cy + o + pin_w / 2],
                            radius=pin_w / 2, fill=color)
    f = font(int(size * 0.34))
    d.text((cx, cy), text, font=f, fill=(255, 255, 255, 255), anchor="mm")


def small(px=96):
    n = px * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw_chip(ImageDraw.Draw(im), n / 2, n / 2, n * 0.96)
    return im.resize((px, px), Image.LANCZOS)


def big(px=260):
    n = px * SS
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    r = n * 0.36
    gd.ellipse([n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r], outline=(40, 120, 255, 255), width=int(n * 0.03))
    glow = glow.filter(ImageFilter.GaussianBlur(n * 0.035))      # soft outer glow
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    im.alpha_composite(glow)
    d = ImageDraw.Draw(im)
    d.ellipse([n / 2 - r, n / 2 - r, n / 2 + r, n / 2 + r], fill=(8, 12, 24, 255),
              outline=(47, 123, 255, 255), width=int(n * 0.014))
    draw_chip(d, n / 2, n / 2, n * 0.44)
    return im.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    big().save(OUT / "logo_big.png")
    small().save(OUT / "logo_small.png")
    big(256).save(OUT / "app.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved logos to", OUT)
