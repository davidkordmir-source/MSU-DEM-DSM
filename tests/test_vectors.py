import numpy as np
import pytest
import rasterio
import shapefile
from rasterio.transform import from_origin
from shapely.geometry import shape

from app import processing, vectors
from app.processing import process_rasters


def read_features(directory):
    features = []
    for path in sorted(directory.glob("features_*.shp")):
        with shapefile.Reader(str(path)) as reader:
            features.extend(
                (shape(s.shape.__geo_interface__), s.record.as_dict()) for s in reader.iterShapeRecords()
            )
        assert rasterio.CRS.from_wkt(path.with_suffix(".prj").read_text()).to_epsg()
        assert path.with_suffix(".cpg").read_text() == "UTF-8"
    return features


def test_threshold_has_no_upper_limit_and_attributes(raster, tmp_path):
    dem = raster("dem.tif", [[-100] * 6])
    dsm = raster("dsm.tif", [[-70, -69, -50, 100, 900, 9900]])
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, threshold=30)
    assert report["selected_pixels"] == 5
    [(polygon, row)] = read_features(out)
    assert polygon.area == 5
    assert row["PIXELS"] == 5
    assert row["H_MIN_M"] == 31
    assert row["H_MAX_M"] == 10000
    assert row["H_MEAN_M"] == pytest.approx((31 + 50 + 200 + 1000 + 10000) / 5)
    assert row["THRESH_M"] == 30 and row["RULE"] == ">"
    assert row["AREA_M2"] == 5


def test_components_merge_across_tiles_and_keep_holes(raster, tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "TILE_SIZE", 2)
    budget = processing.resource_budget()
    monkeypatch.setattr(processing, "resource_budget", lambda: dict(budget, block_size=3))
    ground = np.full((8, 9), -100)
    surface = np.full((8, 9), -50)
    surface[2:6, 2:7] = -100
    transform = from_origin(500000, 4100000, 0.1, 0.1)
    out = tmp_path / "out"
    report = process_rasters(
        raster("dem.tif", ground, transform=transform),
        raster("dsm.tif", surface, transform=transform),
        out,
    )
    [(polygon, row)] = read_features(out)
    assert report["vector"]["feature_count"] == 1
    assert polygon.is_valid
    assert len(polygon.interiors) == 1
    assert polygon.area == pytest.approx(52 * 0.01)
    assert row["PIXELS"] == 52
    assert row["H_MEAN_M"] == 50
    assert not (out / ".vector-work.sqlite").exists()


def test_diagonal_contacts_are_separate_and_nodata_excluded(raster, tmp_path):
    out = tmp_path / "out"
    process_rasters(raster("dem.tif", [[0, 0], [0, -9999]]), raster("dsm.tif", [[50, 0], [0, 100]]), out)
    assert len(read_features(out)) == 1
    out2 = tmp_path / "diagonal"
    process_rasters(raster("dem2.tif", [[0, 0], [0, 0]]), raster("dsm2.tif", [[50, 0], [0, 100]]), out2)
    assert len(read_features(out2)) == 2


def test_empty_shapefile_and_geographic_null_area(raster, tmp_path):
    out = tmp_path / "empty"
    process_rasters(raster("dem.tif", [[0]]), raster("dsm.tif", [[30]]), out)
    assert read_features(out) == []
    out2 = tmp_path / "geographic"
    process_rasters(
        raster("geo-dem.tif", [[0]], crs="EPSG:4326", transform=from_origin(51, 35, 0.01, 0.01)),
        raster("geo-dsm.tif", [[50]], crs="EPSG:4326", transform=from_origin(51, 35, 0.01, 0.01)),
        out2,
    )
    assert read_features(out2)[0][1]["AREA_M2"] is None


def test_shapefile_rollover_preserves_all_ids(raster, tmp_path, monkeypatch):
    monkeypatch.setattr(vectors, "SHAPEFILE_BYTES", 800)
    out = tmp_path / "out"
    report = process_rasters(
        raster("dem.tif", [[0] * 7]), raster("dsm.tif", [[50, 0, 60, 0, 70, 0, 80]]), out
    )
    assert len(report["vector"]["layers"]) > 1
    features = read_features(out)
    assert [row["OBJ_ID"] for _, row in features] == [1, 2, 3, 4]
