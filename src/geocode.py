"""Nominatim-backed geocoding with an on-disk JSON cache.

Each entry in `cache/geocode.json` maps a normalized address key to
`{"lat", "lon", "display_name"}`. Failures are not cached, so the user
can simply click "Geocode" again to retry problematic rows.

Rate-limited to 1 request/second per Nominatim's ToS.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from config_io import Config
from data import STATUS_NEEDS_GEOCODE, STATUS_OK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "cache" / "geocode.json"

USER_AGENT = "list2gis"


def _normalize(address: str) -> str:
    return " ".join(address.strip().split()).casefold()


def load_cache(path: Path = CACHE_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: dict[str, dict], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def lookup(cache: dict[str, dict], address: str) -> tuple[float, float] | None:
    if not address:
        return None
    entry = cache.get(_normalize(address))
    if entry is None:
        return None
    lat, lon = entry.get("lat"), entry.get("lon")
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def geocode_missing(
    addresses: Iterable[str],
    cache: dict[str, dict],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Geocode addresses not yet in `cache` (mutated in place).

    Returns the number of successful lookups. Failures are silently dropped
    from the cache so a subsequent run can retry them.
    """
    to_lookup: list[tuple[str, str]] = []
    seen: set[str] = set()
    for addr in addresses:
        key = _normalize(addr)
        if not key or key in seen or key in cache:
            continue
        seen.add(key)
        to_lookup.append((key, addr))

    if not to_lookup:
        return 0

    geolocator = Nominatim(user_agent=USER_AGENT)
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

    total = len(to_lookup)
    hits = 0
    for i, (key, addr) in enumerate(to_lookup):
        try:
            loc = geocode(addr, timeout=10)
        except Exception:
            loc = None
        if loc is not None:
            cache[key] = {
                "lat": float(loc.latitude),
                "lon": float(loc.longitude),
                "display_name": str(loc.address),
            }
            hits += 1
        if on_progress is not None:
            on_progress(i + 1, total, addr)
    return hits


def apply_geocode_cache(canon: pd.DataFrame, cache: dict[str, dict]) -> int:
    """Fill coords on `needs_geocode` rows from `cache` (mutated in place).

    Returns the number of rows promoted from `needs_geocode` → `ok`.
    """
    needs = canon["_status"] == STATUS_NEEDS_GEOCODE
    promoted = 0
    for idx in canon[needs].index:
        coord = lookup(cache, str(canon.at[idx, "_address"]))
        if coord is None:
            continue
        canon.at[idx, "_lat"] = coord[0]
        canon.at[idx, "_lon"] = coord[1]
        canon.at[idx, "_status"] = STATUS_OK
        canon.at[idx, "_status_reason"] = ""
        promoted += 1
    return promoted


def enriched_csv_bytes(df: pd.DataFrame, cfg: Config, cache: dict[str, dict]) -> bytes:
    """Return the original CSV with cached coords written back.

    Prefers the mapped `latlong` column when empty; otherwise fills mapped
    `lat`+`lon`; if none of those are mapped, appends a new `latlong`
    column so the output is self-sufficient on re-import.
    """
    cols = cfg["columns"]
    ad_col = cols.get("address")
    if not ad_col or ad_col not in df.columns:
        return _to_csv_bytes(df, cfg)

    out = df.copy()
    ll_col = cols.get("latlong")
    la_col = cols.get("lat")
    lo_col = cols.get("lon")
    has_ll = bool(ll_col) and ll_col in out.columns
    has_sep = (
        bool(la_col)
        and bool(lo_col)
        and la_col in out.columns
        and lo_col in out.columns
    )

    if not has_ll and not has_sep:
        if "latlong" not in out.columns:
            out["latlong"] = ""
        ll_col = "latlong"
        has_ll = True

    for idx in out.index:
        addr = str(out.at[idx, ad_col]).strip()
        if not addr:
            continue
        coord = lookup(cache, addr)
        if coord is None:
            continue
        lat_v, lon_v = coord
        if has_ll and not str(out.at[idx, ll_col]).strip():
            out.at[idx, ll_col] = f"{lat_v:.7f},{lon_v:.7f}"
        elif has_sep:
            if not str(out.at[idx, la_col]).strip():
                out.at[idx, la_col] = f"{lat_v:.7f}"
            if not str(out.at[idx, lo_col]).strip():
                out.at[idx, lo_col] = f"{lon_v:.7f}"
    return _to_csv_bytes(out, cfg)


def _to_csv_bytes(df: pd.DataFrame, cfg: Config) -> bytes:
    opts = cfg["csv_options"]
    csv_str = df.to_csv(sep=opts["delimiter"], index=False)
    return csv_str.encode(opts.get("encoding", "utf-8"), errors="replace")
