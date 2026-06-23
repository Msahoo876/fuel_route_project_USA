"""
routing.py

Handles all interaction with the OpenRouteService (ORS) API.

We make exactly 3 calls per user request (within assessment's acceptable range):
  - 2 parallel geocoding calls  (run simultaneously via ThreadPoolExecutor)
  - 1 directions call           (uses geocoded coordinates)

Running geocoding in parallel means the total wall-clock time for
geocoding is just the slower of the two calls, not the sum of both.

ORS Directions API returns:
  - Full route geometry (list of [lon, lat] coordinates)
  - Total distance in meters
  - Total duration in seconds
"""

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.conf import settings


ORS_BASE_URL = "https://api.openrouteservice.org"


def geocode_location(location_string):
    """
    Convert a text location like "New York, NY" to (lat, lon).
    Uses ORS Geocoding API.

    Returns: (latitude, longitude, label) tuple or raises ValueError
    """
    url = f"{ORS_BASE_URL}/geocode/search"
    params = {
        "api_key": settings.ORS_API_KEY,
        "text": location_string,
        "boundary.country": "US",   # Restrict to USA
        "size": 1,                   # We only need the best match
    }

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    features = data.get("features", [])
    if not features:
        raise ValueError(f"Could not geocode location: '{location_string}'")

    # ORS returns coordinates as [longitude, latitude]
    coords = features[0]["geometry"]["coordinates"]
    lon, lat = coords[0], coords[1]
    label = features[0]["properties"].get("label", location_string)

    return lat, lon, label


def get_route(start_location, finish_location):
    """
    Get the full driving route between two US locations.

    Makes 3 API calls total (within assessment's acceptable range):
      - 2 geocoding calls run IN PARALLEL (so wall-clock cost = max of the two, not sum)
      - 1 directions call

    Args:
        start_location: string like "New York, NY"
        finish_location: string like "Los Angeles, CA"

    Returns dict:
        {
            "start_coords": (lat, lon),
            "finish_coords": (lat, lon),
            "start_label": "geocoded name",
            "finish_label": "geocoded name",
            "waypoints": [[lon, lat], ...],   # Full route geometry (for accurate snapping)
            "distance_meters": 4489000,
            "distance_miles": 2789.5,
            "duration_seconds": 143200,
        }
    """
    # Step 1: Geocode both locations IN PARALLEL
    # Using ThreadPoolExecutor so both HTTP calls happen simultaneously.
    # On a 2-second per-geocode latency, parallel = ~2s total vs. ~4s serial.
    start_result = None
    finish_result = None
    start_exc = None
    finish_exc = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(geocode_location, start_location): "start",
            executor.submit(geocode_location, finish_location): "finish",
        }
        for future in as_completed(futures):
            label = futures[future]
            try:
                result = future.result()
                if label == "start":
                    start_result = result
                else:
                    finish_result = result
            except Exception as exc:
                if label == "start":
                    start_exc = exc
                else:
                    finish_exc = exc

    # Raise geocoding errors now (outside executor context)
    if start_exc:
        raise start_exc
    if finish_exc:
        raise finish_exc

    start_lat, start_lon, start_label = start_result
    finish_lat, finish_lon, finish_label = finish_result

    # Step 2: Get driving directions (this gives us the full route geometry)
    # geometry_simplify=False: keep all waypoints for accurate station snapping.
    # We sample the waypoints in views.py ONLY for the API response payload.
    url = f"{ORS_BASE_URL}/v2/directions/driving-car/geojson"
    headers = {
        "Authorization": settings.ORS_API_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "coordinates": [
            [start_lon, start_lat],     # ORS uses [lon, lat] order
            [finish_lon, finish_lat],
        ],
        "geometry_simplify": False,     # Keep full geometry for accurate snapping
        "instructions": False,          # Skip turn-by-turn — we don't need it
    }

    resp = requests.post(url, json=body, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    # Extract route data from GeoJSON response
    feature = data["features"][0]
    geometry = feature["geometry"]["coordinates"]   # List of [lon, lat] points
    summary = feature["properties"]["summary"]

    distance_meters = summary["distance"]
    distance_miles = distance_meters * 0.000621371  # Convert m → miles

    return {
        "start_coords": (start_lat, start_lon),
        "finish_coords": (finish_lat, finish_lon),
        "start_label": start_label,
        "finish_label": finish_label,
        "waypoints": geometry,            # [[lon, lat], [lon, lat], ...]
        "distance_meters": distance_meters,
        "distance_miles": round(distance_miles, 2),
        "duration_seconds": summary["duration"],
    }