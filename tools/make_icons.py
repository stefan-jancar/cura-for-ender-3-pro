"""Vygeneruje PNG ikony (PWA, APK, EXE) z rovnakého motívu ako public/assets/icons/icon.svg."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG = (17, 20, 27, 255)
ORANGE = (255, 122, 26)


def motif(d: ImageDraw.ImageDraw, s: float, ox=0.0, oy=0.0):
    def p(x, y):
        return (ox + x * s, oy + y * s)
    d.polygon([p(176, 92), p(336, 92), p(336, 150), p(296, 190), p(296, 224), p(216, 224), p(216, 190), p(176, 150)], fill=(255, 106, 36))
    d.polygon([p(226, 224), p(286, 224), p(256, 276)], fill=(255, 178, 107))
    for (x, y, w, h, a) in ((96, 316, 320, 26, 242), (136, 356, 240, 22, 180), (176, 392, 160, 20, 115)):
        d.rounded_rectangle([p(x, y), p(x + w, y + h)], radius=9 * s, fill=ORANGE + (a,))


def icon(size, rounded=True, pad=0.0):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    if rounded:
        d.rounded_rectangle([0, 0, size - 1, size - 1], radius=size * 0.22, fill=BG)
    else:
        d.rectangle([0, 0, size, size], fill=BG)
    s = size / 512 * (1 - 2 * pad)
    motif(d, s, size * pad, size * pad)
    return img


def main():
    icons = ROOT / "public/assets/icons"
    icon(192).save(icons / "icon-192.png")
    icon(512).save(icons / "icon-512.png")
    icon(512, rounded=False, pad=0.12).save(icons / "maskable-512.png")
    mob = ROOT / "mobile/assets"
    icon(1024, rounded=False).save(mob / "icon-only.png")
    fg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    motif(ImageDraw.Draw(fg, "RGBA"), 1024 / 512 * 0.62, 1024 * 0.19, 1024 * 0.19)
    fg.save(mob / "icon-foreground.png")
    Image.new("RGBA", (1024, 1024), BG).save(mob / "icon-background.png")
    for name in ("splash.png", "splash-dark.png"):
        sp = Image.new("RGBA", (2732, 2732), (11, 13, 18, 255))
        sp.alpha_composite(icon(700), (1016, 1016))
        sp.save(mob / name)
    ico = icon(256)
    (ROOT / "desktop/assets").mkdir(exist_ok=True)
    ico.save(ROOT / "desktop/assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Ikony hotové")


if __name__ == "__main__":
    main()
