# List2Map

Visualize a CSV list of geo points (addresses and/or coordinates with a category) as styled markers on a map. Switch between OSM, Bayern Luftbild, and BayernAtlas Topo basemaps. Export to KML (for re-import into BayernAtlas) and PDF.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Run locally

```bash
uv sync
uv run streamlit run src/app.py
```

Requires Python 3.12+. `uv` installs the rest.

## Project layout

```
src/             Streamlit app + modules
config/          Symbology config (categories.csv), basemap URLs
assets/icons/    PNG icons used in KML exports
cache/           Geocoding cache (gitignored)
input/           Local CSV inputs (gitignored)
```
