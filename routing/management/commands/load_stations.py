import csv
import json
import time
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routing.models import FuelStation
from routing.services.geocoding import US_STATES, lookup_city, nominatim_search, normalize_city
from routing.services.stations import reset_station_index


def city_key(station):
    return f"{normalize_city(station['city'])}|{station['state']}"


class Command(BaseCommand):
    help = "Import the fuel prices CSV and geocode each station to its city."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_PRICES_CSV))
        parser.add_argument("--geocode-missing", action="store_true",
                            help="Look up unknown cities with Nominatim (1 request per second).")

    def handle(self, *args, **options):
        cache_file = Path(settings.GEOCODE_CACHE_JSON)
        saved_coords = json.loads(cache_file.read_text()) if cache_file.exists() else {}

        stations, skipped = self.read_csv(options["csv"])

        found, missing = [], []
        for station in stations.values():
            coords = lookup_city(station["city"], station["state"]) or saved_coords.get(city_key(station))
            if coords:
                station["latitude"], station["longitude"] = coords
                found.append(station)
            else:
                missing.append(station)

        if missing and options["geocode_missing"]:
            self.lookup_missing(missing, saved_coords)
            cache_file.write_text(json.dumps(saved_coords, indent=1, sort_keys=True))
            still_missing = []
            for station in missing:
                coords = saved_coords.get(city_key(station))
                if coords:
                    station["latitude"], station["longitude"] = coords
                    found.append(station)
                else:
                    still_missing.append(station)
            missing = still_missing

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create([FuelStation(**s) for s in found], batch_size=1000)
        reset_station_index()

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(found)} stations ({skipped} non-US rows skipped, "
            f"{len(missing)} stations without coordinates)."
        ))
        for station in missing[:20]:
            self.stdout.write(f"  unresolved: {station['name']} - {station['city']}, {station['state']}")

    def read_csv(self, path):
        stations = {}
        skipped = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                state = row["State"].strip().upper()
                if state not in US_STATES:
                    skipped += 1
                    continue

                opis_id = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"].strip())
                current = stations.get(opis_id)
                if current and current["retail_price"] <= price:
                    continue

                stations[opis_id] = {
                    "opis_id": opis_id,
                    "name": row["Truckstop Name"].strip(),
                    "address": row["Address"].strip(),
                    "city": row["City"].strip(),
                    "state": state,
                    "rack_id": int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                    "retail_price": price,
                }
        return stations, skipped

    def lookup_missing(self, missing, saved_coords):
        cities = {city_key(s): s for s in missing}
        self.stdout.write(f"Geocoding {len(cities)} cities via Nominatim...")
        for key, station in cities.items():
            try:
                result = nominatim_search(f"{station['city']}, {station['state']}, USA")
            except Exception as exc:
                self.stderr.write(f"  {key}: {exc}")
                result = None
            saved_coords[key] = [result[0], result[1]] if result else None
            time.sleep(1.1)
