from dataclasses import dataclass
from threading import Lock

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS = 3958.8

_index = None
_lock = Lock()


@dataclass
class StationIndex:
    ids: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    price: np.ndarray
    vectors: np.ndarray
    records: dict


def to_vectors(lat, lon):
    lat = np.radians(lat)
    lon = np.radians(lon)
    return np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


def get_station_index():
    global _index
    if _index is not None:
        return _index

    with _lock:
        if _index is None:
            from routing.models import FuelStation

            rows = list(FuelStation.objects.values(
                "id", "opis_id", "name", "address", "city", "state",
                "retail_price", "latitude", "longitude",
            ))
            lat = np.array([r["latitude"] for r in rows], dtype=float)
            lon = np.array([r["longitude"] for r in rows], dtype=float)
            _index = StationIndex(
                ids=np.array([r["id"] for r in rows], dtype=np.int64),
                lat=lat,
                lon=lon,
                price=np.array([float(r["retail_price"]) for r in rows]),
                vectors=to_vectors(lat, lon).reshape(-1, 3),
                records={r["id"]: r for r in rows},
            )
    return _index


def reset_station_index():
    global _index
    _index = None


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS * np.arcsin(np.sqrt(a))


def cumulative_miles(coords):
    if len(coords) < 2:
        return np.zeros(len(coords))
    steps = haversine(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    return np.concatenate([[0.0], np.cumsum(steps)])


def resample(coords, miles, spacing):
    if len(coords) <= 2:
        return coords, miles
    buckets = np.floor(miles / spacing)
    keep = np.concatenate([[True], buckets[1:] != buckets[:-1]])
    keep[-1] = True
    return coords[keep], miles[keep]


def stations_along_route(coords, miles, max_distance, spacing=0.5):
    index = get_station_index()
    if len(index.ids) == 0 or len(coords) == 0:
        return []

    points, point_miles = resample(coords, miles, spacing)
    tree = cKDTree(to_vectors(points[:, 0], points[:, 1]))
    limit = 2 * np.sin(max_distance / (2 * EARTH_RADIUS))
    dist, nearest = tree.query(index.vectors, distance_upper_bound=limit)

    found = np.isfinite(dist)
    off_route = 2 * EARTH_RADIUS * np.arcsin(np.clip(dist[found] / 2, 0, 1))

    matches = list(zip(
        index.ids[found].tolist(),
        point_miles[nearest[found]].tolist(),
        off_route.tolist(),
        index.price[found].tolist(),
    ))
    matches.sort(key=lambda m: (m[1], m[3]))
    return matches
