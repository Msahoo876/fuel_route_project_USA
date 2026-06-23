"""
data_loader.py

Reads the fuel_prices.csv and inserts all stations into the FuelStation table.
This is called by the management command `load_fuel_stations`.

Key design decision:
- We do NOT geocode here (that's a separate slow step).
- This just gets the raw station data into the DB quickly.
"""

import pandas as pd
from django.conf import settings
from fuel_route.models import FuelStation


def load_stations_from_csv(csv_path=None, verbose=True):
    """
    Load all fuel stations from CSV into the database.
    Clears existing data first to avoid duplicates.
    
    Returns: number of stations inserted
    """
    if csv_path is None:
        csv_path = settings.FUEL_CSV_PATH

    if verbose:
        print(f"📂 Reading CSV from: {csv_path}")

    # Read CSV with pandas
    df = pd.read_csv(csv_path)

    if verbose:
        print(f"📊 Found {len(df)} rows in CSV")
        print(f"   Columns: {list(df.columns)}")

    # Clean up column names (remove extra spaces)
    df.columns = df.columns.str.strip()

    # Drop rows with missing prices
    df = df.dropna(subset=['Retail Price'])

    # Drop duplicate stations (same OPIS ID + same address)
    df = df.drop_duplicates(subset=['OPIS Truckstop ID', 'Address'])

    if verbose:
        print(f"✅ After cleaning: {len(df)} unique stations")

    # Clear existing data
    deleted_count = FuelStation.objects.all().delete()[0]
    if verbose and deleted_count:
        print(f"🗑️  Deleted {deleted_count} old records")

    # Build model instances for bulk insert
    stations = []
    for _, row in df.iterrows():
        stations.append(FuelStation(
            opis_id=int(row['OPIS Truckstop ID']),
            name=str(row['Truckstop Name']).strip(),
            address=str(row['Address']).strip(),
            city=str(row['City']).strip(),
            state=str(row['State']).strip().upper(),
            retail_price=float(row['Retail Price']),
            latitude=None,
            longitude=None,
            geocoded=False,
        ))

    # Bulk insert in batches of 500 for speed
    batch_size = 500
    for i in range(0, len(stations), batch_size):
        FuelStation.objects.bulk_create(stations[i:i + batch_size])
        if verbose:
            print(f"   Inserted batch {i // batch_size + 1} / {(len(stations) // batch_size) + 1}")

    if verbose:
        print(f"✅ Successfully loaded {len(stations)} fuel stations into DB!")

    return len(stations)