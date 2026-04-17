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

## 4. Symbology config

- Stored as `config/categories.csv`:
  ```
  category,label,color,icon
  1,Status 1,#d62728,home
  2,Status 2,#ff7f0e,star
  …
  ```
- Example data has categories **1–6** (not 1–4 as originally described). The app supports any integer or string category; missing categories fall back to a default gray circle.
- Edited in-app via a table editor (`st.data_editor`). Save button writes `config/categories.csv`; load button reads it.
- Color = hex. Icon = FontAwesome name (from a curated shortlist to keep the UI sane; user can still type any valid fa name).
- **Legend**: deferred to v2.

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
│   ├── categories.csv          # default symbology (user-editable via UI)
│   └── basemaps.py             # tile URLs
├── assets/
│   └── icons/                  # PNGs mapped from FontAwesome names (for KML)
├── cache/
│   └── geocode.json            # persistent geocoding cache
├── src/
│   ├── app.py                  # Streamlit entry point
│   ├── data.py                 # CSV load, column mapping, coord parsing
│   ├── geocode.py              # Nominatim + cache
│   ├── symbology.py            # category→style lookup, config CSV I/O
│   ├── mapview.py              # Folium map builder
│   ├── export_kml.py
│   └── export_pdf.py
├── requirements.txt
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

## 10. Decisions still open

1. **PDF renderer**: Matplotlib/contextily (recommended, cloud-friendly) vs. Playwright headless screenshot (pixel-identical to Folium but heavier deps). Current pick: Matplotlib.
2. **Icon set for markers**: a curated FontAwesome shortlist (e.g. home, star, flag, circle, square, triangle, exclamation, check) vs. free-form input. Current pick: shortlist with free-form override.
3. **Category IDs**: treat as strings (so `"1a"`, `"high"` also work) vs. integers only. Current pick: strings.
