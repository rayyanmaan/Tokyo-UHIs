# UHI Analyzer

Backend (FastAPI) + Frontend (static) to analyze Urban Heat Island hotspots per city using Google Earth Engine with Getis-Ord Gi* and Local Moran's I.

## Prerequisites
- Python 3.10+
- A Google account with Earth Engine access (use: maan@uni.minerva.edu)
- Internet access for OpenStreetMap geocoding and data fetching

## Setup

```bash
# 1) Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2) Install backend dependencies
pip install --upgrade pip
pip install -r backend/requirements.txt

# 3) Authenticate Google Earth Engine (first time only)
python -c "import ee; ee.Authenticate(); ee.Initialize(project='mod11a2')"
# Follow the printed URL, sign in with your account (maan@uni.minerva.edu),
# paste the auth code back in the terminal.

# 4) Run the backend API (port 8000)
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

In a separate terminal, you can serve the static frontend (optional). If your environment already serves `/workspace/frontend`, skip this and just open the file.

```bash
# Simple static server using Python
python -m http.server 5173
```

Open the UI:
- Backend: http://localhost:8000/health
- Frontend: http://localhost:5173/frontend/index.html

Set the backend and frontend to run in the same machine. The frontend points to the backend at port 8000.

## Usage
1. Open the frontend page.
2. Enter a city name (e.g., "Tokyo") and a year (e.g., 2023).
3. Click Analyze. The backend will:
   - Geocode the city via OpenStreetMap and build an AOI
   - Fetch satellite data from GEE (LST, NDVI, LULC, NTL, Albedo, Impervious, Building Density, Population, Water Distance, Elevation)
   - Compute percentile thresholds (hot: 80th, cool: 20th)
   - Build a threshold exceedance count map (0-8)
   - Create preliminary hotspots (>=6/8, AND urban LULC, AND elevation within city range)
   - Sample 1,500 points and run Getis-Ord Gi* and Local Moran's I
   - Generate PNG thumbnails and JSON outputs under `/workspace/output/<city>/<year>/`

4. The UI will display all variable maps, the threshold exceedance map, and preliminary hotspots, plus a link to download the spatial stats JSON.

## Notes
- If `ee.Initialize(project='mod11a2')` fails, the code falls back to default initialization.
- If some datasets are unavailable for a given year/region, sensible fallbacks are used.
- All processing is server-side; the UI only renders returned images.

## Troubleshooting
- If you see an auth error, rerun:
```bash
python -c "import ee; ee.Authenticate(); ee.Initialize(project='mod11a2')"
```
- If OpenStreetMap geocoding rate-limits you, try again in a minute or add a country hint (e.g., "Paris, France").
- Large cities can take several minutes; be patient.
