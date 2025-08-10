import os
import json
from typing import Optional, List, Dict

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .pipeline import UHIPipeline, PipelineConfig

app = FastAPI(title="UHI Analyzer", version="1.0")

# CORS for local dev frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_BASE = os.path.abspath(os.getenv("UHI_OUTPUT_DIR", "/workspace/output"))
os.makedirs(OUTPUT_BASE, exist_ok=True)

pipeline: Optional[UHIPipeline] = None


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
def analyze_city(
    city: str,
    year: int = Query(2023, ge=2001, le=2025),
    country: Optional[str] = None,
    force: bool = False,
) -> JSONResponse:
    global pipeline
    try:
        if pipeline is None:
            pipeline = UHIPipeline(output_dir=OUTPUT_BASE)
        config = PipelineConfig(year=year, city=city, country=country)
        result = pipeline.run(config=config, force=force)
        return JSONResponse(content=result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/assets")
def list_assets(city: str, year: int = 2023) -> JSONResponse:
    safe_city = city.lower().replace(" ", "_")
    asset_dir = os.path.join(OUTPUT_BASE, safe_city, str(year))
    if not os.path.isdir(asset_dir):
        raise HTTPException(status_code=404, detail="Assets not found. Run /analyze first.")
    assets = {}
    for root, _, files in os.walk(asset_dir):
        for f in files:
            if f.lower().endswith((".png", ".json", ".geojson")):
                rel = os.path.relpath(os.path.join(root, f), OUTPUT_BASE)
                assets[f] = f"/file/{rel}"
    return JSONResponse(content={"assets": assets})


@app.get("/file/{path:path}")
def get_file(path: str):
    abs_path = os.path.abspath(os.path.join(OUTPUT_BASE, path))
    if not abs_path.startswith(OUTPUT_BASE):
        raise HTTPException(status_code=403, detail="Forbidden path")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(abs_path)