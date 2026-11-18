from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def make_demo(directory: Path) -> tuple[Path, Path]:
    """A synthetic flat urban survey below sea level, including exact 30 m and 50 m objects."""
    directory.mkdir(parents=True, exist_ok=True)
    dem = np.full((360, 480), -100, dtype="float32")
    height = np.zeros_like(dem)
    blocks = [
        (50, 100, 55, 120, 50),
        (130, 185, 55, 120, 42),
        (215, 290, 55, 120, 68),
        (50, 100, 165, 225, 24),
        (135, 210, 165, 225, 55),
        (245, 300, 165, 225, 30),
        (55, 125, 270, 320, 36),
        (165, 230, 270, 320, 18),
        (265, 310, 270, 320, 48),
        (45, 105, 370, 425, 61),
        (145, 200, 370, 425, 28),
        (240, 305, 370, 425, 45),
    ]
    for y1, y2, x1, x2, value in blocks:
        height[y1:y2, x1:x2] = value
    dem[50:100, 55:120] = -100  # The user's exact -50 - (-100) = 50 example.
    dsm = dem + height
    dem[:8, :] = -9999
    dsm[:8, :] = -9999
    profile = {
        "driver": "GTiff",
        "height": 360,
        "width": 480,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32639",
        "transform": from_origin(500000, 4100000, 0.5, 0.5),
        "nodata": -9999,
        "compress": "deflate",
    }
    paths = directory / "dem.tif", directory / "dsm.tif"
    for path, data in zip(paths, (dem, dsm), strict=True):
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)
            dst.set_band_unit(1, "m")
    return paths
