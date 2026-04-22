"""Polygon geometry for metric-scaled markers.

Metric mode draws the category symbol as a filled polygon whose size is set
in meters (radius of the bounding circle). Most FA names map to a clean
geometric shape; irregular glyphs like `home` or `flag` fall back to a circle.
"""
from __future__ import annotations

import math

# Curated icon vocabulary exposed in the UI dropdown. These are FA names that
# render (more-or-less) in CSS and all have a metric-mode shape mapping below.
ICON_NAMES: tuple[str, ...] = (
    "home", "star", "circle", "square", "flag", "info", "check",
    "exclamation", "dot", "diamond", "triangle", "cross", "times",
    "pentagon", "hexagon", "plus",
)


# FA icon name → base shape. Unlisted names fall back to "circle".
ICON_TO_SHAPE: dict[str, str] = {
    "circle": "circle",
    "dot": "circle",
    "info": "circle",
    "square": "square",
    "star": "star",
    "triangle": "triangle",
    "diamond": "diamond",
    "plus": "plus",
    "check": "plus",
    "cross": "cross",
    "times": "cross",
    "pentagon": "pentagon",
    "hexagon": "hexagon",
}


def shape_for_icon(icon_name: str) -> str:
    return ICON_TO_SHAPE.get(icon_name, "circle")


def _unit_points(shape: str) -> list[tuple[float, float]]:
    """(dx_east, dy_north) offsets, bounding radius ≈ 1."""
    if shape == "square":
        s = 1.0 / math.sqrt(2.0)
        return [(-s, -s), (s, -s), (s, s), (-s, s)]
    if shape == "triangle":
        return [
            (math.cos(math.pi / 2 - 2 * math.pi * i / 3),
             math.sin(math.pi / 2 - 2 * math.pi * i / 3))
            for i in range(3)
        ]
    if shape == "diamond":
        return [(0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0)]
    if shape == "pentagon":
        return [
            (math.cos(math.pi / 2 - 2 * math.pi * i / 5),
             math.sin(math.pi / 2 - 2 * math.pi * i / 5))
            for i in range(5)
        ]
    if shape == "hexagon":
        return [
            (math.cos(2 * math.pi * i / 6), math.sin(2 * math.pi * i / 6))
            for i in range(6)
        ]
    if shape == "star":
        inner = 0.382
        pts: list[tuple[float, float]] = []
        for i in range(10):
            r = 1.0 if i % 2 == 0 else inner
            theta = math.pi / 2 - 2 * math.pi * i / 10
            pts.append((r * math.cos(theta), r * math.sin(theta)))
        return pts
    if shape == "plus":
        t = 0.3  # arm half-thickness as a fraction of the arm length
        return [
            (-t, 1), (t, 1), (t, t), (1, t), (1, -t),
            (t, -t), (t, -1), (-t, -1), (-t, -t), (-1, -t),
            (-1, t), (-t, t),
        ]
    if shape == "cross":
        t = 0.3
        plus = [
            (-t, 1), (t, 1), (t, t), (1, t), (1, -t),
            (t, -t), (t, -1), (-t, -1), (-t, -t), (-1, -t),
            (-1, t), (-t, t),
        ]
        c = s = 1.0 / math.sqrt(2.0)
        return [(x * c - y * s, x * s + y * c) for x, y in plus]
    # Fallback: 32-point circle
    n = 32
    return [
        (math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _rotate_cw(
    points: list[tuple[float, float]], deg: float
) -> list[tuple[float, float]]:
    """Rotate (dx_east, dy_north) unit points clockwise (compass sense)."""
    if not deg:
        return points
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return [(x * c + y * s, -x * s + y * c) for x, y in points]


def shape_ring_latlon(
    lat: float, lon: float, radius_m: float, shape: str, rotation_deg: float = 0.0
) -> list[tuple[float, float]]:
    """Closed ring of (lat, lon) pairs — Folium-friendly."""
    pts = _rotate_cw(_unit_points(shape), rotation_deg)
    lat_deg_per_m = 1.0 / 111_320.0
    lon_deg_per_m = 1.0 / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    dlat = radius_m * lat_deg_per_m
    dlon = radius_m * lon_deg_per_m
    ring = [(lat + dy * dlat, lon + dx * dlon) for dx, dy in pts]
    ring.append(ring[0])
    return ring


def shape_ring_lonlat(
    lat: float, lon: float, radius_m: float, shape: str, rotation_deg: float = 0.0
) -> list[tuple[float, float]]:
    """Closed ring of (lon, lat) pairs — KML-friendly."""
    return [
        (p_lon, p_lat)
        for p_lat, p_lon in shape_ring_latlon(lat, lon, radius_m, shape, rotation_deg)
    ]
