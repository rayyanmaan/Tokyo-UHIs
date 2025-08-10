import os
import io
import json
import math
import time
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

# Import Earth Engine lazily to allow helpful error messages on auth
try:
    import ee  # type: ignore
except Exception as _e:
    ee = None

from .stats import run_spatial_stats
from .providers import (
    geocode_city_boundary,
    load_lst,
    load_ndvi,
    load_lulc,
    load_viirs_ntl,
    load_albedo,
    load_impervious,
    load_building_density,
    load_worldpop,
    load_water_distance,
    load_elevation,
    get_vis_params,
)


@dataclass
class PipelineConfig:
    year: int
    city: str
    country: Optional[str] = None


def _safe_slug(text: str) -> str:
    return text.lower().replace(" ", "_")


class UHIPipeline:
    def __init__(self, output_dir: str):
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)
        self._ensure_ee_initialized()

    def _ensure_ee_initialized(self) -> None:
        global ee
        if ee is None:
            raise RuntimeError("earthengine-api is not installed. Please install requirements.")
        try:
            # Try project-specific init as requested
            ee.Initialize(project='mod11a2')
        except Exception:
            try:
                ee.Initialize()
            except Exception as exc:
                # Provide a helpful message for first-time auth
                raise RuntimeError(
                    "Google Earth Engine authentication is required. Run: \n"
                    "  python -c 'import ee; ee.Authenticate(); ee.Initialize(project=\"mod11a2\")' \n"
                    "Follow the URL, sign in with maan@uni.minerva.edu, paste the code back, then rerun.\n"
                    f"Underlying error: {exc}"
                )

    def _city_output_dir(self, city: str, year: int) -> str:
        city_dir = os.path.join(self.output_dir, _safe_slug(city), str(year))
        os.makedirs(city_dir, exist_ok=True)
        return city_dir

    def _save_json(self, path: str, data: Dict[str, Any]) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def _thumb(self, image: 'ee.Image', region: 'ee.Geometry', vis: Dict[str, Any], out_path: str, dimensions: int = 2048) -> str:
        params = {
            'region': region,
            'dimensions': dimensions
        }
        if 'min' in vis:
            params['min'] = vis.get('min')
        if 'max' in vis:
            params['max'] = vis.get('max')
        if 'palette' in vis:
            params['palette'] = vis.get('palette')
        url = image.getThumbURL(params)
        # Download
        import requests
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        with open(out_path, 'wb') as f:
            f.write(r.content)
        return out_path

    def _boolean_count(self, images: List['ee.Image']) -> 'ee.Image':
        stack = ee.ImageCollection(images).toBands()
        # Reduce along bands: count of non-zero
        return stack.gt(0).reduce(ee.Reducer.sum()).rename('count_true')

    def run(self, config: PipelineConfig, force: bool = False) -> Dict[str, Any]:
        city = config.city
        year = config.year
        city_dir = self._city_output_dir(city, year)
        index_path = os.path.join(city_dir, 'index.json')
        if os.path.isfile(index_path) and not force:
            with open(index_path, 'r', encoding='utf-8') as f:
                return json.load(f)

        # 1) Boundary
        aoi = geocode_city_boundary(city, config.country)

        # 2) Load variables
        lst = load_lst(year, aoi).rename('lst')
        ndvi = load_ndvi(year, aoi).rename('ndvi')
        lulc = load_lulc(year, aoi).rename('lulc')
        ntl = load_viirs_ntl(year, aoi).rename('ntl')
        albedo = load_albedo(year, aoi).rename('albedo')
        impervious = load_impervious(year, aoi).rename('impervious')
        building_density = load_building_density(year, aoi).rename('bld_dens')
        population = load_worldpop(year, aoi).rename('pop')
        water_distance = load_water_distance(aoi).rename('water_dist')
        elevation = load_elevation(aoi).rename('elev')

        # 3) Thresholds (percentiles per AOI)
        def pctl(img: 'ee.Image', pct: float, scale: int) -> float:
            val = img.reduceRegion(ee.Reducer.percentile([pct]), aoi, scale=scale, maxPixels=1e9)
            # take first value
            return ee.Number(val.values().get(0))

        lst80 = pctl(lst, 80, 1000)
        ndvi20 = pctl(ndvi, 20, 300)
        ntl80 = pctl(ntl, 80, 500)
        albedo20 = pctl(albedo, 20, 500)
        imperv80 = pctl(impervious, 80, 100)
        bld80 = pctl(building_density, 80, 300)
        pop80 = pctl(population, 80, 300)

        # 4) Mandatory masks
        urban_mask = lulc.eq(13)
        elev_mean = elevation.reduceRegion(ee.Reducer.mean(), aoi, scale=90, maxPixels=1e9).get('elev')
        elev_ok = elevation.lte(ee.Number(elev_mean).add(200))

        # 5) Boolean exceedances (8 vars)
        conds = [
            lst.gt(lst80).rename('c_lst'),
            ndvi.lt(ndvi20).rename('c_ndvi'),
            albedo.lt(albedo20).rename('c_albedo'),
            impervious.gt(imperv80).rename('c_imperv'),
            building_density.gt(bld80).rename('c_bld'),
            population.gt(pop80).rename('c_pop'),
            ntl.gt(ntl80).rename('c_ntl'),
            water_distance.gt(500).rename('c_waterdist')
        ]

        count_true = self._boolean_count(conds)
        prelim_hot = count_true.gte(6).And(urban_mask).And(elev_ok).selfMask().rename('prelim_hot')

        # 6) Sampling for spatial stats
        # Sample 1500 points within AOI at 300m scale
        sample_img = count_true
        samples = sample_img.addBands([lst, ndvi, ntl]).sample(
            region=aoi, scale=300, numPixels=1500, geometries=True, seed=42
        )
        # Export samples to client for stats
        # Get lists
        coords = samples.map(lambda f: f.set({'lon': f.geometry().coordinates().get(0), 'lat': f.geometry().coordinates().get(1)}))
        fc = coords.getInfo()
        values = []
        lons = []
        lats = []
        for feat in fc['features']:
            props = feat['properties']
            if 'count_true' in props and props['count_true'] is not None:
                values.append(props['count_true'])
                lons.append(props['lon'])
                lats.append(props['lat'])
        stats = run_spatial_stats(values=np.array(values), lons=np.array(lons), lats=np.array(lats))

        # 7) Visual products
        vis = get_vis_params()
        assets: Dict[str, str] = {}

        # Save raw thresholds
        thresholds_path = os.path.join(city_dir, 'thresholds.json')
        self._save_json(thresholds_path, {
            'lst80': float(lst80.getInfo()),
            'ndvi20': float(ndvi20.getInfo()),
            'ntl80': float(ntl80.getInfo()),
            'albedo20': float(albedo20.getInfo()),
            'imperv80': float(imperv80.getInfo()),
            'bld80': float(bld80.getInfo()),
            'pop80': float(pop80.getInfo())
        })
        assets['thresholds.json'] = thresholds_path

        # Thumbnails for variables
        var_imgs = {
            'lst.png': lst.visualize(**vis['lst']),
            'ndvi.png': ndvi.visualize(**vis['ndvi']),
            'albedo.png': albedo.visualize(**vis['albedo']),
            'impervious.png': impervious.visualize(**vis['impervious']),
            'building_density.png': building_density.visualize(**vis['bld']),
            'population.png': population.visualize(**vis['pop']),
            'ntl.png': ntl.visualize(**vis['ntl']),
            'water_distance.png': water_distance.visualize(**vis['waterdist']),
            'elevation.png': elevation.visualize(**vis['elev']),
            'lulc.png': lulc.visualize(**vis['lulc'])
        }
        for name, vimg in var_imgs.items():
            out = os.path.join(city_dir, name)
            self._thumb(vimg, aoi, vis={'min': 0, 'max': 1}, out_path=out)
            assets[name] = out

        # Count-exceedance map
        count_vis = count_true.visualize(**vis['count'])
        count_path = os.path.join(city_dir, 'threshold_exceedance.png')
        self._thumb(count_vis, aoi, vis={}, out_path=count_path)
        assets['threshold_exceedance.png'] = count_path

        # Preliminary hotspot
        prelim_vis = prelim_hot.visualize(**vis['hot'])
        prelim_path = os.path.join(city_dir, 'preliminary_hotspots.png')
        self._thumb(prelim_vis, aoi, vis={}, out_path=prelim_path)
        assets['preliminary_hotspots.png'] = prelim_path

        # Build consensus validated hotspots: Gi* hotspot (95%) AND Moran significant (95%) with HH quadrant
        try:
            gi_hot = np.array(stats.get('gi', {}).get('hotspot_95_mask', []), dtype=int)
            mi_sig = np.array(stats.get('moran', {}).get('significant_95_mask', []), dtype=int)
            mi_q = np.array(stats.get('moran', {}).get('q', []), dtype=int)
            sig_both = (gi_hot == 1) & (mi_sig == 1) & (mi_q == 1)
            # Rebuild point FeatureCollection with sig_both
            features = []
            idx = 0
            for feat in fc['features']:
                props = feat['properties']
                if 'count_true' in props and props['count_true'] is not None:
                    if idx < sig_both.size and sig_both[idx]:
                        lon = props['lon']
                        lat = props['lat']
                        point = ee.Geometry.Point([lon, lat])
                        features.append(ee.Feature(point, {'sig': 1}))
                    idx += 1
            sig_fc = ee.FeatureCollection(features)
            sig_img = sig_fc.reduceToImage(['sig'], ee.Reducer.sum()).rename('sig')
            # Buffer influence using 500 m kernel, then threshold > 0
            kernel = ee.Kernel.circle(radius=500, units='meters')
            sig_density = sig_img.focal_max(kernel=kernel)
            validated_hot = prelim_hot.And(sig_density.gt(0)).selfMask().rename('validated_hot')
            val_vis = validated_hot.visualize(**vis['hot'])
            val_path = os.path.join(city_dir, 'validated_hotspots.png')
            self._thumb(val_vis, aoi, vis={}, out_path=val_path)
            assets['validated_hotspots.png'] = val_path
        except Exception:
            # If anything fails, skip validated map
            pass

        # Save stats JSON
        stats_path = os.path.join(city_dir, 'spatial_stats.json')
        self._save_json(stats_path, stats)
        assets['spatial_stats.json'] = stats_path

        # Index
        index = {
            'city': city,
            'year': year,
            'assets': {os.path.basename(k): os.path.relpath(v, self.output_dir) for k, v in assets.items()},
            'notes': {
                'auth': 'Initialized Earth Engine with project mod11a2, with fallback to default.',
                'validation': 'Getis-Ord Gi* and Moran\'s I computed on 1500 samples of threshold exceedance count.'
            }
        }
        self._save_json(index_path, index)
        return index