"""List2GIS — Streamlit entry point."""
from __future__ import annotations

import io
import json
import re
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
    header_matches,
    list_presets,
    load_config,
    preset_path,
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
NEW_PRESET_LABEL = "(new from header)"


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

    try:
        df0 = read_csv(io.BytesIO(source_bytes), **csv_opts)
    except Exception as exc:
        st.error(f"Failed to read CSV: {exc}")
        return
    header0 = list(df0.columns)

    preset = _sidebar_preset_picker(source_name, header0)
    _sidebar_preset_import(source_name)
    scope = f"{preset}__{source_name}"

    cfg_key = f"cfg::{preset}::{source_name}"
    if cfg_key not in st.session_state:
        st.session_state[cfg_key] = _bootstrap_config(
            preset, source_name, header0, csv_opts
        )
    cfg: Config = st.session_state[cfg_key]

    try:
        df = read_csv(io.BytesIO(source_bytes), **cfg["csv_options"])
    except Exception as exc:
        st.error(f"Failed to read CSV: {exc}")
        return
    header = list(df.columns)

    _sidebar_file_info(source_name, preset, csv_opts)
    _sidebar_column_mapping(cfg, header, scope)
    _sidebar_hover_columns(cfg, header, scope)
    _sidebar_categories(cfg, scope)
    _sidebar_default_style(cfg, scope)
    _sidebar_rendering(cfg, scope)
    basemap = _sidebar_basemap_picker(scope)
    _sidebar_save_section(cfg, preset, source_name, scope)

    errors = validate_config(cfg, header)
    if errors:
        st.error("Config has errors:\n\n" + "\n".join(f"- {e}" for e in errors))
        return

    canon = build_canonical(df, cfg)
    geocode_cache = load_cache()
    apply_geocode_cache(canon, geocode_cache)
    _render_status_metrics(canon)
    _sidebar_export_buttons(canon, cfg, source_name)

    if (canon["_status"] == STATUS_OK).sum() > 0:
        m = build_map(canon, cfg, selected_basemap=basemap)
        st_folium(m, width=None, height=650, returned_objects=[], key=f"map__{scope}")
    else:
        st.warning("No rows with valid coordinates yet.")

    _render_attention_section(canon, cfg, df, source_name, geocode_cache, scope)


def _bootstrap_config(
    preset: str,
    source_name: str,
    header: list[str],
    csv_opts: dict,
) -> Config:
    if preset != NEW_PRESET_LABEL:
        path = preset_path(preset, CONFIG_DIR)
        if path.exists():
            return load_config(path)
    return seed_config_from_header(
        Path(source_name).stem,
        header,
        csv_options=csv_opts,
    )


def _auto_pick_preset(source_name: str, header: list[str], presets: list[str]) -> str:
    """Filename-stem match first, then first preset whose columns all exist in header."""
    stem = Path(source_name).stem
    if stem in presets:
        return stem
    for name in presets:
        try:
            cfg = load_config(preset_path(name, CONFIG_DIR))
        except Exception:
            continue
        if header_matches(cfg, header):
            return name
    return NEW_PRESET_LABEL


def _sidebar_preset_picker(source_name: str, header: list[str]) -> str:
    st.sidebar.header("Config preset")
    presets = list_presets(CONFIG_DIR)
    options = [NEW_PRESET_LABEL] + presets

    sel_key = f"preset_sel__{source_name}"
    force_key = f"preset_force__{source_name}"
    # A prior save action may have requested a specific preset. Apply it
    # here, before the selectbox is instantiated — Streamlit forbids writes
    # to a widget's session_state key after the widget has rendered.
    forced = st.session_state.pop(force_key, None)
    if forced is not None and forced in options:
        st.session_state[sel_key] = forced
    elif sel_key not in st.session_state:
        st.session_state[sel_key] = _auto_pick_preset(source_name, header, presets)
    if st.session_state[sel_key] not in options:
        st.session_state[sel_key] = NEW_PRESET_LABEL

    chosen = st.sidebar.selectbox(
        "Active preset",
        options=options,
        key=sel_key,
        help=(
            "Presets are named column-mapping + style configurations stored "
            "under config/. They're independent of the CSV filename."
        ),
    )
    return chosen


def _sidebar_preset_import(source_name: str) -> None:
    """Let the user import a preset JSON from disk (needed on streamlit.io,
    where users can't drop files into the repo's config/ folder)."""
    with st.sidebar.expander("📂 Import preset JSON"):
        uploaded = st.file_uploader(
            "Upload a .json preset",
            type=["json"],
            key=f"preset_import__{source_name}",
        )
        if uploaded is None:
            return
        try:
            data = json.loads(uploaded.getvalue().decode("utf-8"))
        except Exception as exc:
            st.error(f"Not valid JSON: {exc}")
            return
        if not isinstance(data, dict):
            st.error("Preset JSON must be an object at the top level.")
            return

        derived = _clean_preset_name(str(data.get("name") or Path(uploaded.name).stem))
        name_input = st.text_input(
            "Import as",
            value=derived,
            key=f"preset_import_name__{source_name}",
            help="You can rename the preset before importing.",
        )
        if st.button("Import", key=f"preset_import_btn__{source_name}"):
            cleaned = _clean_preset_name(name_input)
            if not cleaned:
                st.error("Enter a preset name (letters, digits, `-` or `_`).")
                return
            target = preset_path(cleaned, CONFIG_DIR)
            if target.exists():
                st.error(f"Preset `{cleaned}` already exists. Pick a different name.")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(uploaded.getvalue())
            st.session_state[f"preset_force__{source_name}"] = cleaned
            st.rerun()


def _sidebar_file_info(source_name: str, preset: str, csv_opts: dict) -> None:
    st.sidebar.header("File")
    st.sidebar.write(f"**Source**: `{source_name}`")
    label = preset if preset != NEW_PRESET_LABEL else "(unsaved — new from header)"
    st.sidebar.write(f"**Preset**: `{label}`")
    st.sidebar.caption(
        f"Detected delimiter `{csv_opts['delimiter']}`, encoding `{csv_opts['encoding']}`."
    )


def _sidebar_column_mapping(cfg: Config, header: list[str], scope: str) -> None:
    st.sidebar.header("Column mapping")
    options = [NONE_LABEL] + header
    for field in CANONICAL_FIELDS:
        current = cfg["columns"].get(field)
        default_idx = options.index(current) if current in header else 0
        chosen = st.sidebar.selectbox(
            field,
            options=options,
            index=default_idx,
            key=f"col_{field}__{scope}",
        )
        cfg["columns"][field] = None if chosen == NONE_LABEL else chosen  # type: ignore[literal-required]


def _sidebar_hover_columns(cfg: Config, header: list[str], scope: str) -> None:
    st.sidebar.header("Hover columns")
    current = [c for c in cfg["hover_columns"] if c in header]
    cfg["hover_columns"] = st.sidebar.multiselect(
        "Columns shown in marker popup",
        options=header,
        default=current,
        key=f"hover_cols__{scope}",
    )


def _ensure_category_uids(cfg: Config) -> None:
    """Attach a stable ephemeral `_uid` to each category so widget keys survive
    reorder/delete. Stripped before save by save_config."""
    for cat in cfg["categories"]:
        if "_uid" not in cat:
            cat["_uid"] = uuid.uuid4().hex[:8]  # type: ignore[typeddict-item]


def _sidebar_categories(cfg: Config, scope: str) -> None:
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

    if st.sidebar.button("➕ Add category", key=f"cat_add__{scope}"):
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


def _sidebar_default_style(cfg: Config, scope: str) -> None:
    with st.sidebar.expander("Default style (for unlisted categories)"):
        d = cfg["default_style"]
        c1, c2 = st.columns([1, 2])
        d["color"] = c1.color_picker(
            "Color",
            value=d.get("color", "#888888"),
            key=f"default_color__{scope}",
        )
        icon_options = list(ICON_NAMES)
        current = d.get("icon", "circle")
        idx = icon_options.index(current) if current in icon_options else 0
        d["icon"] = c2.selectbox(
            "Icon / shape",
            options=icon_options,
            index=idx,
            key=f"default_icon__{scope}",
        )
        d["size_m"] = st.number_input(
            "Metric radius (m)",
            min_value=0.5, max_value=500.0,
            value=float(d.get("size_m", DEFAULT_CATEGORY_SIZE_M)),
            step=0.5,
            key=f"default_size_m__{scope}",
        )
        d["rotation_deg"] = float(st.slider(
            "Tilt (° clockwise)",
            min_value=-180, max_value=180,
            value=int(d.get("rotation_deg", 0)),
            key=f"default_rotation__{scope}",
        ))


def _sidebar_rendering(cfg: Config, scope: str) -> None:
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
        key=f"icon_scale_mode__{scope}",
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
            key=f"icon_size_px__{scope}",
        )
    else:
        st.sidebar.caption(
            "Metric radius is set per category (Categories → Metric radius)."
        )

    r["show_labels"] = st.sidebar.checkbox(
        "Show labels on map",
        value=bool(r["show_labels"]),
        key=f"show_labels__{scope}",
    )
    if r["show_labels"]:
        label_mode = st.sidebar.radio(
            "Label scale",
            options=["screen", "metric"],
            index=0 if r.get("label_scale_mode", "screen") == "screen" else 1,
            format_func=lambda m: (
                "Screen-constant (same px at any zoom)"
                if m == "screen"
                else "Metric (true-to-scale, scales with zoom)"
            ),
            key=f"label_scale_mode__{scope}",
        )
        r["label_scale_mode"] = label_mode  # type: ignore[typeddict-item]
        if label_mode == "screen":
            r["label_size_px"] = st.sidebar.slider(
                "Label size (px)",
                min_value=8, max_value=24, value=int(r["label_size_px"]), step=1,
                key=f"label_size_px__{scope}",
            )
        else:
            r["label_size_m"] = st.sidebar.number_input(
                "Label height (m)",
                min_value=0.5, max_value=500.0,
                value=float(r.get("label_size_m", 5.0)),
                step=0.5,
                key=f"label_size_m__{scope}",
            )


def _sidebar_basemap_picker(scope: str) -> str:
    st.sidebar.header("View")
    names = list(BASEMAPS.keys())
    return st.sidebar.selectbox(
        "Initial basemap",
        options=names,
        index=names.index(DEFAULT_BASEMAP),
        key=f"basemap__{scope}",
    )


def _sidebar_save_section(cfg: Config, preset: str, source_name: str, scope: str) -> None:
    """Download the current preset as JSON. The browser's save dialog lets
    the user pick the folder — there's no filesystem path picker in
    Streamlit, and writing to the server-side `config/` folder is
    ephemeral on streamlit.io, so download is the only portable flow."""
    st.sidebar.header("Actions")

    default_name = preset if preset != NEW_PRESET_LABEL else (
        _clean_preset_name(str(cfg.get("name") or Path(source_name).stem)) or "preset"
    )
    name_input = st.sidebar.text_input(
        "Preset name",
        value=default_name,
        key=f"save_name__{scope}",
        help="Used as the download filename (<name>.json).",
    )
    cleaned = _clean_preset_name(name_input) or "preset"
    preset_json = _serialize_preset_for_download(cfg, cleaned)
    st.sidebar.download_button(
        label="⬇️ Download preset JSON",
        data=preset_json,
        file_name=f"{cleaned}.json",
        mime="application/json",
        key=f"dl_preset__{scope}",
        help=(
            "Save the current preset to your computer — the browser will "
            "prompt for a folder. Reload it later via 'Import preset JSON'."
        ),
    )


def _serialize_preset_for_download(cfg: Config, name: str) -> bytes:
    """Mirror save_config's cleanup (strip ephemeral `_uid`) so the
    downloaded JSON round-trips cleanly through Import."""
    cleaned_cats = [
        {k: v for k, v in cat.items() if not k.startswith("_")}
        for cat in cfg["categories"]
    ]
    payload = {**cfg, "name": name, "categories": cleaned_cats}
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def _clean_preset_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\- ]", "", name or "").strip()
    return cleaned.replace(" ", "_")


def _render_attention_section(
    canon: pd.DataFrame,
    cfg: Config,
    df_raw: pd.DataFrame,
    source_name: str,
    cache: dict,
    scope: str,
) -> None:
    n_needs = int((canon["_status"] == STATUS_NEEDS_GEOCODE).sum())
    problems = canon[canon["_status"] != STATUS_OK]

    ad_col = cfg["columns"].get("address")
    n_filled = 0
    if ad_col and ad_col in df_raw.columns:
        for addr in df_raw[ad_col].astype(str):
            if addr.strip() and lookup(cache, addr) is not None:
                n_filled += 1

    if n_needs == 0 and n_filled == 0 and len(problems) == 0:
        return

    st.subheader("Rows needing attention")

    if n_needs > 0:
        if st.button(
            f"📍 Geocode {n_needs} missing address(es)",
            key=f"geocode_main__{scope}",
        ):
            addresses = (
                canon.loc[canon["_status"] == STATUS_NEEDS_GEOCODE, "_address"]
                .astype(str)
                .tolist()
            )
            progress = st.progress(0.0, text="Starting…")

            def on_progress(done: int, total: int, addr: str) -> None:
                snippet = addr if len(addr) <= 60 else addr[:57] + "…"
                progress.progress(done / total, text=f"{done}/{total}: {snippet}")

            hits = geocode_missing(addresses, cache, on_progress=on_progress)
            save_cache(cache)
            progress.empty()
            st.success(
                f"Geocoded {hits} of {len(addresses)}. "
                f"Cached results reused on next run."
            )
            st.rerun()

    if n_filled > 0:
        enriched = enriched_csv_bytes(df_raw, cfg, cache)
        st.download_button(
            label=f"⬇️ Download enriched CSV ({n_filled} filled)",
            data=enriched,
            file_name=Path(source_name).stem + "_geocoded.csv",
            mime="text/csv",
            key=f"dl_csv_enriched__{scope}",
        )

    if len(problems) > 0:
        with st.expander(f"Problem rows ({len(problems)})", expanded=False):
            st.dataframe(
                problems[
                    ["_id", "_label", "_category", "_status", "_status_reason", "_address"]
                ],
                width="stretch",
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
        key=f"dl_kml__{source_name}",
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
    _colored_metric(c1, "Mapped", n_ok, critical=False)
    _colored_metric(c2, "Needs geocoding", n_geo, critical=n_geo > 0)
    _colored_metric(c3, "Errors", n_err, critical=n_err > 0)


def _colored_metric(col, label: str, value: int, critical: bool) -> None:
    color = "#d62728" if critical else "inherit"
    col.markdown(
        f'<div style="font-size:0.85rem;color:rgb(128,128,128);margin-bottom:0.15rem">'
        f"{label}</div>"
        f'<div style="font-size:2.25rem;color:{color};line-height:1.1">{value}</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
