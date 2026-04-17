"""Basemap registry for List2GIS.

Each entry becomes a selectable tile layer in the Folium map. To swap a
provider or endpoint, edit this file — no other code changes needed.

Attribution:
- OSM: "© OpenStreetMap contributors" (ODbL, attribution required).
- LDBV Bayern: "Datenlizenz Bayern — Namensnennung 2.0", attribution to
  "© Bayerische Vermessungsverwaltung".
"""
from __future__ import annotations

from typing import TypedDict


class Basemap(TypedDict):
    url: str
    attr: str
    max_zoom: int


# NOTE: The LDBV WMTS URL templates below follow the documented public-data
# pattern (https://geoservices.bayern.de/od/...). If tiles don't load in the
# browser, open the network panel to see the real request that BayernAtlas
# itself makes and update the `url` here. Keep the {z}/{y}/{x} placeholders
# in whatever order the service expects.
BASEMAPS: dict[str, Basemap] = {
    "OSM Standard": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors",
        "max_zoom": 19,
    },
    "Bayern Luftbild (LDBV DOP)": {
        "url": "https://geoservices.bayern.de/od/wmts/geo/1.0.0/by_dop20c/default/webmercator/{z}/{y}/{x}.png",
        "attr": "© Bayerische Vermessungsverwaltung – Datenlizenz Bayern",
        "max_zoom": 20,
    },
    "BayernAtlas Topo (LDBV TK)": {
        "url": "https://geoservices.bayern.de/od/wmts/geo/1.0.0/by_tk/default/webmercator/{z}/{y}/{x}.png",
        "attr": "© Bayerische Vermessungsverwaltung – Datenlizenz Bayern",
        "max_zoom": 15,
    },
}

DEFAULT_BASEMAP = "OSM Standard"
