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

from config_io import DEFAULT_CATEGORY_SIZE_M, Config
from data import STATUS_OK
from shapes import shape_for_icon, shape_ring_lonlat

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_CACHE = PROJECT_ROOT / "assets" / "icons"

# 1x1 transparent PNG used as the <Icon> for label-only Point placemarks in
# metric mode. BayernAtlas (and several other KML renderers) only attach
# placemark labels to Point geometries — never to Polygons or MultiGeometry —
# so metric-mode labels are emitted as a sibling Point at the same coord.
# IconStyle.scale=0 suppresses the label too, so we need a real (but
# invisible) icon to anchor it.
_TRANSPARENT_PIXEL_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "AAIAAAoAAv/lxKUAAAAASUVORK5CYII="
)

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
    label_style_cache: dict[str, Any] = {}

    rendering = config["rendering"]
    scale_mode = rendering["icon_scale_mode"]
    icon_size_px = int(rendering["icon_size_px"])
    show_labels = bool(rendering["show_labels"])
    label_scale = (float(rendering["label_size_px"]) / 16.0) if show_labels else 0.0
    # Source PNGs are rendered at 64 px and BayernAtlas displays IconStyle.scale=1
    # at roughly that size, so px/32 keeps screen icons visually matched.
    icon_scale = icon_size_px / 32.0

    def label_only_style() -> Any:
        if "s" not in label_style_cache:
            s = simplekml.Style()
            # Invisible (1x1 transparent) icon so the label renders without
            # a pushpin. IconStyle.scale=0 would suppress the label too.
            s.iconstyle.icon.href = _TRANSPARENT_PIXEL_DATA_URL
            s.iconstyle.scale = 1.0
            s.labelstyle.scale = label_scale
            label_style_cache["s"] = s
        return label_style_cache["s"]

    def style_for(value: str) -> Any:
        if value in style_by_value:
            return style_by_value[value]
        cat = cat_by_value.get(value)
        if cat is not None:
            color_hex = cat["color"]
            icon_name = cat["icon"]
            rotation_deg = float(cat.get("rotation_deg", 0.0))
        else:
            d = config["default_style"]
            color_hex = d["color"]
            icon_name = d["icon"]
            rotation_deg = float(d.get("rotation_deg", 0.0))

        style = simplekml.Style()
        if scale_mode == "screen":
            marker = ICON_TO_MARKER.get(icon_name, FALLBACK_MARKER)
            if marker not in icon_data_url_by_marker:
                icon_path = _ensure_icon_png(marker)
                icon_data_url_by_marker[marker] = _png_to_data_url(icon_path)
            style.iconstyle.icon.href = icon_data_url_by_marker[marker]
            style.iconstyle.color = _hex_to_kml_color(color_hex)
            style.iconstyle.colormode = simplekml.ColorMode.normal
            style.iconstyle.scale = icon_scale
            # KML IconStyle.heading: clockwise from north. Matches our convention.
            if rotation_deg:
                style.iconstyle.heading = rotation_deg
            style.labelstyle.scale = label_scale
        else:
            # Metric mode: the Polygon carries the visible shape. No label on
            # this style — BayernAtlas won't render labels from Polygons, so
            # the label is emitted on a sibling Point placemark below.
            style.iconstyle.scale = 0
            style.polystyle.color = _hex_to_kml_color(color_hex, alpha=0x99)
            style.linestyle.color = _hex_to_kml_color(color_hex)
            style.linestyle.width = 2
            style.labelstyle.scale = 0
        style_by_value[value] = style
        return style

    ok = df[df["_status"] == STATUS_OK]
    for _, row in ok.iterrows():
        value = str(row["_category"])
        style = style_for(value)
        name = str(row.get("_label", "") or value or "Point")
        lat = float(row["_lat"])
        lon = float(row["_lon"])

        if scale_mode == "metric":
            cat = cat_by_value.get(value)
            if cat is not None:
                icon_name = cat["icon"]
                size_m = float(cat["size_m"])
                rotation_deg = float(cat["rotation_deg"])
            else:
                d = config["default_style"]
                icon_name = d["icon"]
                size_m = float(d.get("size_m", DEFAULT_CATEGORY_SIZE_M))
                rotation_deg = float(d.get("rotation_deg", 0.0))
            shape = shape_for_icon(icon_name)
            ring = shape_ring_lonlat(lat, lon, size_m, shape, rotation_deg)

            poly_pm = kml.newpolygon(outerboundaryis=ring)
            poly_pm.style = style
            _attach_metadata(poly_pm, row, value)

            # Sibling Point placemark that carries the label text. Only emitted
            # when labels are turned on; without this, BayernAtlas shows no
            # label at all for metric-mode polygons.
            if show_labels and name:
                lbl_pm = kml.newpoint(name=name, coords=[(lon, lat)])
                lbl_pm.style = label_only_style()
        else:
            pm = kml.newpoint(name=name, coords=[(lon, lat)])
            pm.style = style
            _attach_metadata(pm, row, value)

    return kml


def _attach_metadata(pm: Any, row: pd.Series, value: str) -> None:
    desc_html = str(row.get("_popup_html", "") or "")
    if desc_html:
        pm.description = desc_html
    pm.extendeddata.newdata(name="category", value=value)
    row_id = str(row.get("_id", "") or "")
    if row_id:
        pm.extendeddata.newdata(name="id", value=row_id)


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


def _hex_to_kml_color(hex_color: str, alpha: int = 0xFF) -> str:
    """Convert "#rrggbb" to KML "aabbggrr"; `alpha` is 0–255 (default opaque)."""
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", hex_color)
    if not m:
        return f"{alpha:02x}ffffff"
    h = m.group(1).lower()
    rr, gg, bb = h[0:2], h[2:4], h[4:6]
    return f"{alpha:02x}{bb}{gg}{rr}"
