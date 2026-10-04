# Fuel Route Planner API

A Django 6 / Django REST Framework API that takes a start and finish location in the USA and returns:

- the driving route (GeoJSON, plus an interactive map page),
- the most cost-effective fuel stops along it, given a **500-mile range**,
- the **total fuel cost** at **10 MPG**, using the prices in the provided OPIS CSV.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations       # imports + geocodes the fuel CSV (~2 s, no network)
python manage.py runserver
```

```bash
curl "http://127.0.0.1:8000/api/route/?start=New York, NY&finish=Los Angeles, CA"
```

Open the `map_url` from the response in a browser to see the route and stops.

Run the tests (all external APIs are mocked):

```bash
python manage.py test
```

## API

### `GET /api/route/?start=<location>&finish=<location>`
### `POST /api/route/` with `{"start": "...", "finish": "..."}`

A location can be `"City, ST"`, a full address, or `"lat,lon"`. Locations outside the USA are rejected.

Example response (shortened):

```json
{
  "start":  {"query": "Chicago, IL", "label": "Chicago, IL", "latitude": 41.85, "longitude": -87.65},
  "finish": {"query": "Denver, CO",  "label": "Denver, CO",  "latitude": 39.74, "longitude": -104.98},
  "assumptions": {"max_range_miles": 500, "miles_per_gallon": 10, "station_corridor_miles": 10, "stop_penalty_dollars": 5.0},
  "distance_miles": 1001.3,
  "duration_hours": 14.6,
  "stations_considered": 202,
  "total_gallons": 100.1,
  "total_fuel_cost": 292.01,
  "fuel_stops": [
    {"stop_number": 1, "name": "Gulf", "city": "Bensenville", "state": "IL",
     "price_per_gallon": 3.059, "route_mile": 16.0, "off_route_miles": 2.1,
     "gallons": 20.2, "cost": 61.79, "latitude": 41.95, "longitude": -87.94, "...": "..."}
  ],
  "route": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-87.65, 41.85], "..."]}},
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=Chicago%2C+IL&finish=Denver%2C+CO",
  "response_time_ms": 1450.2
}
```

Errors: `400` for invalid or non-US input, `422` when no feasible fuel plan exists (for example a gap longer than 500 miles), and `502` when the routing provider fails.

### `GET /api/route/map/?start=...&finish=...`

An HTML page (Leaflet + OpenStreetMap tiles) that draws the route, start and finish, and every fuel stop, with a cost summary. It reads from the same cache as the JSON endpoint, so opening it right after an API call makes no extra external requests.

## How it works

```
request ─► geocode start/finish ─► OSRM route (1 call) ─► match stations to route ─► DP optimizer ─► response (cached)
```

1. **Station data (offline, once).** `load_stations` reads the CSV and drops Canadian rows. It keeps the lowest price per OPIS station ID and geocodes each station to its city centroid using the bundled US cities dataset (`data/us_cities.csv`). The 5 cities missing from that dataset were resolved once through Nominatim and are committed in `data/geocode_cache.json`, so loading needs no network.
2. **Geocoding inputs.** `"lat,lon"` and `"City, ST"` resolve locally with no API call. Anything else uses one Nominatim call.
3. **Routing.** One call to the free public [OSRM](http://project-osrm.org/) server, with the full route geometry.
   **External calls per request:** 1 for typical inputs, at most 3 for free-form addresses. Repeat requests are served from cache with 0 calls in about 3 ms.
4. **Station matching.** All ~6,600 stations are held in memory as unit vectors on the sphere. The route is resampled every 0.5 mi into a KD-tree, and one vectorized query finds every station within 10 miles of the route, along with its mile marker.
5. **Optimization.** An exact dynamic program over *(station, fuel level)* on a 1-mile grid (see `routing/services/optimizer.py`). It allows partial fill-ups and finds the minimum-cost plan in a few milliseconds. A configurable **stop penalty** (default $5, env `FUEL_STOP_PENALTY`) counts in the objective but not in the reported cost. It removes impractical "buy 0.2 gal to save a cent" stops. For example, NY→LA goes from 22 stops to 7 for +0.9% fuel cost. Set it to `0` for the strictly cheapest plan.

## Assumptions

- The vehicle starts on reserve and fills up within the first 50 route miles. The fuel used to reach that first station is included in the first purchase, so **total gallons = trip miles ÷ 10**.
- Station location = city centroid. Distance to a station = its closest point on the route. Detours to reach a station are not counted.
- When a station has several prices in the CSV, the lowest is used.
- Prices are the CSV retail prices. Canadian stations are ignored because routes are USA-only.

## Project layout

```
config/                      Django settings & root URLs
routing/
  models.py                  FuelStation
  management/commands/load_stations.py
  services/geocoding.py      lat/lon, local city index, Nominatim fallback
  services/routing.py        OSRM client (single call)
  services/stations.py       in-memory KD-tree corridor matching
  services/optimizer.py      DP fuel-stop optimizer
  services/planner.py        orchestration + caching
  views.py / serializers.py  DRF endpoints
  templates/routing/map.html Leaflet map
  tests.py
data/                        fuel CSV, US cities dataset, geocode cache
```
