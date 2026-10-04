import hashlib

import numpy as np
from django.conf import settings
from django.core.cache import cache

from .geocoding import geocode
from .optimizer import Candidate, plan_fuel_stops
from .routing import fetch_route
from .stations import cumulative_miles, get_station_index, resample, stations_along_route


def make_cache_key(*parts):
    raw = "|".join(str(p).strip().lower() for p in parts)
    return "plan:" + hashlib.sha256(raw.encode()).hexdigest()


def stop_details(number, purchase, record):
    return {
        "stop_number": number,
        "opis_id": record["opis_id"],
        "name": record["name"],
        "address": record["address"],
        "city": record["city"],
        "state": record["state"],
        "latitude": record["latitude"],
        "longitude": record["longitude"],
        "price_per_gallon": round(purchase.candidate.price, 3),
        "route_mile": round(purchase.candidate.mile, 1),
        "off_route_miles": round(purchase.candidate.off_route, 1),
        "gallons": round(purchase.gallons, 2),
        "cost": round(purchase.cost, 2),
    }


def build_plan(start_query, finish_query):
    config = settings.FUEL_PLANNER
    key = make_cache_key(start_query, finish_query, config["MAX_RANGE_MILES"], config["MPG"],
                         config["CORRIDOR_MILES"], config["STOP_PENALTY"])
    saved = cache.get(key)
    if saved is not None:
        return saved

    start = geocode(start_query)
    finish = geocode(finish_query)
    route = fetch_route(start, finish)

    coords = np.asarray(route["coordinates"], dtype=float)
    miles = cumulative_miles(coords)
    if miles[-1] > 0:
        miles = miles * (route["distance_miles"] / miles[-1])

    matches = stations_along_route(coords, miles, config["CORRIDOR_MILES"])
    candidates = [Candidate(*m) for m in matches]

    purchases = plan_fuel_stops(
        candidates,
        total_miles=route["distance_miles"],
        tank_range=config["MAX_RANGE_MILES"],
        mpg=config["MPG"],
        start_window_miles=config["START_WINDOW_MILES"],
        stop_penalty=config["STOP_PENALTY"],
    )

    records = get_station_index().records
    stops = [stop_details(n, p, records[p.candidate.station_id]) for n, p in enumerate(purchases, start=1)]
    line, _ = resample(coords, miles, 0.25)

    result = {
        "start": {"query": start_query, "label": start.label, "latitude": start.lat, "longitude": start.lon},
        "finish": {"query": finish_query, "label": finish.label, "latitude": finish.lat, "longitude": finish.lon},
        "assumptions": {
            "max_range_miles": config["MAX_RANGE_MILES"],
            "miles_per_gallon": config["MPG"],
            "station_corridor_miles": config["CORRIDOR_MILES"],
            "stop_penalty_dollars": config["STOP_PENALTY"],
        },
        "distance_miles": round(route["distance_miles"], 1),
        "duration_hours": round(route["duration_hours"], 2),
        "stations_considered": len(candidates),
        "total_gallons": round(sum(p.gallons for p in purchases), 2),
        "total_fuel_cost": round(sum(p.cost for p in purchases), 2),
        "fuel_stops": stops,
        "route": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in line],
            },
            "properties": {"distance_miles": round(route["distance_miles"], 1)},
        },
    }
    cache.set(key, result, config["CACHE_SECONDS"])
    return result
