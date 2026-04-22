"""List2GIS — Streamlit entry point."""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from basemaps import BASEMAPS, DEFAULT_BASEMAP
from config_io import (
    CANONICAL_FIELDS,
    DEFAULT_CATEGORY_SIZE_M,
    Config,
    config_path_for,
    load_config,
    save_config,
    seed_config_from_header,
    validate_config,
)
from shapes import ICON_NAMES
from data import (
    STATUS_ERROR,
    STATUS_NEEDS_GEOCODE,
    STATUS_OK,
    build_canonical,
    detect_csv_options,
    read_csv,
)
from export_kml import export_kml_bytes
from geocode import (
    apply_geocode_cache,
    enriched_csv_bytes,
    geocode_missing,
    load_cache,
    lookup,
    save_cache,
)
from mapview import build_map

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
NONE_LABEL = "— none —"


def main() -> None:
    st.set_page_config(page_title="List2GIS", layout="wide")
    st.title("List2GIS")
    st.caption("Visualize a CSV of geo points on a map. See ARCHITECTURE.md for details.")

    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded is None:
        st.info("Upload a CSV to get started.")
        return

    source_bytes = uploaded.getvalue()
    source_name = uploaded.name

    csv_opts = detect_csv_options(io.BytesIO(source_bytes))
    config_path = config_path_for(source_name, CONFIG_DIR)

    cfg_key = f"cfg::{source_name}"
    if cfg_key not in st.session_state:
        st.session_state[cfg_key] = _bootstrap_config(
            source_name, source_bytes, csv_opts, config_path
        )
    cfg: Config = st.session_state[cfg_key]

    try:
        df = read_csv(io.BytesIO(source_bytes), **cfg["csv_options"])
    except Exception as exc:
        st.error(f"Failed to read CSV: {exc}")
        return
    header = list(df.columns)

    _sidebar_file_info(source_name, config_path, csv_opts)
    _sidebar_column_mapping(cfg, header)
    _sidebar_hover_columns(cfg, header)
    _sidebar_categories(cfg)
    _sidebar_default_style(cfg)
    _sidebar_rendering(cfg)
    basemap = _sidebar_basemap_picker()
    _sidebar_save_button(cfg, config_path)

    errors = validate_config(cfg, header)
    if errors:
        st.error("Config has errors:\n\n" + "\n".join(f"- {e}" for e in errors))
        return

    canon = build_canonical(df, cfg)
    geocode_cache = load_cache()
    apply_geocode_cache(canon, geocode_cache)
    _render_status_metrics(canon)
    _sidebar_geocode_section(canon, cfg, df, source_name, geocode_cache)
    _sidebar_export_buttons(canon, cfg, source_name)

    if (canon["_status"] == STATUS_OK).sum() > 0:
        m = build_map(canon, cfg, selected_basemap=basemap)
        st_folium(m, width=None, height=650, returned_objects=[], key="map")
    else:
        st.warning("No rows with valid coordinates yet.")

    _render_problem_rows(canon)


def _bootstrap_config(
    source_name: str,
    source_bytes: bytes,
    csv_opts: dict,
    config_path: Path,
) -> Config:
    if config_path.exists():
        return load_config(config_path)
    # Read header only, using the detected options, to seed column guesses.
    df0 = read_csv(io.BytesIO(source_bytes), **csv_opts)
    return seed_config_from_header(
        Path(source_name).stem,
        source_name,
        list(df0.columns),
        csv_options=csv_opts,
    )


def _sidebar_file_info(source_name: str, config_path: Path, csv_opts: dict) -> None:
    st.sidebar.header("File")
    st.sidebar.write(f"**Source**: `{source_name}`")
    st.sidebar.write(f"**Config**: `{config_path.relative_to(PROJECT_ROOT)}`")
    st.sidebar.caption(
        f"Detected delimiter `{csv_opts['delimiter']}`, encoding `{csv_opts['encoding']}`."
    )


def _sidebar_column_mapping(cfg: Config, header: list[str]) -> None:
    st.sidebar.header("Column mapping")
    options = [NONE_LABEL] + header
    for field in CANONICAL_FIELDS:
        current = cfg["columns"].get(field)
        default_idx = options.index(current) if current in header else 0
        chosen = st.sidebar.selectbox(
            field,
            options=options,
            index=default_idx,
            key=f"col_{field}",
        )
        cfg["columns"][field] = None if chosen == NONE_LABEL else chosen  # type: ignore[literal-required]


def _sidebar_hover_columns(cfg: Config, header: list[str]) -> None:
    st.sidebar.header("Hover columns")
    current = [c for c in cfg["hover_columns"] if c in header]
    cfg["hover_columns"] = st.sidebar.multiselect(
        "Columns shown in marker popup",
        options=header,
        default=current,
        key="hover_cols",
    )


def _ensure_category_uids(cfg: Config) -> None:
    """Attach a stable ephemeral `_uid` to each category so widget keys survive
    reorder/delete. Stripped before save by save_config."""
    for cat in cfg["categories"]:
        if "_uid" not in cat:
            cat["_uid"] = uuid.uuid4().hex[:8]  # type: ignore[typeddict-item]


def _sidebar_categories(cfg: Config) -> None:
    st.sidebar.header("Categories")
    _ensure_category_uids(cfg)

    to_remove: list[int] = []
    for i, cat in enumerate(cfg["categories"]):
        uid = cat["_uid"]  # type: ignore[typeddict-item]
        title = cat.get("label") or cat.get("value") or f"Category {i + 1}"
        with st.sidebar.expander(f"🎨 {title}", expanded=False):
            cat["value"] = st.text_input(
                "Value",
                value=cat.get("value", ""),
                key=f"cat_value_{uid}",
                help="Must match the category column value in the CSV",
            )
            cat["label"] = st.text_input(
                "Label",
                value=cat.get("label", ""),
                key=f"cat_label_{uid}",
            )
            c1, c2 = st.columns([1, 2])
            cat["color"] = c1.color_picker(
                "Color",
                value=cat.get("color") or "#888888",
                key=f"cat_color_{uid}",
            )
            icon_options = list(ICON_NAMES)
            current_icon = cat.get("icon") or "circle"
            icon_index = (
                icon_options.index(current_icon)
                if current_icon in icon_options
                else 0
            )
            cat["icon"] = c2.selectbox(
                "Icon / shape",
                options=icon_options,
                index=icon_index,
                key=f"cat_icon_{uid}",
            )
            cat["size_m"] = st.number_input(
                "Metric radius (m)",
                min_value=0.5, max_value=500.0,
                value=float(cat.get("size_m", DEFAULT_CATEGORY_SIZE_M)),
                step=0.5,
                key=f"cat_size_{uid}",
                help="Used in metric scale mode. Ignored in screen mode.",
            )
            cat["rotation_deg"] = float(st.slider(
                "Tilt (° clockwise)",
                min_value=-180, max_value=180,
                value=int(cat.get("rotation_deg", 0)),
                key=f"cat_rot_{uid}",
            ))
            if st.button("🗑️ Remove category", key=f"cat_del_{uid}"):
                to_remove.append(i)

    for idx in sorted(to_remove, reverse=True):
        cfg["categories"].pop(idx)

    if st.sidebar.button("➕ Add category"):
        cfg["categories"].append({  # type: ignore[typeddict-item]
            "_uid": uuid.uuid4().hex[:8],
            "value": "",
            "label": "",
            "color": "#1f77b4",
            "icon": "circle",
            "size_m": DEFAULT_CATEGORY_SIZE_M,
            "rotation_deg": 0.0,
        })
        st.rerun()

    if to_remove:
        st.rerun()


def _sidebar_default_style(cfg: Config) -> None:
    with st.sidebar.expander("Default style (for unlisted categories)"):
        d = cfg["default_style"]
        c1, c2 = st.columns([1, 2])
        d["color"] = c1.color_picker(
            "Color",
            value=d.get("color", "#888888"),
            key="default_color",
        )
        icon_options = list(ICON_NAMES)
        current = d.get("icon", "circle")
        idx = icon_options.index(current) if current in icon_options else 0
        d["icon"] = c2.selectbox(
            "Icon / shape",
            options=icon_options,
            index=idx,
            key="default_icon",
        )
        d["size_m"] = st.number_input(
            "Metric radius (m)",
            min_value=0.5, max_value=500.0,
            value=float(d.get("size_m", DEFAULT_CATEGORY_SIZE_M)),
            step=0.5,
            key="default_size_m",
        )
        d["rotation_deg"] = float(st.slider(
            "Tilt (° clockwise)",
            min_value=-180, max_value=180,
            value=int(d.get("rotation_deg", 0)),
            key="default_rotation",
        ))


def _sidebar_rendering(cfg: Config) -> None:
    st.sidebar.header("Rendering")
    r = cfg["rendering"]

    mode = st.sidebar.radio(
        "Icon scale",
        options=["screen", "metric"],
        index=0 if r["icon_scale_mode"] == "screen" else 1,
        format_func=lambda m: (
            "Screen-constant (same px at any zoom)"
            if m == "screen"
            else "Metric (true-to-scale)"
        ),
        key="icon_scale_mode",
        help=(
            "Metric mode draws filled polygons sized per-category (set in "
            "the Categories section)."
        ),
    )
    r["icon_scale_mode"] = mode  # type: ignore[typeddict-item]

    if mode == "screen":
        r["icon_size_px"] = st.sidebar.slider(
            "Icon size (px)",
            min_value=10, max_value=80, value=int(r["icon_size_px"]), step=2,
            key="icon_size_px",
        )
    else:
        st.sidebar.caption(
            "Metric radius is set per category (Categories → Metric radius)."
        )

    r["show_labels"] = st.sidebar.checkbox(
        "Show labels on map",
        value=bool(r["show_labels"]),
        key="show_labels",
    )
    if r["show_labels"]:
        r["label_size_px"] = st.sidebar.slider(
            "Label size (px)",
            min_value=8, max_value=24, value=int(r["label_size_px"]), step=1,
            key="label_size_px",
        )


def _sidebar_basemap_picker() -> str:
    st.sidebar.header("View")
    names = list(BASEMAPS.keys())
    return st.sidebar.selectbox(
        "Initial basemap",
        options=names,
        index=names.index(DEFAULT_BASEMAP),
        key="basemap",
    )


def _sidebar_save_button(cfg: Config, config_path: Path) -> None:
    st.sidebar.header("Actions")
    if st.sidebar.button("💾 Save config to disk"):
        save_config(cfg, config_path)
        st.sidebar.success(f"Saved `{config_path.relative_to(PROJECT_ROOT)}`.")


def _sidebar_geocode_section(
    canon: pd.DataFrame,
    cfg: Config,
    df_raw: pd.DataFrame,
    source_name: str,
    cache: dict,
) -> None:
    st.sidebar.header("Geocoding")
    n_needs = int((canon["_status"] == STATUS_NEEDS_GEOCODE).sum())

    if n_needs == 0:
        st.sidebar.caption("No unresolved addresses.")
    else:
        if st.sidebar.button(f"📍 Geocode {n_needs} missing address(es)"):
            addresses = (
                canon.loc[canon["_status"] == STATUS_NEEDS_GEOCODE, "_address"]
                .astype(str)
                .tolist()
            )
            progress = st.sidebar.progress(0.0, text="Starting…")

            def on_progress(done: int, total: int, addr: str) -> None:
                snippet = addr if len(addr) <= 60 else addr[:57] + "…"
                progress.progress(done / total, text=f"{done}/{total}: {snippet}")

            hits = geocode_missing(addresses, cache, on_progress=on_progress)
            save_cache(cache)
            progress.empty()
            st.sidebar.success(
                f"Geocoded {hits} of {len(addresses)}. "
                f"Cached results reused on next run."
            )
            st.rerun()

    ad_col = cfg["columns"].get("address")
    n_filled = 0
    if ad_col and ad_col in df_raw.columns:
        for addr in df_raw[ad_col].astype(str):
            if addr.strip() and lookup(cache, addr) is not None:
                n_filled += 1
    if n_filled > 0:
        enriched = enriched_csv_bytes(df_raw, cfg, cache)
        st.sidebar.download_button(
            label=f"⬇️ Download enriched CSV ({n_filled} filled)",
            data=enriched,
            file_name=Path(source_name).stem + "_geocoded.csv",
            mime="text/csv",
            key="dl_csv_enriched",
        )


def _sidebar_export_buttons(canon: pd.DataFrame, cfg: Config, source_name: str) -> None:
    st.sidebar.header("Export")
    n_ok = int((canon["_status"] == STATUS_OK).sum())
    if n_ok == 0:
        st.sidebar.caption("No rows to export yet.")
        return
    kml_bytes = _kml_bytes_for_download(canon, cfg, source_name)
    st.sidebar.download_button(
        label=f"⬇️ Download KML ({n_ok} points)",
        data=kml_bytes,
        file_name=Path(source_name).stem + ".kml",
        mime="application/vnd.google-earth.kml+xml",
        key="dl_kml",
    )


def _kml_bytes_for_download(
    canon: pd.DataFrame, cfg: Config, source_name: str
) -> bytes:
    """Stable KML bytes across reruns.

    Regenerating bytes on every rerun registers a new id in Streamlit's
    MemoryMediaFileStorage; the old id can be evicted while the browser is
    still fetching it, producing a MediaFileStorageError. Caching by a
    fingerprint of (cfg, canon) keeps the id stable until inputs change.
    """
    cleaned_cats = [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for c in cfg["categories"]
    ]
    cfg_snapshot = json.dumps(
        {**cfg, "categories": cleaned_cats},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    canon_hash = int(pd.util.hash_pandas_object(canon, index=True).sum())
    fingerprint = (cfg_snapshot, canon_hash)
    cache_key = f"kml_cache::{source_name}"
    cached = st.session_state.get(cache_key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    kml_bytes = export_kml_bytes(canon, cfg)
    st.session_state[cache_key] = (fingerprint, kml_bytes)
    return kml_bytes


def _render_status_metrics(canon: pd.DataFrame) -> None:
    n_ok = int((canon["_status"] == STATUS_OK).sum())
    n_geo = int((canon["_status"] == STATUS_NEEDS_GEOCODE).sum())
    n_err = int((canon["_status"] == STATUS_ERROR).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Mapped", n_ok)
    c2.metric("Needs geocoding", n_geo)
    c3.metric("Errors", n_err)


def _render_problem_rows(canon: pd.DataFrame) -> None:
    problems = canon[canon["_status"] != STATUS_OK]
    if len(problems) == 0:
        return
    with st.expander(f"Rows needing attention ({len(problems)})"):
        st.dataframe(
            problems[
                ["_id", "_label", "_category", "_status", "_status_reason", "_address"]
            ],
            width="stretch",
        )


if __name__ == "__main__":
    main()
