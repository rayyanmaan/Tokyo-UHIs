from typing import Dict
import numpy as np

from esda.getisord import G_Local
from esda.moran import Moran_Local
from libpysal.weights import KNN


def run_spatial_stats(values: np.ndarray, lons: np.ndarray, lats: np.ndarray) -> Dict:
    # Remove NaNs
    mask = ~np.isnan(values)
    values = values[mask]
    lons = lons[mask]
    lats = lats[mask]
    if values.size < 50:
        return {"error": "Insufficient samples for spatial statistics"}

    coords = np.column_stack([lons, lats])

    # Standardize values
    x = (values - values.mean()) / (values.std() + 1e-9)

    # Spatial weights: 8-nearest neighbors
    w = KNN.from_array(coords, k=8)
    w.transform = 'R'

    # Getis-Ord Gi*
    gi = G_Local(x, w, star=True)

    # Local Moran's I
    mi = Moran_Local(x, w)

    return {
        'n': int(values.size),
        'gi': {
            'z_scores': gi.Zs.tolist(),
            'p_values': gi.p_norm.tolist(),
            'hotspot_95_mask': ((gi.Zs > 1.96) & (gi.p_norm < 0.05)).astype(int).tolist()
        },
        'moran': {
            'Is': mi.Is.tolist(),
            'p_values': mi.p_sim.tolist(),
            'significant_95_mask': (mi.p_sim < 0.05).astype(int).tolist(),
            'q': mi.q.tolist()
        }
    }