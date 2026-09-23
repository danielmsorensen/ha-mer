"""Build the integration's brand images from Mer's official logo.

Home Assistant serves a custom integration's icon from a `brand/` folder inside the
integration (`custom_components/mer/brand/`), using the home-assistant/brands file
names: `icon.png` (256x256), `icon@2x.png` (512x512), `logo.png`, `logo@2x.png`, all
PNG with transparency. Dark variants fall back to these when absent.

Source: the "no wordmark" RGB logo published on Mer UK's website (mer.eco is Mer's
own domain; the file is the three-bar gradient mark followed by the "mer" wordmark on
white, 2751x834). The icon is the square mark cropped from its left edge; the logo is
the whole image with the white background made transparent so it sits on dark themes.

One-off; not part of any build step. Needs only Pillow, which Home Assistant already
depends on, so run it with the dev venv's interpreter from WSL:

    ~/.venvs/ha-mer/bin/python /mnt/d/GitHub/Personal/ha-mer/scripts/generate_brand_icon.py
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

SOURCE_URL = "https://uk.mer.eco/wp-content/uploads/sites/9/2025/06/mer-logo-rgb-no-wordmark.jpg"
BRAND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "mer" / "brand"
# JPEG compression leaves a faint fringe where the mark meets the white background;
# cropping a few pixels inside the square avoids a pale edge after downscaling.
INSET = 4
# Pixels at least this bright in every channel are background in the source.
WHITE_THRESHOLD = 240


def fetch(url: str) -> Image.Image:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (ha-mer brand build)"})
    with urlopen(request, timeout=30) as response:
        return Image.open(BytesIO(response.read())).convert("RGB")


def square_mark(source: Image.Image) -> Image.Image:
    """The gradient square: full height of the image, starting at its left edge."""
    side = source.height
    return source.crop((INSET, INSET, side - INSET, side - INSET))


def white_to_transparent(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            if min(r, g, b) >= WHITE_THRESHOLD:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def trim(image: Image.Image) -> Image.Image:
    """Crop to the opaque content, then pad to a tidy margin."""
    box = image.getchannel("A").getbbox()
    if box is None:
        return image
    cropped = image.crop(box)
    margin = cropped.height // 12
    padded = Image.new("RGBA", (cropped.width + 2 * margin, cropped.height + 2 * margin))
    padded.paste(cropped, (margin, margin))
    return padded


def resized(image: Image.Image, width: int) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.LANCZOS)


def main() -> None:
    source = fetch(SOURCE_URL)
    BRAND_DIR.mkdir(parents=True, exist_ok=True)

    mark = square_mark(source).convert("RGBA")
    mark.resize((256, 256), Image.LANCZOS).save(BRAND_DIR / "icon.png")
    mark.resize((512, 512), Image.LANCZOS).save(BRAND_DIR / "icon@2x.png")

    logo = trim(white_to_transparent(source))
    resized(logo, 512).save(BRAND_DIR / "logo.png")
    resized(logo, 1024).save(BRAND_DIR / "logo@2x.png")
    for name in ("icon.png", "icon@2x.png", "logo.png", "logo@2x.png"):
        with Image.open(BRAND_DIR / name) as written:
            print(f"{name}: {written.size[0]}x{written.size[1]} {written.mode}")


if __name__ == "__main__":
    main()
