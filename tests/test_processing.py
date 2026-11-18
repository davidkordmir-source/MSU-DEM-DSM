import json
from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import transform_bounds

from app.downloads import stream_results
from app.processing import ProcessingError, process_rasters


def read(output, name):
    with rasterio.open(output / f"{name}.tif") as src:
        return src.read(1)


def test_below_sea_level_and_strict_boundary(raster, tmp_path):
    dem = raster("dem.tif", [[-100, -100, -100, 20, 20]])
    dsm = raster("dsm.tif", [[-50, -70, -71, 70, 20]])
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, threshold=30)
    np.testing.assert_array_equal(read(out, "height"), [[50, 30, 29, 50, 0]])
    np.testing.assert_array_equal(read(out, "mask"), [[1, 0, 0, 1, 0]])
    assert report["selected_pixels"] == 2
    assert report["mean_height_m"] == 50
    assert report["selected_area_m2"] == 2
    assert report["resampling"] == "none"
    assert np.isnan(read(out, "extracted")[0, 1])
    with ZipFile(BytesIO(b"".join(stream_results(out)))) as archive:
        assert {
            "height.tif",
            "extracted.tif",
            "mask.tif",
            "report.json",
            "features_001.shp",
            "features_001.dbf",
        } <= set(archive.namelist())
        assert json.loads(archive.read("report.json"))["selected_pixels"] == 2


def test_inclusive_and_empty_selection(raster, tmp_path):
    dem = raster("dem.tif", [[-100, -100]])
    dsm = raster("dsm.tif", [[-70, -50]])
    report = process_rasters(dem, dsm, tmp_path / "inclusive", threshold=30, inclusive=True)
    assert report["selected_pixels"] == 2
    report = process_rasters(dem, dsm, tmp_path / "empty", threshold=500)
    assert report["selected_pixels"] == 0
    assert report["max_height_m"] is None
    assert report["mean_height_m"] is None
    assert np.isnan(read(tmp_path / "empty", "extracted")).all()


def test_nodata_nonfinite_and_negative_height(raster, tmp_path):
    dem = raster("dem.tif", [[-9999, -100, -100, -100, np.nan, 0, 0]], nodata=-9999)
    dsm = raster("dsm.tif", [[50, -9999, np.inf, -110, 0, 0, 50]], nodata=-9999)
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, threshold=30)
    np.testing.assert_array_equal(read(out, "mask"), [[255, 255, 255, 0, 255, 0, 1]])
    assert report["valid_pixels"] == 3
    assert report["negative_pixels"] == 1
    assert read(out, "height")[0, 3] == -10


def test_10m_dem_on_half_meter_dsm_grid(raster, tmp_path):
    dem = raster("dem.tif", np.full((3, 3), -100), transform=from_origin(500000, 4100000, 10, 10))
    surface = np.full((60, 60), -100.0)
    surface[10:30, 10:30] = -50
    dsm = raster("dsm.tif", surface, transform=from_origin(500000, 4100000, 0.5, 0.5))
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out)
    assert report["resampling"] == "bilinear"
    assert report["selected_pixels"] == 400
    assert report["selected_area_m2"] == 100
    assert report["valid_pixels"] == 3600
    assert report["negative_pixels"] == 0
    assert report["max_height_m"] == pytest.approx(50)
    with rasterio.open(out / "height.tif") as result, rasterio.open(dsm) as source:
        assert result.shape == source.shape
        assert result.transform == source.transform
        assert result.crs == source.crs


def test_interpolation_roundoff_does_not_cross_threshold(raster, tmp_path):
    dem = raster("dem.tif", np.full((3, 3), -100), transform=from_origin(500000, 4100000, 10, 10))
    dsm = raster("dsm.tif", np.full((60, 60), -70), transform=from_origin(500000, 4100000, 0.5, 0.5))
    assert process_rasters(dem, dsm, tmp_path / "strict")["selected_pixels"] == 0
    assert process_rasters(dem, dsm, tmp_path / "inclusive", inclusive=True)["selected_pixels"] == 3600


def test_half_meter_dem_on_10m_dsm_uses_area_average(raster, tmp_path):
    terrain = np.tile([-100.0, -80.0], (60, 30))
    dem = raster("dem.tif", terrain, transform=from_origin(500000, 4100000, 0.5, 0.5))
    dsm = raster("dsm.tif", np.full((3, 3), -40), transform=from_origin(500000, 4100000, 10, 10))
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, threshold=49)
    assert report["resampling"] == "average"
    assert report["selected_pixels"] == 9
    np.testing.assert_allclose(read(out, "height"), 50, atol=1e-5)


def test_partial_overlap_does_not_use_zero_as_missing_data(raster, tmp_path):
    dem = raster("dem.tif", np.zeros((4, 2)), nodata=None)
    dsm = raster("dsm.tif", np.full((4, 4), 50), nodata=None)
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out)
    assert report["valid_pixels"] == 8
    assert report["selected_pixels"] == 8
    np.testing.assert_array_equal(read(out, "mask")[:, 2:], 255)


def test_internal_validity_mask_survives_resampling(raster, tmp_path):
    dem = raster(
        "dem.tif",
        np.full((2, 2), -100),
        transform=from_origin(500000, 4100000, 10, 10),
        nodata=None,
        mask=[[0, 255], [255, 255]],
    )
    dsm = raster("dsm.tif", np.full((4, 4), -50), transform=from_origin(500000, 4100000, 5, 5))
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, resampling="nearest")
    assert report["valid_pixels"] == 12
    np.testing.assert_array_equal(read(out, "mask")[:2, :2], 255)


def test_different_crs(raster, tmp_path):
    dem = raster("dem.tif", np.full((100, 100), -100), transform=from_origin(500000, 4100000, 10, 10))
    bounds = transform_bounds("EPSG:32639", "EPSG:4326", 500100, 4099100, 500900, 4099900)
    dsm = raster("dsm.tif", np.full((30, 30), -50), crs="EPSG:4326", transform=from_bounds(*bounds, 30, 30))
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out)
    assert report["selected_pixels"] == 900
    assert report["selected_area_m2"] is None
    np.testing.assert_allclose(read(out, "height"), 50)


def test_scale_offset_and_different_vertical_units(raster, tmp_path):
    dem = raster("dem.tif", [[100, 100]], scale=0.5, offset=-150)
    dsm = raster("dsm.tif", [[-50 / 0.3048, -100 / 0.3048]])
    out = tmp_path / "out"
    report = process_rasters(dem, dsm, out, dsm_unit="ft")
    np.testing.assert_allclose(read(out, "height"), [[50, 0]], atol=1e-5)
    assert report["selected_pixels"] == 1


def test_no_overlap_is_reported(raster, tmp_path):
    dem = raster("dem.tif", [[-100]])
    dsm = raster("dsm.tif", [[50]], transform=from_origin(510000, 4200000, 1, 1))
    with pytest.raises(ProcessingError, match="مشترک"):
        process_rasters(dem, dsm, tmp_path / "out")


@pytest.mark.parametrize("threshold", [-1, float("nan"), float("inf")])
def test_invalid_threshold(raster, tmp_path, threshold):
    dem = raster("dem.tif", [[0]])
    with pytest.raises(ProcessingError, match="آستانه"):
        process_rasters(dem, dem, tmp_path / "out", threshold=threshold)


@pytest.mark.parametrize("option", ["missing_crs", "multiband", "corrupt"])
def test_invalid_raster(raster, tmp_path, option):
    good = raster("dsm.tif", [[50, 50], [50, 50]])
    if option == "missing_crs":
        bad = raster("dem.tif", [[0, 0], [0, 0]], crs=None)
    elif option == "multiband":
        bad = raster("dem.tif", np.zeros((3, 2, 2)))
    else:
        bad = tmp_path / "dem.tif"
        bad.write_text("not a GeoTIFF")
    with pytest.raises(ProcessingError):
        process_rasters(bad, good, tmp_path / "out")
