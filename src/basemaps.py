"""Basemap registry for List2GIS.

Each entry becomes a selectable tile layer in the Folium map. To swap a
provider or endpoint, edit this file — no other code changes needed.

NOTE on Bayern LDBV: the open-data WMTS URLs I tried first
(`geoservices.bayern.de/od/wmts/geo/1.0.0/...`) returned no tiles, so v1
ships reliable Esri/OpenTopoMap replacements. Once the real LDBV endpoint
template is confirmed (likely via their GetCapabilities XML), add entries
here like:

    "Bayern Luftbild (LDBV DOP)": {
        "url": "https://<lbdv-wmts>/{z}/{y}/{x}.png",
        "attr": "© Bayerische Vermessungsverwaltung – Datenlizenz Bayern",
        "max_zoom": 20,
    },

Attribution:
- OSM: "© OpenStreetMap contributors" (ODbL).
- Esri World Imagery: "Tiles © Esri — Sources: Esri, Maxar, Earthstar
  Geographics, and the GIS User Community".
- OpenTopoMap: "Map data: © OpenStreetMap contributors, SRTM; Map style:
  © OpenTopoMap (CC-BY-SA)".
"""
from __future__ import annotations

from typing import TypedDict


class Basemap(TypedDict):
    url: str
    attr: str
    max_zoom: int


BASEMAPS: dict[str, Basemap] = {
    "OSM Standard": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors",
        "max_zoom": 19,
    },
    "Luftbild (Esri World Imagery)": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Tiles © Esri — Maxar, Earthstar Geographics, GIS User Community",
        "max_zoom": 19,
    },
    "Topografisch (OpenTopoMap)": {
        "url": "https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attr": "© OpenStreetMap contributors, SRTM | Style: © OpenTopoMap (CC-BY-SA)",
        "max_zoom": 17,
    },
}

DEFAULT_BASEMAP = "OSM Standard"
