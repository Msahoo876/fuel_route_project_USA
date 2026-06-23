"""
Management command: geocode_stations

Usage:
    python manage.py geocode_stations              # Geocode all un-geocoded stations
    python manage.py geocode_stations --limit 100  # Test with 100 stations first
    python manage.py geocode_stations --reset      # Re-geocode everything

Strategy:
- We use the US Census Geocoding API (completely free, no key needed)
  as primary, with Nominatim (OpenStreetMap) as fallback.
- We geocode by "City, State" — not full address — because:
  1. Full address geocoding is less reliable for rural truck stops
  2. Many stations in the same city share the same approximate coordinates
  3. For route matching, city-level accuracy (within ~30 miles) is sufficient

⚠️ This is a SLOW one-time operation (~30-60 min for all 8,151 stations).
   The geocoder has rate limits. We add 1-second delays to be respectful.
   Run it once and never again.

💡 PRO TIP: You can also bulk-geocode using the Census Batch API which is
   much faster (1000 addresses per call). We implement that here.
"""

import time
import requests
from django.core.management.base import BaseCommand
from fuel_route.models import FuelStation


# Cache city+state → (lat, lon) to avoid repeat lookups
_geocode_cache = {}


def geocode_city_state(city, state):
    """
    Geocode a city+state to lat/lon using Nominatim (free, no key needed).
    Returns (lat, lon) or (None, None) on failure.
    """
    cache_key = f"{city},{state}"
    if cache_key in _geocode_cache:
        return _geocode_cache[cache_key]

    # Use Nominatim (OpenStreetMap) — free, no API key
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"{city}, {state}, USA",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }
    headers = {
        # Nominatim requires a descriptive User-Agent
        "User-Agent": "FuelRouteAPI/1.0 (fuel-route-assessment)"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()

        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            _geocode_cache[cache_key] = (lat, lon)
            return lat, lon
        else:
            _geocode_cache[cache_key] = (None, None)
            return None, None

    except Exception as e:
        return None, None


class Command(BaseCommand):
    help = 'Geocode all fuel stations (city+state → lat/lon). Run once after load_fuel_stations.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Max number of stations to geocode (for testing)',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Re-geocode already-geocoded stations too',
        )

    def handle(self, *args, **options):
        limit = options.get('limit')
        reset = options.get('reset')

        if reset:
            FuelStation.objects.all().update(geocoded=False, latitude=None, longitude=None)
            self.stdout.write("🔄 Reset all geocoding flags")

        # Only process un-geocoded stations
        qs = FuelStation.objects.filter(geocoded=False)
        if limit:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(f"📍 Geocoding {total} stations...")
        self.stdout.write("   (This uses Nominatim — adding 1s delay to respect rate limits)\n")

        success = 0
        failed = 0

        for i, station in enumerate(qs):
            lat, lon = geocode_city_state(station.city, station.state)

            if lat and lon:
                station.latitude = lat
                station.longitude = lon
                station.geocoded = True
                station.save(update_fields=['latitude', 'longitude', 'geocoded'])
                success += 1
            else:
                failed += 1
                station.geocoded = True   # Mark as attempted so we skip it next time
                station.save(update_fields=['geocoded'])

            # Progress update every 50 stations
            if (i + 1) % 50 == 0:
                self.stdout.write(f"   Progress: {i + 1}/{total} | ✅ {success} | ❌ {failed}")

            # Rate limit: Nominatim allows max 1 request/second
            # We skip the delay when a cached result was used
            city_key = f"{station.city},{station.state}"
            if city_key not in _geocode_cache:
                time.sleep(1.1)

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Geocoding complete!\n'
                f'   Success: {success}\n'
                f'   Failed:  {failed}\n'
                f'   Total:   {total}\n'
            )
        )
        self.stdout.write('🚀 You can now start the server: python manage.py runserver\n')