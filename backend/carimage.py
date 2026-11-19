"""Foto des Autos: Ein Bild in data/ wird automatisch freigestellt (heller
Hintergrund wird transparent), zugeschnitten und verkleinert.

Das Ergebnis wird in data/.car-cutout.png zwischengespeichert und nur neu
erzeugt, wenn sich das Foto ändert.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

log = logging.getLogger("elektrocar.carimage")

NAMES = ("car.webp", "car.png", "car.jpg", "car.jpeg")
CACHE_NAME = ".car-cutout.png"
MAX_WIDTH = 1200
# Ab welcher Helligkeit / wie wenig Farbe ein Pixel als Hintergrund gilt
WHITE_MIN = 225
WHITE_MAX_CHROMA = 24


def find_photo(data_dir: Path) -> Path | None:
    """Neuestes Foto in data/ (car.webp, .png, .jpg)."""

    found = [data_dir / n for n in NAMES if (data_dir / n).is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def cutout(data_dir: Path) -> Path | None:
    """Pfad zum fertigen Bild, oder None ohne Foto."""

    src = find_photo(data_dir)
    if src is None:
        return None
    cache = data_dir / CACHE_NAME
    stamp = f"{src.name}:{src.stat().st_mtime_ns}:{src.stat().st_size}"
    stamp_file = data_dir / (CACHE_NAME + ".src")
    if cache.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return cache

    try:
        img = _process(Image.open(src))
        img.save(cache, optimize=True)
        stamp_file.write_text(stamp)
        log.info("Foto %s aufbereitet (%dx%d)", src.name, *img.size)
        return cache
    except Exception:  # noqa: BLE001
        log.exception("Foto %s konnte nicht aufbereitet werden, nutze Original", src.name)
        return src


def _process(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGBA")
    if img.width > MAX_WIDTH * 1.6:
        img.thumbnail((int(MAX_WIDTH * 1.6), int(MAX_WIDTH * 1.6)), Image.LANCZOS)

    alpha = np.asarray(img.getchannel("A"))
    if (alpha < 250).mean() < 0.02:
        # nicht (nennenswert) transparent: hellen Hintergrund entfernen
        img.putalpha(_background_mask(img))

    bbox = img.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    if img.width > MAX_WIDTH:
        img.thumbnail((MAX_WIDTH, MAX_WIDTH), Image.LANCZOS)
    return img


def _background_mask(img: Image.Image) -> Image.Image:
    """Alpha-Maske: heller Hintergrund, der vom Bildrand aus zusammenhängend
    erreichbar ist, wird transparent (helle Stellen im Auto bleiben erhalten)."""

    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    light = (rgb.min(axis=2) >= WHITE_MIN) & (np.ptp(rgb, axis=2) <= WHITE_MAX_CHROMA)

    # Füllung vom Rand aus: wiederholt um ein Pixel wachsen, nur über helle Pixel
    bg = np.zeros_like(light)
    bg[0, :], bg[-1, :], bg[:, 0], bg[:, -1] = light[0, :], light[-1, :], light[:, 0], light[:, -1]
    for _ in range(max(light.shape)):
        grown = bg.copy()
        grown[1:, :] |= bg[:-1, :]
        grown[:-1, :] |= bg[1:, :]
        grown[:, 1:] |= bg[:, :-1]
        grown[:, :-1] |= bg[:, 1:]
        grown &= light
        if np.array_equal(grown, bg):
            break
        bg = grown

    mask = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))
    # Kante leicht nach innen und weich, gegen weiße Säume
    return mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
