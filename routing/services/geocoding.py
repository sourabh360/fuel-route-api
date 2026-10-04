import csv
import re
from dataclasses import dataclass
from functools import lru_cache

import requests
from django.conf import settings

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

PREFIXES = [
    (r"^st\.? ", "saint "),
    (r"^ste\.? ", "sainte "),
    (r"^ft\.? ", "fort "),
    (r"^mt\.? ", "mount "),
]

LAT_LON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodingError(Exception):
    pass


@dataclass(frozen=True)
class Location:
    label: str
    lat: float
    lon: float


def normalize_city(city):
    city = re.sub(r"\s+", " ", city.strip().lower())
    for pattern, replacement in PREFIXES:
        city = re.sub(pattern, replacement, city)
    city = re.sub(r"[^a-z ]", "", city)
    return city.replace(" ", "")


@lru_cache(maxsize=1)
def city_index():
    cities = {}
    with open(settings.US_CITIES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (normalize_city(row["CITY"]), row["STATE_CODE"].upper())
            cities.setdefault(key, (float(row["LATITUDE"]), float(row["LONGITUDE"])))
    return cities


def lookup_city(city, state):
    return city_index().get((normalize_city(city), state.strip().upper()))


def inside_usa(lat, lon):
    mainland = 24.0 <= lat <= 49.6 and -125.0 <= lon <= -66.5
    alaska = 51.0 <= lat <= 71.6 and -180.0 <= lon <= -129.0
    hawaii = 18.5 <= lat <= 22.5 and -161.0 <= lon <= -154.0
    return mainland or alaska or hawaii


def nominatim_search(query):
    response = requests.get(
        settings.NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
        headers={"User-Agent": settings.HTTP_USER_AGENT},
        timeout=settings.EXTERNAL_API_TIMEOUT,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    first = results[0]
    return float(first["lat"]), float(first["lon"]), first.get("display_name", query)


def geocode(query):
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Location is empty.")

    match = LAT_LON.match(query)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if not inside_usa(lat, lon):
            raise GeocodingError(f"'{query}' is not inside the USA.")
        return Location(query, lat, lon)

    parts = [p.strip() for p in query.split(",")]
    if len(parts) >= 2 and parts[-1].upper() in US_STATES:
        city = parts[-2] if len(parts) > 2 else parts[0]
        coords = lookup_city(city, parts[-1])
        if coords:
            return Location(query, *coords)

    try:
        found = nominatim_search(query)
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding service unavailable: {exc}") from exc

    if not found:
        raise GeocodingError(f"Could not find '{query}' in the USA.")

    lat, lon, label = found
    if not inside_usa(lat, lon):
        raise GeocodingError(f"'{query}' is not inside the USA.")
    return Location(label, lat, lon)
