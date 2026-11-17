from pathlib import Path

import numpy as np
import pytest
import rasterio
import shapefile
from rasterio.crs import CRS
from rasterio.transform import from_origin


@pytest.fixture
def raster(tmp_path):
    def create(name, data, *, transform=None, crs="EPSG:32639", nodata=-9999, scale=1, offset=0, mask=None):
        path = tmp_path / name
        array = np.asarray(data, dtype="float64")
        profile = {
            "driver": "GTiff",
            "width": array.shape[-1],
            "height": array.shape[-2],
            "count": 1 if array.ndim == 2 else array.shape[0],
            "dtype": "float64",
            "crs": crs,
            "transform": transform or from_origin(500000, 4100000, 1, 1),
            "nodata": nodata,
        }
        with rasterio.open(path, "w", **profile) as dst:
            if array.ndim == 2:
                dst.write(array, 1)
            else:
                dst.write(array)
            dst.scales = (scale,) * dst.count
            dst.offsets = (offset,) * dst.count
            if mask is not None:
                dst.write_mask(np.asarray(mask, dtype="uint8"))
        return Path(path)

    return create


@pytest.fixture
def aoi(tmp_path):
    counter = 0

    def create(polygons, *, crs="EPSG:32639", zipped=False):
        nonlocal counter
        from zipfile import ZipFile

        from shapely.geometry.polygon import orient

        counter += 1
        directory = tmp_path / f"aoi-{counter}"
        directory.mkdir()
        with shapefile.Writer(str(directory / "boundary"), shapeType=shapefile.POLYGON) as writer:
            writer.field("ID", "N")
            for i, polygon in enumerate(polygons):
                polygon = orient(polygon, sign=-1)
                writer.poly(
                    [list(polygon.exterior.coords), *[list(ring.coords) for ring in polygon.interiors]]
                )
                writer.record(i)
        (directory / "boundary.prj").write_text(CRS.from_user_input(crs).to_wkt())
        if zipped:
            archive_dir = tmp_path / f"zipped-{counter}"
            archive_dir.mkdir()
            with ZipFile(archive_dir / "area.zip", "w") as archive:
                for path in directory.iterdir():
                    archive.write(path, "folder/" + path.name)
            return archive_dir
        return directory

    return create
