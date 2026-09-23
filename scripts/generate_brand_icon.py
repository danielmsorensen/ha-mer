"""Generate the integration's brand icon.

Home Assistant serves a custom integration's own icon from a `brand/` folder inside
the integration (`custom_components/mer/brand/`), with the same file names the
home-assistant/brands repository uses: `icon.png` (256x256) and `icon@2x.png`
(512x512), square PNGs on a transparent background. `logo*.png` and `dark_*.png`
variants fall back to these when absent.

Until Mer's own logo is dropped in (just overwrite the two PNGs), this renders the
MDI "ev-station" glyph that the integration's availability entities already use, so
the integration page, the entity icons and the "Add integration" list all match.

One-off; not part of any build step. Needs cairosvg (`pip install cairosvg` into the
dev venv), which in turn needs the cairo library, present in the WSL Ubuntu image.
Run it with the venv's interpreter, from WSL:

    ~/.venvs/ha-mer/bin/python /mnt/d/GitHub/Personal/ha-mer/scripts/generate_brand_icon.py

The path data is MDI's ev-station icon (24x24 viewBox), Apache-2.0 licensed,
taken from the icon set bundled with the Home Assistant frontend.
"""

from __future__ import annotations

from pathlib import Path

import cairosvg

BRAND_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "mer" / "brand"

# A plain EV green rather than a brand colour: this is a placeholder glyph, not Mer's mark.
ICON_COLOR = "#1DB954"

MDI_EV_STATION_PATH = (
    "M19.77,7.23L19.78,7.22L16.06,3.5L15,4.56L17.11,6.67C16.17,7.03 15.5,7.93 15.5,9"
    "A2.5,2.5 0 0,0 18,11.5C18.36,11.5 18.69,11.42 19,11.29V18.5A1,1 0 0,1 18,19.5"
    "A1,1 0 0,1 17,18.5V14A2,2 0 0,0 15,12H14V5A2,2 0 0,0 12,3H6A2,2 0 0,0 4,5V21H14"
    "V13.5H15.5V18.5A2.5,2.5 0 0,0 18,21A2.5,2.5 0 0,0 20.5,18.5V9C20.5,8.31 20.22,7.68"
    " 19.77,7.23M18,10A1,1 0 0,1 17,9A1,1 0 0,1 18,8A1,1 0 0,1 19,9A1,1 0 0,1 18,10"
    "M8,18V13.5H6L10,6V11H12L8,18Z"
)


def draw_icon(size: int) -> bytes:
    """Render the glyph as PNG bytes at the given square size."""
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"
         viewBox="0 0 24 24">
      <path d="{MDI_EV_STATION_PATH}" fill="{ICON_COLOR}" />
    </svg>
    """
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=size, output_height=size)


def main() -> None:
    """Write icon.png (256x256) and icon@2x.png (512x512) to BRAND_DIR."""
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    (BRAND_DIR / "icon.png").write_bytes(draw_icon(256))
    (BRAND_DIR / "icon@2x.png").write_bytes(draw_icon(512))
    print(f"wrote icon.png and icon@2x.png to {BRAND_DIR}")


if __name__ == "__main__":
    main()
