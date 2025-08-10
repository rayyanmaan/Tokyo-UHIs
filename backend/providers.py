import math
from typing import Dict, Any, Optional

import numpy as np
import requests

try:
    import ee  # type: ignore
except Exception:
    ee = None


def geocode_city_boundary(city: str, country: Optional[str] = None) -> 'ee.Geometry':
    # Use Nominatim to fetch city polygon or bbox
    q = f"{city}, {country}" if country else city
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "json", "polygon_geojson": 1, "limit": 1}
    resp = requests.get(url, params=params, headers={"User-Agent": "uhi-analyzer/1.0"}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Could not geocode city: {q}")
    item = data[0]
    if 'geojson' in item and item['geojson']:
        geom = ee.Geometry(item['geojson'])
    else:
        # Fallback to bbox
        bbox = [float(item['boundingbox'][2]), float(item['boundingbox'][0]), float(item['boundingbox'][3]), float(item['boundingbox'][1])]
        geom = ee.Geometry.Rectangle(bbox)
    return geom.simplify(100)


# Loaders

def load_lst(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    col = ee.ImageCollection('MODIS/061/MOD11A2') \
        .filterDate(f"{year}-06-01", f"{year}-08-31") \
        .select('LST_Day_1km')
    img = col.mean().multiply(0.02).subtract(273.15)
    return img.clip(aoi)


def load_ndvi(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    col = ee.ImageCollection('MODIS/061/MOD13Q1') \
        .filterDate(f"{year}-05-01", f"{year}-08-31") \
        .select('NDVI')
    img = col.mean().multiply(0.0001)
    return img.clip(aoi)


def load_lulc(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    # Use IGBP scheme
    img = ee.ImageCollection('MODIS/061/MCD12Q1') \
        .filterDate(f"{year}-01-01", f"{year}-12-31").first() \
        .select('LC_Type1')
    return img.clip(aoi)


def load_viirs_ntl(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    # Try NASA VNP46A2 monthly BRDF-corrected lights; fallback to NOAA monthly stable lights
    def nasa():
        col = ee.ImageCollection('NASA/VIIRS/002/VNP46A2').filterDate(f"{year}-01-01", f"{year}-12-31").select('DNB_BRDF_Corrected_NTL')
        return col.mean()
    def noaa():
        col = ee.ImageCollection('NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG').filter(ee.Filter.calendarRange(year, year, 'year')).select('avg_rad')
        return col.mean()
    try:
        img = nasa()
    except Exception:
        img = noaa()
    return img.clip(aoi)


def load_albedo(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    # Use white-sky albedo shortwave
    col = ee.ImageCollection('MODIS/006/MCD43A3') \
        .filterDate(f"{year}-06-01", f"{year}-08-31") \
        .select('Albedo_WSA_shortwave')
    img = col.mean().multiply(0.001)
    return img.clip(aoi)


def load_impervious(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    # Use GHSL built-up surface fraction as proxy for imperviousness
    # GHS-BUILT-S: fraction of built-up per pixel, 10m to 100m (resampled here)
    try:
        col = ee.ImageCollection('JRC/GHSL/P2019A/GHS_BUILT_S').filter(ee.Filter.eq('year', year))
        img = col.first().select('built_surface')
    except Exception:
        # Fallback to 2018 release single image
        img = ee.Image('JRC/GHSL/P2016/BUILT_LDSMT_GLOBE_V1').rename('built_surface')
    return img.resample('bilinear').reproject(crs='EPSG:4326', scale=100).clip(aoi)


def load_building_density(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    # Use Google Open Buildings polygons; compute footprint density via kernel over 500m
    fc = ee.FeatureCollection('GOOGLE/Research/open-buildings/v3/polygons').filterBounds(aoi)
    # Use area in m^2 as weight
    fc = fc.map(lambda f: f.set('area_m2', ee.Number(f.geometry().area())))
    img = fc.reduceToImage(properties=['area_m2'], reducer=ee.Reducer.sum())
    # Smooth over 500m kernel to approximate density
    kernel = ee.Kernel.circle(radius=500, units='meters', normalize=True)
    dens = img.focal_mean(kernel=kernel, iterations=1)
    return dens.rename('bld_density').clip(aoi)


def load_worldpop(year: int, aoi: 'ee.Geometry') -> 'ee.Image':
    try:
        col = ee.ImageCollection('WorldPop/GP/100m/pop').filter(ee.Filter.eq('year', year))
        img = col.first().select('population')
    except Exception:
        img = ee.ImageCollection('CIESIN/GPWv411/GPW_Population_Count').filter(ee.Filter.eq('year', year)).first().select('population_count')
    return img.resample('bilinear').reproject(crs='EPSG:4326', scale=100).clip(aoi)


def load_water_distance(aoi: 'ee.Geometry') -> 'ee.Image':
    water = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').gt(0)
    # Distance in meters using 30m base scale
    dist = water.Not().fastDistanceTransform(30).sqrt().multiply(30)
    return dist.clip(aoi)


def load_elevation(aoi: 'ee.Geometry') -> 'ee.Image':
    img = ee.Image('USGS/SRTMGL1_003').select('elevation')
    return img.clip(aoi)


def get_vis_params() -> Dict[str, Dict[str, Any]]:
    return {
        'lst': {'min': 15, 'max': 45, 'palette': ['#2c7bb6','#abd9e9','#ffffbf','#fdae61','#d7191c']},
        'ndvi': {'min': 0, 'max': 0.8, 'palette': ['#654321', '#c2b280', '#a6d96a', '#1a9641']},
        'albedo': {'min': 0.05, 'max': 0.3, 'palette': ['#1f77b4', '#aec7e8', '#fddbc7', '#d73027']},
        'impervious': {'min': 0, 'max': 100, 'palette': ['#f7f7f7', '#cccccc', '#969696', '#525252']},
        'bld': {'min': 0, 'max': 5000, 'palette': ['#f7fbff','#c6dbef','#6baed6','#2171b5','#08306b']},
        'pop': {'min': 0, 'max': 5000, 'palette': ['#ffffcc','#c2e699','#78c679','#31a354','#006837']},
        'ntl': {'min': 0, 'max': 60, 'palette': ['#000004','#2c105c','#711f81','#b63679','#ee605e','#fdae61']},
        'waterdist': {'min': 0, 'max': 5000, 'palette': ['#313695','#74add1','#fee090','#f46d43','#a50026']},
        'elev': {'min': 0, 'max': 1000, 'palette': ['#e0f3f8','#fee090','#fdae61','#f46d43','#a50026']},
        'lulc': {'min': 0, 'max': 17, 'palette': ['#05450a', '#086a10', '#54a708', '#78d203', '#009900', '#c6b044', '#dcd159', '#dade48', '#fbff13', '#b6ff05', '#27ff87', '#c24f44', '#a5a5a5', '#ff6d4c', '#69fff8', '#f9ffa4', '#1c0dff', '#ffffff']},
        'count': {'min': 0, 'max': 8, 'palette': ['#ffffff', '#fee8c8', '#fdbb84', '#e34a33']},
        'hot': {'palette': ['#00000000', '#ff0000']},
    }