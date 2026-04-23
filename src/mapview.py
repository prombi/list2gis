"""Build a Folium map from a canonical DataFrame and a Config."""
from __future__ import annotations

import html as html_lib

import folium
import pandas as pd

from basemaps import BASEMAPS, DEFAULT_BASEMAP
from config_io import DEFAULT_CATEGORY_SIZE_M, Config
from shapes import shape_for_icon, shape_ring_latlon

# FontAwesome 6 (free) — served from cdnjs. Injected into each map so
# DivIcon markers render `<i class="fa fa-...">` glyphs correctly.
_FA_CSS_LINK = (
    '<link rel="stylesheet" '
    'href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" '
    'crossorigin="anonymous" referrerpolicy="no-referrer">'
)

# Updates the font-size of every `.l2g-metric-label` on zoom/move so labels
# scale with the map (i.e. stay ~N meters tall in map units). Uses the
# Web Mercator ground resolution formula: mpp = 156543.03 * cos(lat) / 2^z.
# Finds the Leaflet map by duck-typing on window so it survives Folium's
# auto-generated map variable name changing between reruns.
_METRIC_LABEL_JS = """
<script>
(function () {
  function findMap() {
    for (var k in window) {
      try {
        var v = window[k];
        if (v && typeof v === 'object'
            && v._container
            && typeof v.getZoom === 'function'
            && typeof v.getCenter === 'function') {
          return v;
        }
      } catch (e) { }
    }
    return null;
  }
  function update(map) {
    var zoom = map.getZoom();
    var center = map.getCenter();
    var mpp = 156543.03 * Math.cos(center.lat * Math.PI / 180) / Math.pow(2, zoom);
    var els = document.querySelectorAll('.l2g-metric-label');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var sizeM = parseFloat(el.getAttribute('data-size-m') || '5');
      el.style.fontSize = (sizeM / mpp) + 'px';
    }
  }
  function init() {
    var map = findMap();
    if (!map) { setTimeout(init, 200); return; }
    map.on('zoomend moveend load', function () { update(map); });
    update(map);
  }
  init();
})();
</script>
"""


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
    rendering = config["rendering"]
    scale_mode = rendering["icon_scale_mode"]
    size_px = int(rendering["icon_size_px"])
    show_labels = bool(rendering["show_labels"])
    label_size_px = int(rendering["label_size_px"])
    label_scale_mode = rendering.get("label_scale_mode", "screen")
    label_size_m = float(rendering.get("label_size_m", 5.0))

    if show_labels and label_scale_mode == "metric":
        m.get_root().html.add_child(folium.Element(_METRIC_LABEL_JS))

    for _, row in ok.iterrows():
        cat = cat_by_value.get(str(row["_category"]))
        color = cat["color"] if cat else default_style["color"]
        icon = cat["icon"] if cat else default_style["icon"]
        size_m = float(cat["size_m"]) if cat else float(
            default_style.get("size_m", DEFAULT_CATEGORY_SIZE_M)
        )
        rotation_deg = float(cat["rotation_deg"]) if cat else float(
            default_style.get("rotation_deg", 0.0)
        )

        lat, lon = float(row["_lat"]), float(row["_lon"])
        label = str(row.get("_label", "") or "")
        popup_html = str(row.get("_popup_html", "") or "")
        popup = folium.Popup(popup_html, max_width=400) if popup_html else None
        # Hover tooltip only when labels aren't already drawn as text.
        hover_tooltip = (
            folium.Tooltip(label, sticky=True) if label and not show_labels else None
        )

        if scale_mode == "metric":
            shape = shape_for_icon(icon)
            # Circle is rotation-invariant → cheaper native folium.Circle.
            if shape == "circle" and rotation_deg == 0:
                folium.Circle(
                    location=[lat, lon],
                    radius=size_m,
                    color=color,
                    weight=2,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.6,
                    tooltip=hover_tooltip,
                    popup=popup,
                ).add_to(m)
            else:
                folium.Polygon(
                    locations=shape_ring_latlon(lat, lon, size_m, shape, rotation_deg),
                    color=color,
                    weight=2,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.6,
                    tooltip=hover_tooltip,
                    popup=popup,
                ).add_to(m)
        else:
            folium.Marker(
                location=[lat, lon],
                icon=_fa_divicon(icon, color, size=size_px, rotation_deg=rotation_deg),
                tooltip=hover_tooltip,
                popup=popup,
            ).add_to(m)

        if show_labels and label:
            label_icon = (
                _metric_label_divicon(label, label_size_m)
                if label_scale_mode == "metric"
                else _label_divicon(label, label_size_px)
            )
            folium.Marker(
                location=[lat, lon],
                icon=label_icon,
                interactive=False,
            ).add_to(m)

    if len(ok) > 1:
        sw = [ok["_lat"].min(), ok["_lon"].min()]
        ne = [ok["_lat"].max(), ok["_lon"].max()]
        m.fit_bounds([sw, ne], padding=(20, 20))

    folium.LayerControl(collapsed=False).add_to(m)
    return m


def _metric_label_divicon(text: str, size_m: float) -> folium.DivIcon:
    """Zoom-responsive label: font-size is set (and updated) by _METRIC_LABEL_JS.

    Emits `.l2g-metric-label` with `data-size-m`; the injected script
    recomputes pixel size on zoomend/moveend so the label stays ~size_m
    meters tall in map units.
    """
    safe = html_lib.escape(text)
    inner = (
        f'<div class="l2g-metric-label" data-size-m="{size_m}" style="'
        f'position:absolute;'
        f'top:0;'
        f'left:0;'
        f'transform:translate(-50%, -50%);'
        f'color:#111;'
        f'white-space:nowrap;'
        f'text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,'
        f'-1px 1px 0 #fff,1px 1px 0 #fff;'
        f'pointer-events:none;'
        f'">'
        f"{safe}</div>"
    )
    outer = f'<div style="position:relative;width:0;height:0;">{inner}</div>'
    return folium.DivIcon(icon_size=(0, 0), icon_anchor=(0, 0), html=outer)


def _label_divicon(text: str, font_px: int) -> folium.DivIcon:
    """Pure text label centered on the GPS point (horizontally and vertically).

    Rendered as a zero-size DivIcon; `translate(-50%, -50%)` centers the
    (auto-sized, nowrap) text box on the lat/lon anchor so the midpoint of
    the text sits exactly on the coordinate.
    """
    safe = html_lib.escape(text)
    inner = (
        f'<div style="'
        f'position:absolute;'
        f'top:0;'
        f'left:0;'
        f'transform:translate(-50%, -50%);'
        f'font-size:{font_px}px;'
        f'color:#111;'
        f'white-space:nowrap;'
        f'text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,'
        f'-1px 1px 0 #fff,1px 1px 0 #fff;'
        f'pointer-events:none;'
        f'">'
        f"{safe}</div>"
    )
    outer = f'<div style="position:relative;width:0;height:0;">{inner}</div>'
    return folium.DivIcon(icon_size=(0, 0), icon_anchor=(0, 0), html=outer)


def _fa_divicon(
    icon_name: str, color: str, size: int, rotation_deg: float = 0.0
) -> folium.DivIcon:
    """A DivIcon that renders a single FA glyph in `color`, centered on the point.

    The white text-shadow gives the glyph readable contrast on any basemap
    (aerial, topo, street). Pointer-events stay on the underlying Marker so
    tooltips and popups still open on click.
    """
    rotate_css = f"transform:rotate({rotation_deg}deg);" if rotation_deg else ""
    html = (
        f'<div style="'
        f'font-size:{size}px;'
        f'line-height:{size}px;'
        f'color:{color};'
        f'text-align:center;'
        f'{rotate_css}'
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
