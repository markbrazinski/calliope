"""Synthetic test images for tests/verify-live.mjs (no people): .venv/bin/python tests/make_images.py <dir>"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw


def person(bg, skin=(200, 160, 130)):
    im = Image.new("RGB", (700, 1200), bg); d = ImageDraw.Draw(im)
    d.ellipse([300, 80, 400, 200], fill=skin); d.rectangle([280, 210, 420, 640], fill=(90, 90, 110)); d.rectangle([290, 640, 410, 1150], fill=(60, 60, 70))
    return im


out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
person((200, 200, 200)).save(out / "shopper-a.jpg", quality=90)
person((150, 160, 175), (170, 130, 105)).save(out / "shopper-b.jpg", quality=90)
Image.new("RGB", (600, 600), (40, 120, 60)).save(out / "top-green.jpg")
Image.new("RGB", (600, 600), (170, 30, 40)).save(out / "top-red.jpg")
Image.new("RGB", (1200, 700), (8, 8, 10)).save(out / "dark-landscape.jpg")
Image.new("RGB", (300, 200), (180, 180, 180)).save(out / "tiny.jpg")
(out / "not-an-image.jpg").write_bytes(b"this is not an image\n")
print("synthetic test images in", out)
