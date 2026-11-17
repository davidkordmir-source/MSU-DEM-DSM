from zipfile import ZipFile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from shapely.geometry import Polygon, box

from app.aoi import load_aoi
from app.processing import ProcessingError, process_rasters


@pytest.mark.parametrize("zipped", [False, True])
def test_aoi_crops_to_dsm_grid(raster, aoi, tmp_path, zipped):
    dem = raster("dem.tif", np.full((6, 8), -100))
    dsm = raster("dsm.tif", np.full((6, 8), -50))
    area = aoi([box(500002, 4099996, 500006, 4099999)], zipped=zipped)
    report = process_rasters(dem, dsm, tmp_path / "out", aoi_directory=area)
    assert report["selected_pixels"] == 12
    assert report["grid"]["width"] == 4
    assert report["grid"]["height"] == 3
    assert report["aoi"]["source_crs"] == "EPSG:32639"
    with rasterio.open(tmp_path / "out/height.tif") as result:
        assert result.transform == from_origin(500002, 4099999, 1, 1)
        np.testing.assert_array_equal(result.read(1), 50)
    assert (tmp_path / "out/aoi.png").is_file()


def test_holes_are_nodata(raster, aoi, tmp_path):
    dem = raster("dem.tif", np.full((6, 8), -100))
    dsm = raster("dsm.tif", np.full((6, 8), -50))
    polygon = box(500001, 4099995, 500007, 4099999).difference(box(500003, 4099996, 500005, 4099998))
    report = process_rasters(dem, dsm, tmp_path / "out", aoi_directory=aoi([polygon]))
    assert report["selected_pixels"] == 20
    assert report["outside_aoi_pixels"] == 4
    with rasterio.open(tmp_path / "out/mask.tif") as result:
        np.testing.assert_array_equal(result.read(1)[1:3, 2:4], 255)


def test_overlapping_features_are_counted_once(raster, aoi, tmp_path):
    dem = raster("dem.tif", np.full((6, 8), -100))
    dsm = raster("dsm.tif", np.full((6, 8), -50))
    area = aoi([box(500000, 4099995, 500005, 4100000), box(500003, 4099995, 500008, 4100000)])
    report = process_rasters(dem, dsm, tmp_path / "out", aoi_directory=area)
    assert report["selected_pixels"] == 40
    assert report["aoi"]["feature_count"] == 2


def test_aoi_transforms_crs_and_respects_resolution(raster, aoi, tmp_path):
    dem = raster("dem.tif", np.full((3, 3), -100), transform=from_origin(500000, 4100000, 10, 10))
    dsm = raster("dsm.tif", np.full((60, 60), -50), transform=from_origin(500000, 4100000, 0.5, 0.5))
    bounds = transform_bounds("EPSG:32639", "EPSG:4326", 500005.1, 4099980.1, 500014.9, 4099989.9)
    area = aoi([box(*bounds)], crs="EPSG:4326")
    report = process_rasters(dem, dsm, tmp_path / "out", aoi_directory=area)
    assert report["selected_pixels"] == 400
    assert report["resampling"] == "bilinear"
    assert report["grid"]["resolution"] == [0.5, 0.5]


def test_area_outside_raster(raster, aoi, tmp_path):
    dem = raster("dem.tif", np.zeros((4, 4)))
    with pytest.raises(ProcessingError, match="هم‌پوشانی"):
        process_rasters(dem, dem, tmp_path / "out", aoi_directory=aoi([box(0, 0, 1, 1)]))


def test_missing_prj_and_invalid_polygon(aoi):
    area = aoi([box(0, 0, 1, 1)])
    (area / "boundary.prj").unlink()
    with pytest.raises(ProcessingError, match=".prj"):
        load_aoi(area, rasterio.CRS.from_epsg(32639))
    area = aoi([Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])])
    with pytest.raises(ProcessingError, match="هندسه"):
        load_aoi(area, rasterio.CRS.from_epsg(32639))


def test_zip_traversal_rejected(tmp_path):
    with ZipFile(tmp_path / "aoi.zip", "w") as archive:
        archive.writestr("../boundary.shp", b"bad")
    with pytest.raises(ProcessingError, match="ساختار"):
        load_aoi(tmp_path, rasterio.CRS.from_epsg(32639))


def test_large_dimensions_are_processed_with_aoi(aoi, tmp_path):
    paths = []
    for name, elevation in (("dem", -100), ("dsm", -50)):
        path = tmp_path / f"{name}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=262144,
            height=131072,
            count=1,
            dtype="float32",
            crs="EPSG:32639",
            transform=from_origin(500000, 4100000, 1, 1),
            nodata=-9999,
            tiled=True,
            compress="deflate",
            SPARSE_OK=True,
            BIGTIFF="YES",
        ) as out:
            out.write(np.array([[elevation]], dtype="float32"), 1, window=rasterio.windows.Window(0, 0, 1, 1))
        paths.append(path)
    report = process_rasters(
        *paths, tmp_path / "out", aoi_directory=aoi([box(500000, 4099999, 500001, 4100000)])
    )
    assert report["selected_pixels"] == 1
    assert report["inputs"]["dsm"]["width"] * report["inputs"]["dsm"]["height"] == 34_359_738_368
