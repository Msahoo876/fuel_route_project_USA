"""
views.py

Contains the single API endpoint: POST /api/route/

Flow:
1. Validate request body (start + finish locations)
2. Check cache — return immediately if same route was computed before
3. Call ORS API ONCE to get route geometry (geocoding runs in parallel)
4. Run fuel optimizer (pure Python/DB, no more API calls)
5. Cache the result, then return structured JSON response
"""

from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from fuel_route.serializers import RouteRequestSerializer
from fuel_route.utils.routing import get_route
from fuel_route.utils.fuel_optimizer import find_optimal_fuel_stops


# Waypoint sampling: send every Nth point in the response payload.
# The full geometry is used internally for accurate station snapping.
# 1 in every 20 points is more than enough to draw a smooth route on a map.
WAYPOINT_SAMPLE_RATE = 20

# Cache TTL: fuel prices rarely change intra-day, 1 hour is safe.
CACHE_TTL_SECONDS = 3600


class RouteView(APIView):
    """
    POST /api/route/

    Request body:
        {
            "start": "New York, NY",
            "finish": "Los Angeles, CA"
        }

    Response:
        {
            "start": "...",
            "finish": "...",
            "total_distance_miles": 2790.5,
            "total_fuel_cost_usd": 847.23,
            "total_gallons_needed": 279.0,
            "number_of_stops": 5,
            "fuel_stops": [...],
            "route_waypoints": [[lon, lat], ...]   # sampled for compact payload
        }
    """

    def post(self, request):
        # ── Step 1: Validate input ──────────────────────────
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid input", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        start = serializer.validated_data['start'].strip().lower()
        finish = serializer.validated_data['finish'].strip().lower()

        # ── Step 2: Cache check — skip all computation for repeat routes ──
        cache_key = f"fuel_route:{start}:{finish}"
        cached_response = cache.get(cache_key)
        if cached_response:
            cached_response["cached"] = True
            return Response(cached_response, status=status.HTTP_200_OK)

        # ── Step 3: Get route from ORS (geocoding + directions) ──────────
        # routing.py runs both geocode calls in parallel, then one directions call.
        try:
            route_data = get_route(start, finish)
        except ValueError as e:
            return Response(
                {"error": f"Geocoding failed: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Routing API error: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # ── Step 4: Find optimal fuel stops (pure local — no API calls) ───
        try:
            optimization_result = find_optimal_fuel_stops(route_data)
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Optimization error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # ── Step 5: Build response ────────────────────────────────────────
        # Sample waypoints: take every Nth point for a compact payload.
        # Always preserve the very first and last points so the route
        # starts and ends at the exact geocoded locations.
        all_waypoints = route_data["waypoints"]
        sampled = all_waypoints[::WAYPOINT_SAMPLE_RATE]
        # Ensure start & end are always included
        if sampled[0] != all_waypoints[0]:
            sampled = [all_waypoints[0]] + sampled
        if sampled[-1] != all_waypoints[-1]:
            sampled = sampled + [all_waypoints[-1]]

        response_data = {
            "start": route_data["start_label"],
            "finish": route_data["finish_label"],
            "total_distance_miles": route_data["distance_miles"],
            "total_fuel_cost_usd": optimization_result["total_fuel_cost_usd"],
            "total_gallons_needed": optimization_result["total_gallons_needed"],
            "number_of_stops": optimization_result["number_of_stops"],
            "fuel_stops": optimization_result["fuel_stops"],
            "route_waypoints": sampled,          
            "cached": False,
        }

        # ── Step 6: Cache result for repeat calls ──────────────────────────
        cache.set(cache_key, response_data, timeout=CACHE_TTL_SECONDS)

        return Response(response_data, status=status.HTTP_200_OK)
