from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """
    Validates the incoming POST body.
    Both start and finish must be US city/address strings.
    """
    start = serializers.CharField(
        max_length=200,
        help_text="Starting location, e.g. 'New York, NY'"
    )
    finish = serializers.CharField(
        max_length=200,
        help_text="Destination location, e.g. 'Los Angeles, CA'"
    )


class FuelStopSerializer(serializers.Serializer):
    """
    Represents a single recommended fuel stop in the response.
    """
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    price_per_gallon = serializers.FloatField()
    gallons_to_fill = serializers.FloatField()
    cost_at_stop = serializers.FloatField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    miles_from_start = serializers.FloatField()


class RouteResponseSerializer(serializers.Serializer):
    """
    The complete API response shape.
    """
    start = serializers.CharField()
    finish = serializers.CharField()
    total_distance_miles = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    total_gallons_needed = serializers.FloatField()
    number_of_stops = serializers.IntegerField()
    fuel_stops = FuelStopSerializer(many=True)
    route_waypoints = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField()),
        help_text="List of [lon, lat] pairs representing the full route"
    )