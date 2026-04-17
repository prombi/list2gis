"""KMZ export for List2GIS.

Strategy: ship each KMZ with a small set of white marker PNGs generated
at runtime (cached in assets/icons/). Per-category color is applied via
KML's IconStyle.color tint (aabbggrr), so one PNG per shape is enough —
colors come from the config. Shape comes from a mapping of FontAwesome
icon names to matplotlib marker codes; unknown names fall back to a
filled circle.
"""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path
from typing import Any

import matplotlib.figure
import pandas as pd
import simplekml
from matplotlib.backends.backend_agg import FigureCanvasAgg

from config_io import CategoryStyle, Config
from data import STATUS_OK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_CACHE = PROJECT_ROOT / "assets" / "icons"

# FontAwesome icon name → matplotlib marker code. Unknown FA names fall back
# to "o" (filled circle). Intent: give each category a visually distinct
# shape in the KML, even though the full FA icon set isn't reproducible
# without a FA font dependency.
ICON_TO_MARKER: dict[str, str] = {
    "home": "s",
    "star": "*",
    "circle": "o",
    "square": "s",
    "flag": "^",
    "info": "o",
    "check": "P",
    "exclamation": "X",
    "dot": "o",
    "diamond": "D",
    "triangle": "^",
    "cross": "X",
    "times": "X",
    "pentagon": "p",
    "hexagon": "h",
    "plus": "P",
}

# matplotlib marker code → filename-safe label (so "*" doesn't collide with "^")
MARKER_FILENAMES: dict[str, str] = {
    "o": "circle",
    "s": "square",
    "*": "star",
    "^": "triangle",
    "P": "plus",
    "X": "cross",
    "D": "diamond",
    "p": "pentagon",
    "h": "hexagon",
}

FALLBACK_MARKER = "o"


def export_kmz(df: pd.DataFrame, config: Config, output_path: Path) -> Path:
    """Write a KMZ of all OK rows to `output_path`. Returns the path."""
    kml = simplekml.Kml(name=config.get("name") or "List2GIS export")

    cat_by_value = {c["value"]: c for c in config["categories"]}
    style_by_value: dict[str, Any] = {}
    icon_href_by_marker: dict[str, str] = {}

    def style_for(value: str) -> Any:
        if value in style_by_value:
            return style_by_value[value]
        cat = cat_by_value.get(value)
        if cat is not None:
            color_hex, icon_name = cat["color"], cat["icon"]
        else:
            color_hex = config["default_style"]["color"]
            icon_name = config["default_style"]["icon"]

        marker = ICON_TO_MARKER.get(icon_name, FALLBACK_MARKER)
        if marker not in icon_href_by_marker:
            icon_path = _ensure_icon_png(marker)
            icon_href_by_marker[marker] = kml.addfile(str(icon_path))
        icon_href = icon_href_by_marker[marker]

        style = simplekml.Style()
        style.iconstyle.icon.href = icon_href
        style.iconstyle.color = _hex_to_kml_color(color_hex)
        style.iconstyle.colormode = simplekml.ColorMode.normal
        style.iconstyle.scale = 1.2
        style.labelstyle.scale = 0.9
        style_by_value[value] = style
        return style

    ok = df[df["_status"] == STATUS_OK]
    for _, row in ok.iterrows():
        value = str(row["_category"])
        style = style_for(value)

        name = str(row.get("_label", "") or value or "Point")
        pm = kml.newpoint(
            name=name,
            coords=[(float(row["_lon"]), float(row["_lat"]))],
        )
        pm.style = style
        desc_html = str(row.get("_popup_html", "") or "")
        if desc_html:
            pm.description = desc_html
        # Preserve category value on round-trip (BayernAtlas exposes ExtendedData).
        pm.extendeddata.newdata(name="category", value=value)
        row_id = str(row.get("_id", "") or "")
        if row_id:
            pm.extendeddata.newdata(name="id", value=row_id)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    kml.savekmz(str(output_path))
    return output_path


def export_kmz_bytes(df: pd.DataFrame, config: Config) -> bytes:
    """Like `export_kmz` but returns the KMZ content as bytes (for Streamlit download)."""
    with tempfile.NamedTemporaryFile(suffix=".kmz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        export_kmz(df, config, tmp_path)
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)


def _ensure_icon_png(marker: str, size_px: int = 64) -> Path:
    """Render a white-on-transparent marker PNG to the icon cache."""
    ICON_CACHE.mkdir(parents=True, exist_ok=True)
    filename = MARKER_FILENAMES.get(marker, "circle")
    path = ICON_CACHE / f"{filename}_white.png"
    if path.exists():
        return path

    fig = matplotlib.figure.Figure(figsize=(size_px / 100, size_px / 100), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-1.2, 1.2)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.scatter(
        [0],
        [0],
        marker=marker,
        s=size_px * 20,
        c="white",
        edgecolors="white",
        linewidths=2,
    )
    fig.patch.set_alpha(0)
    fig.savefig(path, transparent=True, dpi=100)
    return path


def _hex_to_kml_color(hex_color: str) -> str:
    """Convert "#rrggbb" to KML "aabbggrr" (alpha=ff, opaque)."""
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_color)
    if not m:
        return "ffffffff"
    h = m.group(1).lower()
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"ff{bb}{gg}{rr}"
