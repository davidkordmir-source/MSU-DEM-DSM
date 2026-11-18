import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from app.errors import ProcessingError
from app.previews import MAX_SAMPLES, build_preview


def test_input_preview_before_extraction(raster, tmp_path):
    path = raster("surface.tif", [[-100, -50], [-9999, 1000]])
    info = build_preview(path, tmp_path)
    assert info["width"] == 2
    assert info["crs"] == "EPSG:32639"
    assert info["valid_samples"] == 3
    with Image.open(tmp_path / "preview.png") as image:
        assert image.getpixel((0, 1))[3] == 0
        assert image.getpixel((0, 0)) != image.getpixel((1, 1))


def test_huge_grid_preview_samples_without_full_read(tmp_path):
    path = tmp_path / "huge.tif"
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
        tiled=True,
        BIGTIFF="YES",
        SPARSE_OK=True,
        nodata=-9999,
    ) as target:
        target.write(np.array([[50]], dtype="float32"), 1, window=rasterio.windows.Window(0, 0, 1, 1))
    info = build_preview(path, tmp_path)
    assert info["sampled"]
    assert info["preview_width"] * info["preview_height"] <= MAX_SAMPLES
    assert info["valid_samples"] == 0  # Small feature is not in this deliberately coarse sample.


def test_overviews_are_used_for_large_inputs(tmp_path):
    path = tmp_path / "overview.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2048,
        height=2048,
        count=1,
        dtype="uint8",
        crs="EPSG:32639",
        transform=from_origin(500000, 4100000, 1, 1),
        tiled=True,
        compress="deflate",
    ) as target:
        target.write(np.full((2048, 2048), 50, dtype="uint8"), 1)
        target.build_overviews([4], rasterio.enums.Resampling.nearest)
    info = build_preview(path, tmp_path)
    assert info["has_overviews"] and not info["sampled"]
    assert info["preview_width"] == 768


def test_invalid_preview_is_actionable(tmp_path):
    path = tmp_path / "bad.tif"
    path.write_bytes(b"invalid")
    with pytest.raises(ProcessingError, match="پیش‌نمایش"):
        build_preview(path, tmp_path)
