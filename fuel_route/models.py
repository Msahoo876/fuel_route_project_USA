# Create your models here.
from django.db import models


class FuelStation(models.Model):
    """
    Represents a single fuel/truckstop station from the CSV.
    
    We pre-load all 8,151 stations into this table once (via management command).
    At request time, we query this table locally — no external API calls needed
    for station data.
    """
    opis_id = models.IntegerField()                          # Unique station ID from CSV
    name = models.CharField(max_length=255)                  # Truckstop Name
    address = models.CharField(max_length=255)               # Street address
    city = models.CharField(max_length=100)                  # City
    state = models.CharField(max_length=2)                   # 2-letter state code
    retail_price = models.FloatField()                       # Price per gallon (USD)
    
    # These are filled in by the geocoding management command
    latitude = models.FloatField(null=True, blank=True)      # GPS lat
    longitude = models.FloatField(null=True, blank=True)     # GPS lon
    
    geocoded = models.BooleanField(default=False)            # Was geocoding successful?

    class Meta:
        # Index on lat/lon for fast spatial queries
        indexes = [
            models.Index(fields=['latitude', 'longitude']),
            models.Index(fields=['state']),
            models.Index(fields=['geocoded']),
        ]

    def __str__(self):
        return f"{self.name} — {self.city}, {self.state} @ ${self.retail_price:.3f}"