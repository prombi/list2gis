"""JSON config I/O for List2GIS datasets.

Configs are named presets stored under `config/<preset>.json`, independent
of the CSV filename — a preset can be applied to any CSV whose header
contains the columns it references. See ARCHITECTURE.md §4 for the schema.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Literal, TypedDict


class CsvOptions(TypedDict):
    delimiter: str
    encoding: str


class Rendering(TypedDict):
    icon_scale_mode: Literal["screen", "metric"]
    icon_size_px: int
    show_labels: bool
    label_size_px: int
    label_scale_mode: Literal["screen", "metric"]
    label_size_m: float


class ColumnMapping(TypedDict):
    id: str | None
    label: str | None
    latlong: str | None
    lat: str | None
    lon: str | None
    address: str | None
    category: str | None


class CategoryStyle(TypedDict):
    value: str
    label: str
    color: str
    icon: str
    size_m: float
    rotation_deg: float


class DefaultStyle(TypedDict):
    color: str
    icon: str
    size_m: float
    rotation_deg: float


class Config(TypedDict):
    name: str
    csv_options: CsvOptions
    columns: ColumnMapping
    hover_columns: list[str]
    categories: list[CategoryStyle]
    default_style: DefaultStyle
    rendering: Rendering


CANONICAL_FIELDS: tuple[str, ...] = (
    "id",
    "label",
    "latlong",
    "lat",
    "lon",
    "address",
    "category",
)

# Canonical-field → header-match patterns. Patterns beginning with "^"
# are anchored regexes; others are case-insensitive substring matches.
# Declaration order matters: `latlong` is tried before `lat`/`lon` so a
# column literally named "latlong" doesn't get captured by the "lat" rule.
_HEADER_PATTERNS: dict[str, tuple[str, ...]] = {
    "id": ("schlüssel", "schluessel", "^id$", "key"),
    "label": ("adresse kurz", "short address", "label", "name", "bezeichnung"),
    "latlong": ("latlong", "lat_long", "lat,long", "coords", "coordinates"),
    "lat": ("^lat$", "latitude", "breite"),
    "lon": ("^lon$", "^long$", "longitude", "länge", "laenge"),
    "address": ("adresse komplett", "full address", "^address$", "^adresse$"),
    "category": ("kategorie", "^category$", "^cat$", "status", "^typ$", "^type$"),
}

DEFAULT_STYLE: DefaultStyle = {
    "color": "#888888",
    "icon": "circle",
    "size_m": 5.0,
    "rotation_deg": 0.0,
}

DEFAULT_RENDERING: Rendering = {
    "icon_scale_mode": "screen",
    "icon_size_px": 28,
    "show_labels": False,
    "label_size_px": 12,
    "label_scale_mode": "screen",
    "label_size_m": 5.0,
}

# Default metric radius for newly added categories (meters). Per-category now.
DEFAULT_CATEGORY_SIZE_M: float = 5.0


def empty_config(name: str) -> Config:
    return {
        "name": name,
        "csv_options": {"delimiter": ";", "encoding": "utf-8"},
        "columns": {f: None for f in CANONICAL_FIELDS},  # type: ignore[typeddict-item]
        "hover_columns": [],
        "categories": [],
        "default_style": dict(DEFAULT_STYLE),  # type: ignore[typeddict-item]
        "rendering": dict(DEFAULT_RENDERING),  # type: ignore[typeddict-item]
    }


def seed_config_from_header(
    name: str,
    header: list[str],
    csv_options: CsvOptions | None = None,
) -> Config:
    """Create a fresh Config with column mappings heuristically guessed from `header`."""
    cfg = empty_config(name)
    if csv_options:
        cfg["csv_options"] = {**cfg["csv_options"], **csv_options}

    used: set[str] = set()
    for field, patterns in _HEADER_PATTERNS.items():
        for pattern in patterns:
            match = _find_header_match(header, pattern, exclude=used)
            if match is not None:
                cfg["columns"][field] = match  # type: ignore[literal-required]
                used.add(match)
                break
    return cfg


def _find_header_match(
    header: list[str], pattern: str, exclude: set[str]
) -> str | None:
    regex = re.compile(pattern, re.IGNORECASE) if pattern.startswith("^") else None
    for col in header:
        if col in exclude:
            continue
        c = col.strip().casefold()
        if regex is not None:
            if regex.match(c):
                return col
        elif pattern.casefold() in c:
            return col
    return None


def list_presets(config_dir: Path) -> list[str]:
    """Return sorted preset names (filenames without .json) under `config_dir`."""
    if not config_dir.exists():
        return []
    return sorted(p.stem for p in config_dir.glob("*.json"))


def preset_path(name: str, config_dir: Path) -> Path:
    return config_dir / f"{name}.json"


def header_matches(cfg: Config, header: Iterable[str]) -> bool:
    """True if every column the config references exists in `header`.

    Used to auto-select a preset for an uploaded CSV when the filename
    doesn't match any preset name directly.
    """
    header_set = set(header)
    for field, val in cfg["columns"].items():
        if val is not None and val not in header_set:
            return False
    for col in cfg.get("hover_columns", []):
        if col not in header_set:
            return False
    return True


def load_config(path: Path) -> Config:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    # Start from an empty config so missing keys get sensible defaults, then
    # overlay what was actually in the file.
    cfg = empty_config(data.get("name", path.stem))
    cfg.update({k: v for k, v in data.items() if k in cfg})  # type: ignore[typeddict-item]
    cfg["columns"] = {**cfg["columns"], **data.get("columns", {})}
    cfg["csv_options"] = {**cfg["csv_options"], **data.get("csv_options", {})}
    cfg["default_style"] = {**cfg["default_style"], **data.get("default_style", {})}
    # Legacy configs stored icon_size_m under rendering; it's now per-category.
    legacy_size_m = float(data.get("rendering", {}).get("icon_size_m", DEFAULT_CATEGORY_SIZE_M))
    cfg["rendering"] = {
        **cfg["rendering"],
        **{k: v for k, v in data.get("rendering", {}).items() if k in cfg["rendering"]},
    }
    cfg["categories"] = [_migrate_category(c, legacy_size_m) for c in data.get("categories", [])]
    return cfg


def _migrate_category(cat: dict, legacy_size_m: float) -> CategoryStyle:
    return {
        "value": str(cat.get("value", "")),
        "label": str(cat.get("label", "")),
        "color": str(cat.get("color", DEFAULT_STYLE["color"])),
        "icon": str(cat.get("icon", DEFAULT_STYLE["icon"])),
        "size_m": float(cat.get("size_m", legacy_size_m)),
        "rotation_deg": float(cat.get("rotation_deg", 0.0)),
    }


def save_config(config: Config, path: Path) -> None:
    # Strip ephemeral UI-only keys (e.g. `_uid` used to key Streamlit widgets).
    cleaned = {
        **config,
        "categories": [
            {k: v for k, v in cat.items() if not k.startswith("_")}
            for cat in config["categories"]
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)
        f.write("\n")


def validate_config(config: Config, header: list[str] | None = None) -> list[str]:
    """Return a list of human-readable error strings; empty list = valid."""
    errors: list[str] = []
    cols = config["columns"]

    has_latlong = bool(cols.get("latlong"))
    has_separate = bool(cols.get("lat")) and bool(cols.get("lon"))
    has_address = bool(cols.get("address"))
    if not (has_latlong or has_separate or has_address):
        errors.append(
            "At least one coordinate source is required: latlong, lat+lon, or address."
        )

    if not cols.get("category"):
        errors.append("A category column is required.")

    if header is not None:
        header_set = set(header)
        for field, val in cols.items():
            if val is not None and val not in header_set:
                errors.append(
                    f"Column '{val}' (mapped to {field}) not found in CSV header."
                )
        for col in config["hover_columns"]:
            if col not in header_set:
                errors.append(f"Hover column '{col}' not found in CSV header.")

    seen_values: set[str] = set()
    for i, cat in enumerate(config["categories"]):
        value = cat.get("value", "")
        if not value:
            errors.append(f"categories[{i}]: 'value' is required.")
        elif value in seen_values:
            errors.append(f"categories[{i}]: duplicate value '{value}'.")
        seen_values.add(value)
        if not _is_hex_color(cat.get("color", "")):
            errors.append(
                f"categories[{i}]: color '{cat.get('color')}' must be a 6-digit hex like #rrggbb."
            )

    if not _is_hex_color(config["default_style"].get("color", "")):
        errors.append(
            f"default_style.color '{config['default_style'].get('color')}' must be a 6-digit hex like #rrggbb."
        )

    return errors


def _is_hex_color(s: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", s))
