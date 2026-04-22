# List2GIS — Architecture

Minimal web app to visualize a list of geo points (addresses + categories) on a map, with switchable basemaps, KML export, and PDF export of the current view.

## 1. Deployment shape

- **Streamlit single-page app**, runnable locally (`streamlit run src/app.py`) and deployable to [streamlit.io](https://streamlit.io).
- No backend database. All user data stays on the user's machine (uploaded CSV → in-memory session). Streamlit Community Cloud will also work since we never persist uploaded data server-side.
- Desktop browsers only (no mobile layout).

## 2. Stack

| Concern | Choice | Notes |
|---|---|---|
| UI | **Streamlit** | Simple widgets, file upload, session state |
| Map (interactive) | **Folium** + `streamlit-folium` | Leaflet-based; supports WMTS/XYZ layers + custom markers |
| CSV | **pandas** | Handles German `;` delimiter, encoding |
| Geocoding | **geopy** with Nominatim | 1 req/s rate limit, on-disk cache |
| Symbology icons | **FontAwesome** via Folium `Icon(prefix='fa')` | Editable in UI, saved as CSV |
| KML export | **simplekml** | Preserves color + icon per placemark |
| PDF export | **Matplotlib + contextily** | Re-renders current bbox with same symbology; no headless browser needed (Streamlit Cloud friendly) |

**Why not Playwright/Selenium for PDF?** Cleaner dependency story on Streamlit Cloud — Matplotlib/contextily is pure Python. Downside: the PDF won't look pixel-identical to the Folium view, but it uses the same bounds + basemap + markers, which is what matters for a printable deliverable.

## 3. Data model

Canonical in-memory representation (pandas DataFrame):

| Column | Type | Required | Source |
|---|---|---|---|
| `id` | str | yes | derived from `Schlüssel` or row index |
| `label` | str | yes | `Adresse kurz` or fallback |
| `address` | str | one of address/coords | `Adresse komplett` |
| `lat` | float | one of address/coords | parsed from `latlong` |
| `lon` | float | one of address/coords | parsed from `latlong` |
| `category` | str | yes | `Kategorie` |
| `extra` | dict | no | all remaining columns kept verbatim |

### Input CSV handling quirks (from the example file)

- **Delimiter is `;`**; encoding typically `utf-8` or `cp1252`. Autodetect both.
- The `latlong` column contains the correct pair (`47.9642…,11.2925…`). The separate `lat`/`long` columns are **corrupted** by Excel's German locale (decimal points stripped) — we ignore them and parse `latlong` only.
- Rows where `latlong` is empty but `address` is present → send through the geocoder, write the result back so the user can re-export an enriched CSV.
- Rows with neither coords nor address → flagged as errors in a sidebar table, not silently dropped.
- Column mapping is **configurable** via the UI (dropdown per canonical field) so future CSVs with different headers work.

## 4. Dataset config (column mapping + symbology)

One **JSON config per input CSV**, stored in `config/<name>.json` and committed to git (it's mapping logic, not data). The app offers to load an existing config on upload, or to create a fresh one seeded with best-guess column matches.

```json
{
  "name": "Adressen Pöcking",
  "source_file": "Example-Adressen-Kategorien.csv",
  "csv_options": {
    "delimiter": ";",
    "encoding": "utf-8"
  },
  "columns": {
    "id": "Schlüssel",
    "label": "Adresse kurz",
    "latlong": "latlong",
    "lat": null,
    "lon": null,
    "address": "Adresse komplett",
    "category": "Kategorie"
  },
  "hover_columns": ["Adresse kurz", "PLZ", "Ort", "Kategorie"],
  "categories": [
    {"value": "1", "label": "Status 1", "color": "#d62728", "icon": "home", "size_m": 5.0, "rotation_deg": 0},
    {"value": "2", "label": "Status 2", "color": "#ff7f0e", "icon": "star", "size_m": 8.0, "rotation_deg": 45}
  ],
  "default_style": {"color": "#888888", "icon": "circle"},
  "rendering": {
    "icon_scale_mode": "screen",
    "icon_size_px": 28,
    "show_labels": false,
    "label_size_px": 12
  }
}
```

**Rules**

- **No built-in default categories.** The app displays whatever the config specifies. A fresh config starts with an empty `categories` list.
- **Coordinate source is flexible**: loader prefers `latlong` (combined `"lat,lon"` string) → `lat`+`lon` (separate numeric columns) → `address` (geocoded). Only one of the three needs to be present per row.
- `hover_columns` is an ordered list — displayed in that order in the marker popup.
- `categories[].value` is a string (so `"1"`, `"1a"`, `"high"` all work).
- `categories[].size_m` is the metric-mode polygon radius (meters); consumed only when `rendering.icon_scale_mode == "metric"`.
- `categories[].rotation_deg` tilts the marker clockwise (compass sense) — rotates the polygon vertices in metric mode, applies CSS `transform:rotate` in screen mode, and sets KML `IconStyle.heading` on export.
- `default_style` renders rows whose category isn't in the list — prevents unknown categories from breaking the view.
- `csv_options` only override autodetection when needed; usually omittable.
- `rendering` controls global marker size/scaling behavior (per-category size comes from `categories[].size_m`):
  - `icon_scale_mode: "screen"` keeps icons at `icon_size_px` regardless of zoom, rendering the FA glyph as CSS text in the map and as a bitmap icon in KML.
  - `icon_scale_mode: "metric"` draws each marker as a filled polygon of bounding-radius `categories[].size_m` (true-to-world, scales with zoom). Common symbols (circle, square, triangle, diamond, plus, cross, star, pentagon, hexagon) map to shape-specific polygons; irregular FA glyphs (e.g. `home`, `flag`) fall back to a circle. On export these are emitted as KML Polygons rather than iconized Points.
  - `show_labels` toggles an always-visible text label at each point, sized by `label_size_px`. The label is rendered as a centered DivIcon (plain text with a white halo) in the map view and via KML `LabelStyle.scale` on export. When off, hovering a marker still shows a transient tooltip.

**UI editing**

Three sections in the sidebar, each persisted to the same JSON on save:
1. Column mapping — a dropdown per canonical field, populated from the CSV header.
2. Hover columns — multiselect from the CSV header.
3. Category table — `st.data_editor` row-editable (add/remove/reorder).

**Icons**

FontAwesome names only (v1). Curated shortlist in the UI dropdown (`home`, `star`, `flag`, `circle`, `square`, `info`, `check`, `exclamation`, etc.) with free-form text override for any other valid FA name.

**KML icon fidelity**: FA in the Folium map is rendered as CSS font, but KML needs bitmap icons. At export time we'll render the FA glyph + category color to a PNG on the fly and embed the PNGs in a **KMZ** (zipped KML). Implementation detail for the KML export module.

**Legend**: deferred to v2.

## 5. Map providers

Tile layers in order shown to the user:

1. **OSM Standard** — `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (attribution: © OpenStreetMap contributors)
2. **Bayern LDBV Luftbild (DOP)** — LDBV WMTS endpoint (exact URL to confirm at implementation time; likely `https://geoservices.bayern.de/od/wmts/dop/v1/...`). Requires attribution "© Bayerische Vermessungsverwaltung" + Datenlizenz Bayern.
3. **BayernAtlas Topo** — LDBV Topographische Karte WMTS (URL to confirm at implementation time).

Tile URLs will be defined in `config/basemaps.py` so they're easy to swap if an endpoint changes.

## 6. Geocoding

- Provider: **Nominatim** via `geopy.geocoders.Nominatim(user_agent="list2gis")`.
- `geopy.extra.rate_limiter.RateLimiter(min_delay_seconds=1)` to respect ToS.
- **On-disk cache** at `cache/geocode.json` keyed by normalized address → `(lat, lon, display_name)`. Survives session restarts and avoids re-hitting Nominatim.
- Progress bar in the UI while geocoding.
- **v2**: swap in BKG/Bayern geocoder behind the same interface.

## 7. Export

### KML (v1)
- `simplekml`. One `Placemark` per row.
- Style per category: `IconStyle.color` (Google-style `aabbggrr` hex) + `IconStyle.Icon.href` mapped from FontAwesome name to a hosted PNG (small library of preset icons shipped in `assets/icons/`).
- Verified target: re-import into BayernAtlas keeps color + icon.

### PDF (v1)
- "Export current view" button captures the current Folium map `bounds` + active tile layer.
- Matplotlib figure with contextily basemap (using the same tile URL) and markers rendered from the symbology config.
- Page size A4 landscape, title = filename, footer = attribution.

### Other formats
- GeoJSON, GPX, EWKT: **deferred**. KML covers the BayernAtlas use case; we add others only if needed.

## 8. File layout

```
List2GIS/
├── input/
│   └── Example-Adressen-Kategorien.csv
├── config/
│   ├── <dataset>.json          # one per input CSV: column mapping + symbology (user-editable via UI)
│   └── basemaps.py             # tile URLs
├── assets/
│   └── icons/                  # FA glyph → PNG renderer cache (for KMZ export)
├── cache/
│   └── geocode.json            # persistent geocoding cache
├── src/
│   ├── app.py                  # Streamlit entry point
│   ├── data.py                 # CSV load, column mapping, coord parsing
│   ├── config_io.py            # JSON config load/save, seed-from-header helper
│   ├── geocode.py              # Nominatim + cache
│   ├── symbology.py            # category→style lookup
│   ├── mapview.py              # Folium map builder
│   ├── export_kml.py           # emits KMZ with per-category PNG icons
│   └── export_pdf.py
├── pyproject.toml
├── uv.lock
├── ARCHITECTURE.md             # this file
└── README.md
```

## 9. Iteration plan

**v1 (first working build)**
- Load `Example-Adressen-Kategorien.csv`, show markers on OSM basemap, switch to Luftbild/Topo, export KML.

**v1.1**
- Geocoding fallback for address-only rows, with cache and progress.
- Category editor UI + save/load config.

**v1.2**
- PDF export of current view.

**v2**
- Legend on map + in PDF.
- BKG/Bayern geocoder option.
- GeoJSON / GPX / EWKT export if needed.

## 10. Resolved decisions

- **PDF renderer**: Matplotlib + contextily (Streamlit-Cloud-friendly, no headless-browser deps).
- **Icons**: FontAwesome names only (v1). Curated shortlist in the UI with free-form FA-name override. No custom PNG/SVG upload in v1.
- **Category IDs**: strings (so `"1a"`, `"high"` also work).
- **Default categories**: none. Categories come entirely from the per-dataset JSON config; a fresh config starts empty.
