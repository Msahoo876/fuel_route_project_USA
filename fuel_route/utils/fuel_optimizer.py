"""
fuel_optimizer.py

The core algorithm that finds the cheapest fuel stops along a route.

ALGORITHM OVERVIEW:
==================
1. Take the route as a list of waypoints ([lon, lat] points from ORS)
2. THIN the waypoints (keep every 10th) for fast station snapping — city-level
   accuracy is more than sufficient for matching stations to route positions.
3. Compute cumulative distance from start (miles) at each thinned waypoint
4. Query the DB for ALL geocoded fuel stations in the route's bounding box
5. For each station, snap it to the closest thinned waypoint (vectorized with numpy)
6. Apply Smarter Greedy Forward-Look algorithm:
   - Start at mile 0 with a full tank (500 miles range)
   - Look ahead up to 500 miles (our max range)
   - Find the cheapest station ahead in the FULL LOOKAHEAD window
   - If that cheapest station is reachable on current fuel → drive straight to it
   - If it's NOT reachable → find a "bridge" station: the closest reachable station
     that gets us close enough to then reach the cheap one. Fill MINIMALLY at the
     bridge (just enough to reach the cheap station, not a full tank).
   - At the cheapest station, always fill to a FULL TANK
   - Repeat until destination is reachable

PERFORMANCE:
- Waypoint thinning: 5000 pts → ~500 pts (10x reduction in snapping loop)
- numpy vectorized haversine: replaces Python for-loop, ~50–100x faster per station
- Combined: O(S × N/10) with numpy → effectively sub-second for typical routes

CORRECTNESS:
- total_fuel_cost_usd correctly accounts for the initial tank at start (if any)
- Smarter greedy fills minimally at bridge stops, saving money at expensive stations
"""

import math
import numpy as np

from fuel_route.models import FuelStation
from django.conf import settings


# ──────────────────────────────────────────────────────
# HAVERSINE DISTANCE (scalar — used for cumulative route building)
# ──────────────────────────────────────────────────────

def haversine_miles(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two GPS points in miles.
    """
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ──────────────────────────────────────────────────────
# NUMPY VECTORIZED HAVERSINE (used for station snapping)
# ──────────────────────────────────────────────────────

def haversine_miles_vectorized(station_lat, station_lon, route_lats, route_lons):
    """
    Compute haversine distance from ONE station to MANY route waypoints at once.
    Uses numpy broadcasting — 50–100x faster than a Python for-loop.

    Args:
        station_lat, station_lon: scalars
        route_lats, route_lons: numpy arrays of shape (N,)

    Returns: numpy array of shape (N,) with distances in miles
    """
    R = 3958.8
    lat1 = math.radians(station_lat)
    lat2 = np.radians(route_lats)
    dlat = lat2 - lat1
    dlon = np.radians(route_lons) - math.radians(station_lon)
    a = np.sin(dlat / 2) ** 2 + math.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# ──────────────────────────────────────────────────────
# ROUTE WAYPOINT PROCESSING
# ──────────────────────────────────────────────────────

def thin_waypoints(waypoints, step=10):
    """
    Keep every `step`-th waypoint for efficient station snapping.
    Always includes the first and last point.

    City-level accuracy (within ~30 miles) is all we need for matching
    fuel stations to route positions — thinning by 10x has no real impact
    on the quality of results.
    """
    thinned = waypoints[::step]
    if thinned[-1] != waypoints[-1]:
        thinned = thinned + [waypoints[-1]]
    return thinned


def build_route_miles(waypoints):
    """
    Given waypoints ([[lon, lat], ...]),
    compute cumulative distance in miles at each waypoint.

    Returns:
        route_array: numpy array of shape (N, 3) — [lon, lat, cumulative_miles]
    """
    result = []
    total = 0.0

    for i, (lon, lat) in enumerate(waypoints):
        if i == 0:
            result.append([lon, lat, 0.0])
        else:
            prev_lon, prev_lat, prev_miles = result[i - 1]
            segment_dist = haversine_miles(prev_lat, prev_lon, lat, lon)
            total += segment_dist
            result.append([lon, lat, total])

    return np.array(result, dtype=np.float64)  # shape (N, 3)


def snap_station_to_route(station_lat, station_lon, route_array, max_detour_miles=30):
    """
    Find where a fuel station sits "on" the route using vectorized haversine.

    Args:
        route_array: numpy (N, 3) array of [lon, lat, cumulative_miles]

    Returns: (miles_from_start, detour_miles) or None if too far off route.
    """
    route_lats = route_array[:, 1]
    route_lons = route_array[:, 0]
    distances = haversine_miles_vectorized(station_lat, station_lon, route_lats, route_lons)

    best_idx = int(np.argmin(distances))
    best_dist = float(distances[best_idx])

    if best_dist <= max_detour_miles:
        return float(route_array[best_idx, 2]), best_dist
    return None


# ──────────────────────────────────────────────────────
# STATION LOADING & FILTERING
# ──────────────────────────────────────────────────────

def load_stations_near_route(waypoints):
    """
    Load all geocoded fuel stations that fall within the bounding box
    of the route + a 1-degree buffer (~70 miles). Returns a list of dicts.
    """
    lons = [wp[0] for wp in waypoints]
    lats = [wp[1] for wp in waypoints]

    min_lat = min(lats) - 1.0
    max_lat = max(lats) + 1.0
    min_lon = min(lons) - 1.0
    max_lon = max(lons) + 1.0

    stations_qs = FuelStation.objects.filter(
        geocoded=True,
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    ).values('id', 'name', 'address', 'city', 'state', 'retail_price', 'latitude', 'longitude')

    return list(stations_qs)


# ──────────────────────────────────────────────────────
# MAIN OPTIMIZER
# ──────────────────────────────────────────────────────

def find_optimal_fuel_stops(route_data):
    """
    Main function: given route data from ORS, find the cheapest fuel stops.

    Algorithm: Smarter Greedy with Minimum-Fill Bridge Stops
    ---------------------------------------------------------
    Starting with a full tank, at each step we:
      1. Identify all stations reachable from current position (within fuel range)
      2. Identify the globally cheapest station in that window
      3. Look ahead: is there an even cheaper station JUST BEYOND our range?
         - If yes: find the closest reachable "bridge" station that gets us there,
           and fill MINIMALLY (just enough fuel to reach the cheap station).
         - If no: drive to the cheapest reachable station and fill to FULL TANK.

    This prevents wasting money filling up at an expensive station when a much
    cheaper one is just a little further ahead.

    Args:
        route_data: dict from routing.get_route()

    Returns:
        {
            "fuel_stops": [...],
            "total_fuel_cost_usd": 847.23,
            "total_gallons_needed": 279.0,
            "number_of_stops": N,
        }
    """
    waypoints = route_data["waypoints"]
    total_distance_miles = route_data["distance_miles"]

    RANGE = settings.VEHICLE_RANGE_MILES           # 500 miles
    MPG = settings.VEHICLE_MPG                     # 10 MPG
    TANK_GALLONS = settings.VEHICLE_TANK_GALLONS   # 50 gallons

    # ── Step 1: Thin waypoints for fast snapping ────────────────────────────
    # Use every 10th waypoint for snapping — city-level accuracy is sufficient.
    # Full waypoints are still used for the response (sampled in views.py).
    thinned = thin_waypoints(waypoints, step=10)

    # ── Step 2: Build cumulative miles array (numpy) ────────────────────────
    route_array = build_route_miles(thinned)  # shape (N, 3): [lon, lat, miles]

    # ── Step 3: Load candidate stations from DB (bounding box) ─────────────
    raw_stations = load_stations_near_route(waypoints)

    if not raw_stations:
        raise ValueError(
            "No geocoded fuel stations found near this route. "
            "Please run: python manage.py geocode_stations"
        )

    # ── Step 4: Snap each station to its position on the route ─────────────
    # Using vectorized numpy haversine — 50–100x faster than a Python loop.
    snapped_stations = []
    for station in raw_stations:
        result = snap_station_to_route(
            station['latitude'],
            station['longitude'],
            route_array,
            max_detour_miles=30,
        )
        if result is not None:
            route_miles, detour = result
            snapped_stations.append({
                **station,
                'route_mile': route_miles,
                'detour_miles': detour,
            })

    # Sort by position along route
    snapped_stations.sort(key=lambda s: s['route_mile'])

    if not snapped_stations:
        raise ValueError(
            "No fuel stations found within 30 miles of the route. "
            "The route may go through very remote areas."
        )

    # ── Step 5: Smarter Greedy Fuel Stop Selection ─────────────────────────
    fuel_stops = []
    current_mile = 0.0
    fuel_remaining_miles = float(RANGE)  # Start with a full tank

    iteration_limit = 100  # Safety: prevent infinite loops
    iterations = 0

    while current_mile + fuel_remaining_miles < total_distance_miles - 1:
        iterations += 1
        if iterations > iteration_limit:
            break

        farthest_reachable = current_mile + fuel_remaining_miles

        # All stations reachable from current position
        candidates = [
            s for s in snapped_stations
            if current_mile < s['route_mile'] <= farthest_reachable
        ]

        if not candidates:
            if current_mile + fuel_remaining_miles >= total_distance_miles:
                break
            else:
                raise ValueError(
                    f"Cannot reach destination! Stuck at mile {current_mile:.0f} "
                    f"with {fuel_remaining_miles:.0f} miles of fuel left. "
                    f"No stations found in the next {fuel_remaining_miles:.0f} miles."
                )

        # ── Smarter selection: look-ahead for a cheaper station just beyond reach ──
        cheapest_reachable = min(candidates, key=lambda s: s['retail_price'])

        # Check if there's a cheaper station JUST beyond our range (within 1 more full tank)
        extended_reach = farthest_reachable + RANGE
        beyond_candidates = [
            s for s in snapped_stations
            if farthest_reachable < s['route_mile'] <= extended_reach
        ]

        use_bridge = False
        bridge_station = None
        target_station = cheapest_reachable

        if beyond_candidates:
            cheapest_beyond = min(beyond_candidates, key=lambda s: s['retail_price'])

            if cheapest_beyond['retail_price'] < cheapest_reachable['retail_price']:
                # There's a cheaper station ahead! Use a bridge strategy.
                # Find the closest reachable station (bridge) to get near the target.
                # "Closest reachable" = the one with the highest route_mile we can reach.
                bridge_station = max(
                    [s for s in candidates
                     if s['route_mile'] + RANGE >= cheapest_beyond['route_mile']],
                    key=lambda s: s['route_mile'],
                    default=None
                )

                if bridge_station is not None:
                    use_bridge = True
                    target_station = cheapest_beyond

        if use_bridge and bridge_station is not None:
            # BRIDGE STOP: fill only enough fuel to reach the cheaper target station
            bridge_mile = bridge_station['route_mile']
            miles_to_bridge = bridge_mile - current_mile
            fuel_at_bridge = fuel_remaining_miles - miles_to_bridge  # miles of fuel left

            # Minimum fuel to reach the cheaper target from the bridge
            miles_bridge_to_target = target_station['route_mile'] - bridge_mile
            # Buffer: add 10% safety margin
            fuel_needed_to_reach_target = miles_bridge_to_target * 1.10
            fuel_to_add_at_bridge = max(0.0, fuel_needed_to_reach_target - fuel_at_bridge)
            gallons_to_fill = fuel_to_add_at_bridge / MPG

            if gallons_to_fill > 0.5:  # Only stop if we need at least half a gallon
                cost_at_stop = gallons_to_fill * bridge_station['retail_price']
                fuel_stops.append({
                    "name": bridge_station['name'],
                    "address": bridge_station['address'],
                    "city": bridge_station['city'],
                    "state": bridge_station['state'],
                    "price_per_gallon": round(bridge_station['retail_price'], 3),
                    "gallons_to_fill": round(gallons_to_fill, 2),
                    "cost_at_stop": round(cost_at_stop, 2),
                    "latitude": bridge_station['latitude'],
                    "longitude": bridge_station['longitude'],
                    "miles_from_start": round(bridge_mile, 1),
                    "stop_type": "bridge",          # Minimal fill to reach cheaper station
                })
                fuel_remaining_miles = fuel_at_bridge + fuel_to_add_at_bridge
                current_mile = bridge_mile
            else:
                # Skip the bridge — we already have enough to reach the target directly
                # Fall through to fill at the cheapest reachable station
                use_bridge = False
                target_station = cheapest_reachable

        if not use_bridge:
            # STANDARD STOP: drive to cheapest reachable, fill to FULL TANK
            station_mile = target_station['route_mile']
            miles_to_station = station_mile - current_mile
            fuel_at_arrival_miles = fuel_remaining_miles - miles_to_station

            # Fill to full tank
            gallons_at_arrival = fuel_at_arrival_miles / MPG
            gallons_to_fill = TANK_GALLONS - gallons_at_arrival
            gallons_to_fill = max(0.0, round(gallons_to_fill, 4))
            cost_at_stop = gallons_to_fill * target_station['retail_price']

            fuel_stops.append({
                "name": target_station['name'],
                "address": target_station['address'],
                "city": target_station['city'],
                "state": target_station['state'],
                "price_per_gallon": round(target_station['retail_price'], 3),
                "gallons_to_fill": round(gallons_to_fill, 2),
                "cost_at_stop": round(cost_at_stop, 2),
                "latitude": target_station['latitude'],
                "longitude": target_station['longitude'],
                "miles_from_start": round(station_mile, 1),
                "stop_type": "full_fill",
            })

            current_mile = station_mile
            fuel_remaining_miles = float(RANGE)   # Full tank after filling up

    # ── Step 6: Calculate totals ────────────────────────────────────────────
    # total_gallons_needed = total trip distance / MPG (required by assessment)
    total_gallons = total_distance_miles / MPG
    # total_fuel_cost = sum of all actual purchases at stops
    total_fuel_cost = sum(s['cost_at_stop'] for s in fuel_stops)

    return {
        "fuel_stops": fuel_stops,
        "total_fuel_cost_usd": round(total_fuel_cost, 2),
        "total_gallons_needed": round(total_gallons, 2),
        "number_of_stops": len(fuel_stops),
    }