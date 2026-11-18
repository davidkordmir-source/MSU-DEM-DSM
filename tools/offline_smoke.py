"""Functional self-check using only bundled modules; safe to run with Internet disconnected."""

import json
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "app").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from app.offline import enable_offline_mode

enable_offline_mode()

import numpy as np
import rasterio
import shapefile
from rasterio.transform import from_origin

from app.demo import make_demo
from app.previews import build_preview
from app.processing import process_dsm, process_rasters
from app.resources import resource_budget

for host in ("example.com", "1.1.1.1"):
    try:
        socket.create_connection((host, 443), timeout=0.1)
    except PermissionError:
        pass
    else:
        raise AssertionError("External connection was not blocked")
assert os.environ["PROJ_NETWORK"] == "OFF"

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp)
    dem, dsm = make_demo(path)
    report = process_dsm(dsm, path / "demo-output")
    assert report["ground_estimation"]["estimated"]
    assert report["ground_estimation"]["profile"] == "urban"
    assert report["selected_pixels"] == 28825
    assert report["max_height_m"] == 68
    assert report["vector"]["feature_count"] > 0
    with shapefile.Reader(str(path / "demo-output/features_001.shp")) as reader:
        records = [record.as_dict() for record in reader.iterRecords()]
        assert sum(record["PIXELS"] for record in records) == 28825
        assert max(record["H_MAX_M"] for record in records) == 68
        assert all(record["GND_SRC"] == "DSM_EST" for record in records)
    preview = path / "input-preview"
    preview.mkdir()
    info = build_preview(dsm, preview)
    assert info["valid_samples"] > 0 and (preview / "preview.png").is_file()
    coarse = path / "coarse.tif"
    fine = path / "fine.tif"
    for target, shape, resolution, elevation in (
        (coarse, (3, 3), 10, -100),
        (fine, (60, 60), 0.5, -50),
    ):
        with rasterio.open(
            target,
            "w",
            driver="GTiff",
            width=shape[1],
            height=shape[0],
            count=1,
            dtype="float32",
            crs="EPSG:32639",
            nodata=-9999,
            transform=from_origin(500000, 4100000, resolution, resolution),
        ) as dst:
            dst.write(np.full(shape, elevation, dtype="float32"), 1)
    report = process_rasters(coarse, fine, path / "resolution-output", threshold=30)
    assert report["selected_pixels"] == 3600
    assert report["resampling"] == "bilinear"
    with rasterio.open(path / "resolution-output/height.tif") as output:
        np.testing.assert_allclose(output.read(1), 50)
    # Exercise bundled PyShp, GEOS and raster masking together.
    aoi = path / "aoi"
    aoi.mkdir()
    with shapefile.Writer(str(aoi / "boundary")) as writer:
        writer.field("id", "N")
        writer.poly(
            [
                [
                    (500005, 4099995),
                    (500010, 4099995),
                    (500010, 4100000),
                    (500005, 4100000),
                    (500005, 4099995),
                ][::-1]
            ]
        )
        writer.record(1)
    (aoi / "boundary.prj").write_text(rasterio.CRS.from_epsg(32639).to_wkt())
    report = process_rasters(coarse, fine, path / "aoi-output", threshold=30, aoi_directory=aoi)
    assert report["selected_pixels"] == 100
    assert report["aoi"]["feature_count"] == 1
    with shapefile.Reader(str(path / "aoi-output/features_001.shp")) as reader:
        assert len(reader) == 1
        attributes = reader.record(0).as_dict()
        assert attributes["PIXELS"] == 100
        assert attributes["H_MEAN_M"] == 50
    with rasterio.open(path / "aoi-output/height.tif") as output:
        assert output.shape == (10, 10)
        np.testing.assert_allclose(output.read(1), 50)
    # Exercise offline PROJ using bundled coordinate databases.
    from rasterio.warp import transform

    x, y = transform("EPSG:4326", "EPSG:32639", [51.0], [35.0])
    assert abs(x[0] - 500000) < 1
    assert y[0] > 3_000_000
    print(
        json.dumps(
            {
                "offline_check": "passed",
                "platform": sys.platform,
                "python": sys.version.split()[0],
                "rasterio": rasterio.__version__,
                "resources": resource_budget(),
                "external_connections": "blocked",
                "checks": [
                    "DSM-only estimated ground with SciPy",
                    "negative elevations",
                    "10m vs 0.5m",
                    "shapefile AOI",
                    "attributed shapefile output",
                    "input preview before extraction",
                    "GeoTIFF output",
                    "offline CRS conversion",
                ],
            }
        )
    )
