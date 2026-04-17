"""Build a Folium map from a canonical DataFrame and a Config."""
from __future__ import annotations

import folium
import pandas as pd

from basemaps import BASEMAPS, DEFAULT_BASEMAP
from config_io import Config

# FontAwesome 6 (free) — served from cdnjs. Injected into each map so
# DivIcon markers render `<i class="fa fa-...">` glyphs correctly.
_FA_CSS_LINK = (
    '<link rel="stylesheet" '
    'href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" '
    'crossorigin="anonymous" referrerpolicy="no-referrer">'
)

MARKER_SIZE = 28  # px; controls both font-size and icon_anchor


def build_map(
    df: pd.DataFrame,
    config: Config,
    selected_basemap: str | None = None,
) -> folium.Map:
    """Return a Folium map.

    Expects the canonical columns from `data.build_canonical`. Only rows with
    `_status == "ok"` are rendered; the rest are meant to be surfaced in a
    separate UI table. All registered basemaps are added as toggleable layers;
    `selected_basemap` (or DEFAULT_BASEMAP) is the one initially visible.
    """
    ok = df[df["_status"] == "ok"]
    center, zoom = _initial_view(ok)
    initial = selected_basemap if selected_basemap in BASEMAPS else DEFAULT_BASEMAP

    m = folium.Map(location=center, zoom_start=zoom, tiles=None, control_scale=True)
    m.get_root().header.add_child(folium.Element(_FA_CSS_LINK))

    _add_tile_layer(m, initial, show=True)
    for name in BASEMAPS:
        if name != initial:
            _add_tile_layer(m, name, show=False)

    cat_by_value = {c["value"]: c for c in config["categories"]}
    default_style = config["default_style"]

    for _, row in ok.iterrows():
        style = cat_by_value.get(str(row["_category"]))
        color = style["color"] if style else default_style["color"]
        icon = style["icon"] if style else default_style["icon"]

        marker_icon = _fa_divicon(icon, color)

        tooltip = str(row.get("_label", "") or "") or None
        popup_html = str(row.get("_popup_html", "") or "")
        popup = folium.Popup(popup_html, max_width=400) if popup_html else None

        folium.Marker(
            location=[float(row["_lat"]), float(row["_lon"])],
            icon=marker_icon,
            tooltip=tooltip,
            popup=popup,
        ).add_to(m)

    if len(ok) > 1:
        sw = [ok["_lat"].min(), ok["_lon"].min()]
        ne = [ok["_lat"].max(), ok["_lon"].max()]
        m.fit_bounds([sw, ne], padding=(20, 20))

    folium.LayerControl(collapsed=False).add_to(m)
    return m


def _fa_divicon(icon_name: str, color: str, size: int = MARKER_SIZE) -> folium.DivIcon:
    """A DivIcon that renders a single FA glyph in `color`, centered on the point.

    The white text-shadow gives the glyph readable contrast on any basemap
    (aerial, topo, street). Pointer-events stay on the underlying Marker so
    tooltips and popups still open on click.
    """
    html = (
        f'<div style="'
        f'font-size:{size}px;'
        f'line-height:{size}px;'
        f'color:{color};'
        f'text-align:center;'
        f'text-shadow: -1px -1px 0 #fff, 1px -1px 0 #fff,'
        f' -1px 1px 0 #fff, 1px 1px 0 #fff;'
        f'">'
        f'<i class="fa fa-{icon_name}"></i>'
        f'</div>'
    )
    return folium.DivIcon(
        icon_size=(size, size),
        icon_anchor=(size // 2, size // 2),
        html=html,
    )


def _initial_view(ok: pd.DataFrame) -> tuple[list[float], int]:
    if len(ok) == 0:
        return [51.0, 10.5], 6  # center of Germany
    return [float(ok["_lat"].mean()), float(ok["_lon"].mean())], 13


def _add_tile_layer(m: folium.Map, name: str, show: bool) -> None:
    spec = BASEMAPS[name]
    folium.TileLayer(
        tiles=spec["url"],
        attr=spec["attr"],
        name=name,
        max_zoom=spec["max_zoom"],
        control=True,
        show=show,
    ).add_to(m)
