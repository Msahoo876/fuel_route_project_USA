"""
Management command: load_fuel_stations

Usage:
    python manage.py load_fuel_stations
    python manage.py load_fuel_stations --csv /path/to/custom.csv

This command reads the fuel prices CSV and inserts all stations into the DB.
Run this ONCE before starting the server.
"""

from django.core.management.base import BaseCommand
from fuel_route.utils.data_loader import load_stations_from_csv


class Command(BaseCommand):
    help = 'Load fuel stations from CSV into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            default=None,
            help='Path to fuel prices CSV file (defaults to settings.FUEL_CSV_PATH)',
        )

    def handle(self, *args, **options):
        csv_path = options.get('csv')
        self.stdout.write("🚀 Starting fuel station data load...")

        count = load_stations_from_csv(csv_path=csv_path, verbose=True)

        self.stdout.write(
            self.style.SUCCESS(f'\n✅ Done! {count} fuel stations loaded into database.')
        )
        self.stdout.write(
            '\n⚡ Next step: run geocoding with:\n'
            '   python manage.py geocode_stations\n'
        )