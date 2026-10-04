from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import FuelStation
from .services import geocoding
from .services.optimizer import Candidate, NoFuelPlanError, plan_fuel_stops
from .services.stations import reset_station_index

def c(mile, price, sid=None):
    return Candidate(sid if sid is not None else int(mile), mile, 0.0, price)

class OptimizerTests(TestCase):
    def plan(self, cands, total, **kw):
        kw.setdefault("tank_range", 500)
        kw.setdefault("mpg", 10)
        kw.setdefault("start_window_miles", 50)
        return plan_fuel_stops(cands, total, **kw)

    def test_short_trip_single_fill_at_start(self):
        stops = self.plan([c(0, 3.00), c(100, 2.00)], 80)
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(stops[0].gallons, 8.0)
        self.assertAlmostEqual(stops[0].cost, 24.0)

    def test_buys_only_enough_to_reach_cheaper_station(self):
        stops = self.plan([c(0, 4.00), c(200, 2.00)], 600)
        self.assertEqual([s.candidate.mile for s in stops], [0, 200])
        self.assertAlmostEqual(stops[0].gallons, 20.0)
        self.assertAlmostEqual(stops[1].gallons, 40.0)
        self.assertAlmostEqual(sum(s.cost for s in stops), 80 + 80)

    def test_fills_up_when_next_stations_are_pricier(self):
        stops = self.plan([c(0, 2.00), c(400, 4.00)], 700)
        self.assertAlmostEqual(stops[0].gallons, 50.0)
        self.assertAlmostEqual(stops[1].gallons, 20.0)
        self.assertAlmostEqual(sum(s.gallons for s in stops), 70.0)

    def test_total_gallons_equals_distance_over_mpg(self):
        cands = [c(m, 3 + (m % 7) / 10) for m in range(0, 2000, 37)]
        stops = self.plan(cands, 1999)
        self.assertAlmostEqual(sum(s.gallons for s in stops), 199.9)
        for s in stops:
            self.assertLessEqual(s.gallons, 50 + 5 + 1e-9)

    def test_stop_penalty_reduces_tiny_stops(self):
        cands = [c(0, 3.00), c(100, 2.99), c(200, 2.98), c(300, 2.97), c(400, 2.96)]
        cheapest = self.plan(cands, 450, stop_penalty=0)
        practical = self.plan(cands, 450, stop_penalty=5)
        self.assertGreater(len(cheapest), len(practical))
        self.assertLessEqual(sum(s.cost for s in cheapest), sum(s.cost for s in practical))

    def test_gap_larger_than_range_raises(self):
        with self.assertRaises(NoFuelPlanError):
            self.plan([c(0, 3.0), c(600, 3.0)], 900)

    def test_no_station_near_start_raises(self):
        with self.assertRaises(NoFuelPlanError):
            self.plan([c(120, 3.0)], 300)

class GeocodingTests(TestCase):
    def test_latlon_literal(self):
        loc = geocoding.geocode("41.88, -87.63")
        self.assertEqual((loc.lat, loc.lon), (41.88, -87.63))

    def test_latlon_outside_usa_rejected(self):
        with self.assertRaises(geocoding.GeocodingError):
            geocoding.geocode("51.5, -0.12")

    @mock.patch("routing.services.geocoding.nominatim_search")
    def test_city_state_resolved_locally(self, nominatim):
        loc = geocoding.geocode("Chicago, IL")
        self.assertAlmostEqual(loc.lat, 41.88, delta=0.2)
        nominatim.assert_not_called()

    def test_normalize_city(self):
        self.assertEqual(geocoding.normalize_city("St. Louis"), geocoding.normalize_city("Saint Louis"))

FAKE_ROUTE = {

    "coordinates": [(30 + i * 0.01, -90.0) for i in range(1501)],
    "distance_miles": 1036.0,
    "duration_hours": 16.0,
}

@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for opis, lat, price in [(1, 30.05, 3.50), (2, 33.0, 2.90), (3, 37.0, 3.20), (4, 40.0, 2.80), (5, 44.0, 3.90)]:
            FuelStation.objects.create(
                opis_id=opis, name=f"Station {opis}", address="I-55", city="Town", state="MS",
                retail_price=Decimal(str(price)), latitude=lat, longitude=-90.02,
            )

    def setUp(self):
        cache.clear()
        reset_station_index()
        self.client = APIClient()

    @mock.patch("routing.services.planner.fetch_route", return_value=FAKE_ROUTE)
    def test_route_plan(self, fetch):
        res = self.client.get("/api/route/", {"start": "30.0,-90.0", "finish": "45.0,-90.0"})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(fetch.call_count, 1)
        self.assertAlmostEqual(body["total_gallons"], 103.6, delta=0.2)
        self.assertGreater(len(body["fuel_stops"]), 1)
        self.assertEqual(body["route"]["geometry"]["type"], "LineString")
        self.assertIn("/api/route/map/", body["map_url"])
        self.assertAlmostEqual(body["total_fuel_cost"], sum(s["cost"] for s in body["fuel_stops"]), delta=0.05)

    @mock.patch("routing.services.planner.fetch_route", return_value=FAKE_ROUTE)
    def test_repeat_request_is_cached(self, fetch):
        params = {"start": "30.0,-90.0", "finish": "45.0,-90.0"}
        self.client.get("/api/route/", params)
        self.client.post("/api/route/", params, format="json")
        self.assertEqual(fetch.call_count, 1)

    @mock.patch("routing.services.planner.fetch_route", return_value=FAKE_ROUTE)
    def test_map_page(self, fetch):
        res = self.client.get("/api/route/map/", {"start": "30.0,-90.0", "finish": "45.0,-90.0"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "leaflet")

    def test_missing_params(self):
        self.assertEqual(self.client.get("/api/route/").status_code, 400)

    def test_non_us_location(self):
        res = self.client.get("/api/route/", {"start": "51.5,-0.12", "finish": "45.0,-90.0"})
        self.assertEqual(res.status_code, 400)
