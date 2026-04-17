"""CSV loading and canonical DataFrame construction.

Reads a CSV with user-provided (or auto-detected) options, then applies
a Config (see config_io) to produce a canonical DataFrame with the
columns the rest of the app depends on: _id, _label, _lat, _lon,
_address, _category, _status, _status_reason, _popup_html. The
underscore prefix avoids clashing with user column names.
"""
from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import IO, Literal

import pandas as pd

from config_io import Config, CsvOptions

Status = Literal["ok", "needs_geocode", "error"]
STATUS_OK: Status = "ok"
STATUS_NEEDS_GEOCODE: Status = "needs_geocode"
STATUS_ERROR: Status = "error"


def detect_csv_options(source: str | Path | IO[bytes]) -> CsvOptions:
    """Sniff delimiter and encoding from the first 4KB."""
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()[:4096]
    else:
        source.seek(0)
        raw = source.read(4096)
        source.seek(0)

    encoding = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "cp1252"
        text = raw.decode("cp1252", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text, delimiters=";,\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    return {"delimiter": delimiter, "encoding": encoding}


def read_csv(
    source: str | Path | IO[bytes],
    delimiter: str,
    encoding: str,
) -> pd.DataFrame:
    """Read the raw CSV as strings (so we don't mangle numeric-looking IDs)."""
    if not isinstance(source, (str, Path)):
        source.seek(0)
    df = pd.read_csv(source, sep=delimiter, encoding=encoding, dtype=str)
    return df.fillna("")


def build_canonical(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Return a copy of `df` with canonical columns appended."""
    cols = config["columns"]
    out = df.copy()

    out["_id"] = _col_or_index(df, cols.get("id"))
    out["_label"] = _col_or_blank(df, cols.get("label"))
    out["_address"] = _col_or_blank(df, cols.get("address"))
    out["_category"] = _col_or_blank(df, cols.get("category"))

    lat, lon, status, reason = _resolve_coords(df, cols)
    out["_lat"] = lat
    out["_lon"] = lon
    out["_status"] = status
    out["_status_reason"] = reason

    out["_popup_html"] = _build_popups(df, config["hover_columns"])
    return out


def _col_or_index(df: pd.DataFrame, colname: str | None) -> pd.Series:
    if colname and colname in df.columns:
        return df[colname].astype(str)
    return pd.Series([str(i) for i in range(len(df))], index=df.index)


def _col_or_blank(df: pd.DataFrame, colname: str | None) -> pd.Series:
    if colname and colname in df.columns:
        return df[colname].astype(str)
    return pd.Series([""] * len(df), index=df.index)


def _resolve_coords(
    df: pd.DataFrame, cols: dict
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    idx = df.index
    n = len(df)
    lat = pd.Series([float("nan")] * n, index=idx, dtype="float64")
    lon = pd.Series([float("nan")] * n, index=idx, dtype="float64")
    status = pd.Series([STATUS_ERROR] * n, index=idx, dtype="object")
    reason = pd.Series([""] * n, index=idx, dtype="object")
    resolved = pd.Series([False] * n, index=idx)

    ll_col = cols.get("latlong")
    if ll_col and ll_col in df.columns:
        parts = df[ll_col].astype(str).str.split(",", n=1, expand=True)
        if parts.shape[1] == 2:
            la = pd.to_numeric(parts[0].str.strip(), errors="coerce")
            lo = pd.to_numeric(parts[1].str.strip(), errors="coerce")
            valid = la.between(-90, 90) & lo.between(-180, 180)
            lat = lat.where(~valid, la)
            lon = lon.where(~valid, lo)
            resolved = resolved | valid

    la_col, lo_col = cols.get("lat"), cols.get("lon")
    if la_col and lo_col and la_col in df.columns and lo_col in df.columns:
        la = pd.to_numeric(df[la_col], errors="coerce")
        lo = pd.to_numeric(df[lo_col], errors="coerce")
        # Reject out-of-range values (catches Excel's German-locale decimal corruption).
        valid = (~resolved) & la.between(-90, 90) & lo.between(-180, 180)
        lat = lat.where(~valid, la)
        lon = lon.where(~valid, lo)
        resolved = resolved | valid

    status = status.where(~resolved, STATUS_OK)

    ad_col = cols.get("address")
    if ad_col and ad_col in df.columns:
        has_addr = df[ad_col].astype(str).str.strip() != ""
        needs_geo = (~resolved) & has_addr
        status = status.where(~needs_geo, STATUS_NEEDS_GEOCODE)
        reason = reason.where(~needs_geo, "coordinates missing; needs geocoding")

    missing = status == STATUS_ERROR
    reason = reason.where(~missing, "no usable coordinates or address")
    return lat, lon, status, reason


def _build_popups(df: pd.DataFrame, hover_columns: list[str]) -> pd.Series:
    valid = [c for c in hover_columns if c in df.columns]
    if not valid:
        return pd.Series([""] * len(df), index=df.index)
    rows: list[str] = []
    for _, row in df.iterrows():
        lines = []
        for c in valid:
            v = str(row[c]).strip()
            if v:
                lines.append(f"<b>{html.escape(c)}</b>: {html.escape(v)}")
        rows.append("<br>".join(lines))
    return pd.Series(rows, index=df.index)


def load_and_canonicalize(
    source: str | Path | IO[bytes], config: Config
) -> pd.DataFrame:
    """Convenience wrapper: read CSV with config's csv_options and canonicalize."""
    opts = config["csv_options"]
    raw = read_csv(source, delimiter=opts["delimiter"], encoding=opts["encoding"])
    return build_canonical(raw, config)
