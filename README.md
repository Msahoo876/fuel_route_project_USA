# ⛽ Fuel Route API

A Django REST API that finds the most cost-effective fuel stops along any US driving route.

## Features
- Takes a start and finish US location
- Returns optimal fuel stops (cheapest price along route)
- Vehicle: 500-mile range, 10 MPG
- Single call to OpenRouteService API per request
- All station matching done locally from pre-loaded DB

## Tech Stack
- Django 5.x + Django REST Framework
- SQLite (dev) / PostgreSQL (prod)
- OpenRouteService API (free tier)
- Nominatim for geocoding stations

---

## Setup

### 1. Clone & Install
```bash
git clone <your-repo>
cd fuel_route_project
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API Key
In `fuel_route_project/settings.py`, set your ORS key:
```python
ORS_API_KEY = 'your-key-from-openrouteservice.org'
```
Get a free key at: https://openrouteservice.org/dev/#/login

### 3. Copy CSV Data
```bash
mkdir data
cp /path/to/fuel_prices.csv data/fuel_prices.csv
```

### 4. Run Migrations & Load Data
```bash
python manage.py migrate
python manage.py load_fuel_stations
python manage.py geocode_stations   # ⚠️ Takes ~30-60 min (one-time only)
```

### 5. Start Server
```bash
python manage.py runserver
```

---

## API Usage

**Endpoint:** `POST /api/route/`

**Request:**
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

**Response:**
```json
{
  "start": "New York, New York, United States",
  "finish": "Los Angeles, California, United States",
  "total_distance_miles": 2790.5,
  "total_fuel_cost_usd": 847.23,
  "total_gallons_needed": 279.0,
  "number_of_stops": 6,
  "fuel_stops": [
    {
      "name": "PILOT TRAVEL CENTER #212",
      "address": "I-76, EXIT 54",
      "city": "Youngstown",
      "state": "OH",
      "price_per_gallon": 3.112,
      "gallons_to_fill": 50.0,
      "cost_at_stop": 155.60,
      "latitude": 41.09,
      "longitude": -80.64,
      "miles_from_start": 415.2
    }
  ],
  "route_waypoints": [[-74.006, 40.713], ...]
}
```

---

## Architecture

```
POST /api/route/
       │
       ▼
  views.py (RouteView)
       │
       ├── routing.py ──► OpenRouteService API (1 call)
       │                  Gets route geometry + distance
       │
       └── fuel_optimizer.py (pure local Python)
               │
               ├── Build route with cumulative miles
               ├── Query DB for stations in bounding box
               ├── Snap each station to route position
               └── Greedy algorithm → cheapest stops
```

## How the Algorithm Works

1. Route is converted to a list of waypoints with cumulative miles
2. Stations in the route's bounding box are loaded from DB
3. Each station is "snapped" to its closest point on the route
4. Greedy algorithm: starting at mile 0 with full tank, always pick the cheapest reachable station, fill up, repeat
5. Total cost = sum of (gallons filled × price per gallon) at each stop