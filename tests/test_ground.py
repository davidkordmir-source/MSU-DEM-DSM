"""Synthetic accuracy, missing-support, and out-of-core regressions for DSM-only terrain."""

import numpy as np
import pytest
import rasterio
import shapefile
from rasterio.transform import from_origin
from shapely.geometry import box

from app import ground
from app.errors import ProcessingError
from app.processing import process_dsm


@pytest.mark.parametrize("datum", [-100, 0, 1400])
def test_dsm_only_strict_threshold_no_ceiling_and_datum_invariance(raster, tmp_path, datum):
    values = np.full((120, 160), float(datum))
    for col, height in zip((10, 40, 70, 100, 130), (30, 31, 50, 1000, 10000), strict=True):
        values[40:60, col : col + 10] += height
    dsm = raster("only-dsm.tif", values)
    output = tmp_path / "out"
    report = process_dsm(dsm, output, max_object_width=60)
    assert report["selected_pixels"] == 800
    assert report["max_height_m"] == pytest.approx(10000)
    assert report["ground_estimation"]["estimated"]
    assert set(report["inputs"]) == {"dsm", "ground"}
    with rasterio.open(output / "ground.tif") as src:
        np.testing.assert_allclose(src.read(1), datum, atol=1e-5)
    with shapefile.Reader(str(output / "features_001.shp")) as reader:
        rows = [record.as_dict() for record in reader.records()]
    assert [row["H_MAX_M"] for row in rows] == [31, 50, 1000, 10000]
    assert all(row["GND_SRC"] == "DSM_EST" and row["WEAK_PX"] == 0 for row in rows)
    inclusive = process_dsm(dsm, tmp_path / "inclusive", max_object_width=60, inclusive=True)
    assert inclusive["selected_pixels"] == 1000


@pytest.mark.parametrize("slope", [0, 0.05, 0.3])
def test_local_ground_preserves_slopes_beneath_buildings(slope):
    y, x = np.indices((192, 224))
    truth = -100 + slope * x + 0.03 * y
    surface = truth.copy()
    surface[50:100, 70:120] += 50
    estimate, codes = ground.estimate_tile(surface, dx=1, dy=1, max_width=100, profile="urban")
    np.testing.assert_allclose(estimate[50:100, 70:120], truth[50:100, 70:120], atol=1e-5)
    assert not np.any((surface - estimate > 30) & (surface == truth))
    assert (codes[50:100, 70:120] == 2).all()


def test_aoi_only_roof_uses_ground_outside_boundary(raster, aoi, tmp_path):
    values = np.full((160, 160), -100.0)
    values[50:110, 50:110] = -50
    dsm = raster("city.tif", values)
    boundary = aoi([box(500060, 4099910, 500090, 4099940)])
    report = process_dsm(dsm, tmp_path / "crop", aoi_directory=boundary, max_object_width=100)
    assert report["selected_pixels"] == 900
    assert report["min_height_m"] == report["max_height_m"] == 50
    assert report["grid"]["width"] == report["grid"]["height"] == 30
    assert report["ground_estimation"]["context_outside_aoi"]


def test_tile_seams_match_whole_scene_with_holes(raster, tmp_path, monkeypatch):
    y, x = np.indices((180, 240))
    values = -100 + 0.07 * x + 0.02 * y
    values[40:110, 60:135] += 50
    values[120:140, 30:80] += 80
    values[15:24, 28:32] = np.nan
    values[80:84, 95:98] = np.nan
    dsm = raster("seams.tif", values, nodata=np.nan)
    ground.build_ground(dsm, tmp_path / "whole", grid_size=1, max_width=90)
    monkeypatch.setattr(ground, "TILE_SIZE", 37)
    ground.build_ground(dsm, tmp_path / "tiles", grid_size=1, max_width=90)
    for name in ("ground", "ground_quality"):
        with (
            rasterio.open(tmp_path / f"whole/{name}.tif") as one,
            rasterio.open(tmp_path / f"tiles/{name}.tif") as many,
        ):
            np.testing.assert_allclose(one.read(1), many.read(1), atol=1e-5, equal_nan=True)


def test_units_scale_offset_and_original_half_metre_grid(raster, tmp_path):
    values = np.full((160, 160), (-100 / 0.3048 - 20) / 2)
    values[60:80, 60:80] = (-50 / 0.3048 - 20) / 2
    dsm = raster("feet.tif", values, scale=2, offset=20, transform=from_origin(500000, 4100000, 0.5, 0.5))
    report = process_dsm(dsm, tmp_path / "out", dsm_unit="ft", max_object_width=40)
    assert report["grid"]["resolution"] == [0.5, 0.5]
    assert report["selected_pixels"] == 400
    assert report["max_height_m"] == pytest.approx(50, abs=1e-5)


def test_geographic_input_uses_metric_model_and_native_output(raster, tmp_path):
    values = np.full((100, 100), -100.0)
    values[40:60, 40:60] = -50
    dsm = raster("degrees.tif", values, crs="EPSG:4326", transform=from_origin(51, 35, 0.00001, 0.00001))
    report = process_dsm(dsm, tmp_path / "out", max_object_width=60)
    assert report["grid"]["crs"] == "EPSG:4326"
    assert report["ground_estimation"]["crs"] == "EPSG:32639"
    assert report["selected_pixels"] == 400
    assert report["max_height_m"] == pytest.approx(50, abs=1e-5)


def test_no_ground_exposure_is_explicitly_an_estimate(raster, tmp_path):
    # This can be a roof at -50 or bare ground at -50. DSM alone cannot tell.
    dsm = raster("roof-only.tif", np.full((40, 40), -50))
    report = process_dsm(dsm, tmp_path / "out")
    assert report["selected_pixels"] == 0
    assert "قابل تعیین نیست" in report["ground_estimation"]["limitation"]
    assert "not confidence" in report["ground_estimation"]["quality_note"]


def test_nodata_is_not_ground_and_invalid_pixels_are_excluded(raster, tmp_path):
    values = np.full((100, 100), -100.0)
    values[35:60, 40:60] = -50
    values[:5] = -9999
    values[45:50, 45:50] = -9999
    dsm = raster("nodata.tif", values)
    report = process_dsm(dsm, tmp_path / "out", max_object_width=60)
    assert report["selected_pixels"] == 475
    with rasterio.open(tmp_path / "out/mask.tif") as src:
        assert (src.read(1)[45:50, 45:50] == 255).all()


def test_limited_support_is_labelled():
    values = np.full((1, 80), -100.0)
    values[:, 30:50] = -50
    estimate, codes = ground.estimate_tile(values, dx=1, dy=1, max_width=30, profile="urban")
    np.testing.assert_allclose(estimate, -100)
    assert (codes[:, 30:50] == 3).all()


@pytest.mark.parametrize(
    "options", [{"max_object_width": float("nan")}, {"ground_grid": 0}, {"ground_profile": "invalid"}]
)
def test_invalid_ground_parameters_fail_before_processing(raster, tmp_path, options):
    dsm = raster("dsm.tif", [[0]])
    with pytest.raises(ProcessingError):
        process_dsm(dsm, tmp_path / "out", **options)
