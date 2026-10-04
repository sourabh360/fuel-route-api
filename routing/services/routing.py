import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    pass


def fetch_route(start, finish):
    url = f"{settings.OSRM_URL}/route/v1/driving/{start.lon},{start.lat};{finish.lon},{finish.lat}"
    try:
        response = requests.get(
            url,
            params={"overview": "full", "geometries": "geojson"},
            headers={"User-Agent": settings.HTTP_USER_AGENT},
            timeout=settings.EXTERNAL_API_TIMEOUT,
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(data.get("message") or "No drivable route found between these locations.")

    route = data["routes"][0]
    return {
        "coordinates": [(lat, lon) for lon, lat in route["geometry"]["coordinates"]],
        "distance_miles": route["distance"] / METERS_PER_MILE,
        "duration_hours": route["duration"] / 3600,
    }
