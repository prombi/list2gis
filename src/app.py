"""List2GIS — Streamlit entry point."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from basemaps import BASEMAPS, DEFAULT_BASEMAP
from config_io import (
    CANONICAL_FIELDS,
    Config,
    config_path_for,
    load_config,
    save_config,
    seed_config_from_header,
    validate_config,
)
from data import (
    STATUS_ERROR,
    STATUS_NEEDS_GEOCODE,
    STATUS_OK,
    build_canonical,
    detect_csv_options,
    read_csv,
)
from export_kml import export_kmz_bytes
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
    basemap = _sidebar_basemap_picker()
    _sidebar_save_button(cfg, config_path)

    errors = validate_config(cfg, header)
    if errors:
        st.error("Config has errors:\n\n" + "\n".join(f"- {e}" for e in errors))
        return

    canon = build_canonical(df, cfg)
    _render_status_metrics(canon)
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


def _sidebar_categories(cfg: Config) -> None:
    st.sidebar.header("Categories")
    cats_df = pd.DataFrame(cfg["categories"])
    if cats_df.empty:
        cats_df = pd.DataFrame(columns=["value", "label", "color", "icon"])
    edited = st.sidebar.data_editor(
        cats_df,
        num_rows="dynamic",
        use_container_width=True,
        key="cats_editor",
        column_config={
            "value": st.column_config.TextColumn(
                "value", help="Must match the category column value in the data"
            ),
            "label": st.column_config.TextColumn("label"),
            "color": st.column_config.TextColumn("color", help="Hex like #d62728"),
            "icon": st.column_config.TextColumn(
                "icon", help="FontAwesome name (e.g. home, star, flag)"
            ),
        },
    )
    cfg["categories"] = edited.fillna("").to_dict("records")


def _sidebar_default_style(cfg: Config) -> None:
    with st.sidebar.expander("Default style (for unlisted categories)"):
        cfg["default_style"]["color"] = st.text_input(
            "Default color",
            value=cfg["default_style"].get("color", "#888888"),
            key="default_color",
        )
        cfg["default_style"]["icon"] = st.text_input(
            "Default icon",
            value=cfg["default_style"].get("icon", "circle"),
            key="default_icon",
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


def _sidebar_export_buttons(canon: pd.DataFrame, cfg: Config, source_name: str) -> None:
    st.sidebar.header("Export")
    n_ok = int((canon["_status"] == STATUS_OK).sum())
    if n_ok == 0:
        st.sidebar.caption("No rows to export yet.")
        return
    kmz_bytes = export_kmz_bytes(canon, cfg)
    st.sidebar.download_button(
        label=f"⬇️ Download KMZ ({n_ok} points)",
        data=kmz_bytes,
        file_name=Path(source_name).stem + ".kmz",
        mime="application/vnd.google-earth.kmz",
        key="dl_kmz",
    )


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
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
