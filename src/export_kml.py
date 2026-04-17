"""KML export for List2GIS.

BayernAtlas accepts plain KML but not KMZ, so the exporter writes a single
self-contained .kml file with marker icons embedded as base64 `data:` URLs.
Each shape is rendered once (white PNG, cached under assets/icons/) and
color is applied at KML-style level via IconStyle.color (aabbggrr tint).
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any

import matplotlib.figure
import pandas as pd
import simplekml
from matplotlib.backends.backend_agg import FigureCanvasAgg

from config_io import Config
from data import STATUS_OK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_CACHE = PROJECT_ROOT / "assets" / "icons"

# FontAwesome icon name → matplotlib marker code. Unknown FA names fall back
# to "o" (filled circle). We can't render the full FA set without shipping
# the FA font; instead we approximate shape with standard matplotlib markers.
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


def export_kml(df: pd.DataFrame, config: Config, output_path: Path) -> Path:
    """Write a self-contained .kml to `output_path`. Returns the path."""
    kml = _build_kml(df, config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kml.save(str(output_path))
    return output_path


def export_kml_bytes(df: pd.DataFrame, config: Config) -> bytes:
    """Return the KML content as UTF-8 bytes (for Streamlit download_button)."""
    kml = _build_kml(df, config)
    # simplekml's .kml() returns the document as a string.
    return kml.kml().encode("utf-8")


def _build_kml(df: pd.DataFrame, config: Config) -> simplekml.Kml:
    kml = simplekml.Kml(name=config.get("name") or "List2GIS export")

    cat_by_value = {c["value"]: c for c in config["categories"]}
    style_by_value: dict[str, Any] = {}
    icon_data_url_by_marker: dict[str, str] = {}

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
        if marker not in icon_data_url_by_marker:
            icon_path = _ensure_icon_png(marker)
            icon_data_url_by_marker[marker] = _png_to_data_url(icon_path)
        icon_href = icon_data_url_by_marker[marker]

        style = simplekml.Style()
        style.iconstyle.icon.href = icon_href
        style.iconstyle.color = _hex_to_kml_color(color_hex)
        style.iconstyle.colormode = simplekml.ColorMode.normal
        style.iconstyle.scale = 1.2
        # scale 0 hides the always-on label text in BayernAtlas/Google Earth;
        # the placemark's `name` still shows in hover/click popups.
        style.labelstyle.scale = 0
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
        pm.extendeddata.newdata(name="category", value=value)
        row_id = str(row.get("_id", "") or "")
        if row_id:
            pm.extendeddata.newdata(name="id", value=row_id)

    return kml


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


def _png_to_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _hex_to_kml_color(hex_color: str) -> str:
    """Convert "#rrggbb" to KML "aabbggrr" (alpha=ff, opaque)."""
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_color)
    if not m:
        return "ffffffff"
    h = m.group(1).lower()
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"ff{bb}{gg}{rr}"
