"""Windowed DSM-derived terrain: multiscale opening and local candidate-ground planes.

This is an estimate, not a bare-earth measurement or a semantic classifier. A DSM
containing only a roof is indistinguishable from a flat patch of bare terrain.
"""

import math
import shutil
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, transform
from rasterio.windows import Window
from scipy import ndimage

from app.aoi import load_aoi
from app.errors import ProcessingError
from app.resources import resource_budget

PROFILES = {"urban": (0.08, 3.0), "mixed": (0.2, 6.0), "steep": (0.4, 12.0)}
TILE_SIZE = 512
LIMITATION = (
    "زمین از خود DSM تخمین زده شده و اندازه‌گیری مستقیم زمین نیست. زیر ساختمان‌های پهن، "
    "پوشش متراکم، لبهٔ فایل و زمین ناهموار ممکن است ارتفاع کم یا زیاد برآورد شود. "
    "اگر زمین در DSM دیده نشود، ارتفاع واقعی از این فایل به‌تنهایی قابل تعیین نیست."
)


def validate_options(profile, max_width, grid_size):
    if profile not in PROFILES:
        raise ProcessingError("نوع زمین معتبر نیست.")
    if not math.isfinite(max_width) or not 2 <= max_width <= 5000:
        raise ProcessingError("عرض بزرگ‌ترین عارضه باید بین ۲ و ۵۰۰۰ متر باشد.")
    if not math.isfinite(grid_size) or not 0.25 <= grid_size <= 100:
        raise ProcessingError("گام تخمین زمین باید بین ۰٫۲۵ و ۱۰۰ متر باشد.")


def _grid(src, requested_step):
    if src.crs.is_projected:
        factor = src.crs.linear_units_factor[1]
        fx, fy = [max(1, math.ceil(requested_step / (res * factor))) for res in src.res]
        affine = src.transform * rasterio.Affine.scale(fx, fy)
        return src.crs, affine, math.ceil(src.width / fx), math.ceil(src.height / fy)
    longitude, latitude = transform(
        src.crs,
        "EPSG:4326",
        [(src.bounds.left + src.bounds.right) / 2],
        [(src.bounds.bottom + src.bounds.top) / 2],
    )
    lon, lat = longitude[0], latitude[0]
    if not math.isfinite(lon + lat):
        raise ProcessingError("تبدیل مختصات DSM به شبکهٔ متری ممکن نشد.")
    if lat > 84:
        crs = rasterio.CRS.from_epsg(3413)
    elif lat < -80:
        crs = rasterio.CRS.from_epsg(3031)
    else:
        zone = max(1, min(60, int((lon + 180) / 6) + 1))
        crs = rasterio.CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)
    native, _, _ = calculate_default_transform(src.crs, crs, src.width, src.height, *src.bounds)
    step = max(requested_step, abs(native.a), abs(native.e))
    affine, width, height = calculate_default_transform(
        src.crs, crs, src.width, src.height, *src.bounds, resolution=step
    )
    return crs, affine, width, height


def _radii(max_width, dx, dy):
    end = max(1, math.ceil(max_width / (2 * min(dx, dy))))
    values, radius = [], 1
    while radius < end:
        values.append(radius * min(dx, dy))
        radius *= 2
    values.append(max_width / 2 + min(dx, dy))
    return [(max(1, math.ceil(m / dy)), max(1, math.ceil(m / dx))) for m in values]


def estimate_tile(values, *, dx, dy, max_width, profile):
    """Inputs/outputs are bounded halo tiles; all arithmetic is in metres."""
    valid = np.isfinite(values)
    if not valid.any():
        return np.full(values.shape, np.nan, "float32"), np.zeros(values.shape, "uint8")
    # Centre Z to make fitting independent of the sea-level offset.
    reference = float(np.median(values[valid]))
    z = values - reference
    if not valid.all():
        indices = ndimage.distance_transform_edt(~valid, return_distances=False, return_indices=True)
        filled = z[tuple(indices)]
    else:
        filled = z
    candidates = valid.copy()
    slope, cap = PROFILES[profile]
    for ry, rx in _radii(max_width, dx, dy):
        size = (2 * ry + 1, 2 * rx + 1)
        opened = ndimage.maximum_filter(
            ndimage.minimum_filter(filled, size=size, mode="nearest"), size=size, mode="nearest"
        )
        tolerance = min(cap, 0.5 + slope * max(ry * dy, rx * dx))
        candidates &= filled - opened <= tolerance
    ry, rx = max(1, math.ceil(max_width / dy)), max(1, math.ceil(max_width / dx))
    box = (2 * ry + 1, 2 * rx + 1)
    weights = candidates.astype("float64")

    def mean(a):
        return ndimage.uniform_filter(a, size=box, mode="constant", cval=0)

    mass = mean(weights)
    denominator = np.maximum(mass, 1e-30)
    y, x = np.indices(values.shape, dtype="float64")
    x *= dx
    y *= dy
    # Enough moments for a local least-squares plane. No global triangulation,
    # point cloud, or array whose size grows with the complete input raster.
    mx, my = mean(weights * x) / denominator, mean(weights * y) / denominator
    seed_z = np.where(candidates, z, 0)
    mz = mean(seed_z) / denominator
    xx = np.maximum(0, mean(weights * x * x) / denominator - mx * mx)
    yy = np.maximum(0, mean(weights * y * y) / denominator - my * my)
    xy = mean(weights * x * y) / denominator - mx * my
    xz = mean(seed_z * x) / denominator - mx * mz
    yz = mean(seed_z * y) / denominator - my * mz
    determinant = xx * yy - xy * xy
    full_rank = determinant > np.maximum(xx * yy * 1e-8, 1e-8)
    safe_det = np.where(full_rank, determinant, 1)
    sx = np.where(full_rank, (xz * yy - yz * xy) / safe_det, 0)
    sy = np.where(full_rank, (yz * xx - xz * xy) / safe_det, 0)
    # A line of support can still give a 1D estimate, but is labelled limited.
    sx = np.where(~full_rank & (xx > yy) & (xx > 1e-8), xz / np.maximum(xx, 1e-8), sx)
    sy = np.where(~full_rank & (yy >= xx) & (yy > 1e-8), yz / np.maximum(yy, 1e-8), sy)
    fitted = mz + sx * (x - mx) + sy * (y - my)
    supported = mass * box[0] * box[1] >= 8 - 1e-6
    terrain = np.where(mass > 1e-10, fitted, opened)
    terrain = np.where(candidates, z, np.minimum(terrain, z)) + reference
    quality = np.where(candidates, 1, np.where(supported & full_rank, 2, 3)).astype("uint8")
    quality[~valid] = 0
    terrain[~valid] = np.nan
    return terrain.astype("float32"), quality


def build_ground(
    dsm_path: Path,
    output: Path,
    *,
    dsm_unit="m",
    profile="urban",
    max_width=200.0,
    grid_size=2.0,
    aoi_directory=None,
    progress=lambda *_: None,
):
    from app.processing import UNIT_FACTORS, validate_raster

    validate_options(profile, max_width, grid_size)
    if dsm_unit not in UNIT_FACTORS:
        raise ProcessingError("واحد ارتفاع نامعتبر است.")
    budget = resource_budget()
    # Bound stencil memory even for very large objects; extraction stays native.
    step = max(grid_size, max_width / 128)
    output.mkdir(parents=True, exist_ok=True)
    with (
        rasterio.Env(
            GDAL_NUM_THREADS=str(budget["cpu_threads"]),
            GDAL_CACHEMAX=budget["gdal_cache_mb"] * 1024**2,
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        ),
        ExitStack() as stack,
    ):
        src = stack.enter_context(rasterio.open(dsm_path, driver="GTiff"))
        validate_raster(src, "DSM")
        crs, affine, width, height = _grid(src, step)
        grid = stack.enter_context(
            WarpedVRT(
                src,
                crs=crs,
                transform=affine,
                width=width,
                height=height,
                resampling=Resampling.average,
                dtype="float64",
                nodata=float("nan"),
                warp_mem_limit=budget["warp_memory_mb"],
                NUM_THREADS=str(budget["cpu_threads"]),
            )
        )
        region = Window(0, 0, width, height)
        if aoi_directory:
            aoi = load_aoi(aoi_directory, crs)
            try:
                region = geometry_window(grid, [aoi.geometry], pad_x=2, pad_y=2)
            except rasterio.errors.WindowError as exc:
                raise ProcessingError("محدودهٔ شیپ‌فایل با DSM هم‌پوشانی ندارد.") from exc
        cols, rows = int(region.width), int(region.height)
        required = cols * rows * 5 + 32 * 1024**2
        if shutil.disk_usage(output).free < required:
            raise ProcessingError("فضای دیسک برای ذخیرهٔ زمین تخمینی کافی نیست؛ پوشه یا محدوده را تغییر دهید.")
        options = {
            "driver": "GTiff",
            "crs": crs,
            "transform": grid.window_transform(region),
            "width": cols,
            "height": rows,
            "count": 1,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
            "BIGTIFF": "IF_SAFER",
            "NUM_THREADS": str(budget["cpu_threads"]),
        }
        terrain = stack.enter_context(
            rasterio.open(
                output / "ground.tif", "w", **options, dtype="float32", nodata=float("nan"), predictor=3
            )
        )
        quality = stack.enter_context(
            rasterio.open(output / "ground_quality.tif", "w", **options, dtype="uint8", nodata=0)
        )
        terrain.set_band_description(1, "Estimated local terrain from DSM, NOT measured bare earth")
        terrain.set_band_unit(1, "m")
        quality.set_band_description(1, "0=invalid, 1=candidate ground, 2=local plane, 3=limited support")
        terrain.update_tags(
            method="multiscale_opening_local_planes_v1",
            profile=profile,
            max_object_width_m=max_width,
            ground_is_estimated="YES",
        )
        factor = crs.linear_units_factor[1]
        dx, dy = (res * factor for res in grid.res)
        rmax_y, rmax_x = _radii(max_width, dx, dy)[-1]
        halo_y = 2 * rmax_y + math.ceil(max_width / dy) + 2
        halo_x = 2 * rmax_x + math.ceil(max_width / dx) + 2
        size = min(TILE_SIZE, 256 if budget["memory_mb"] < 2048 else TILE_SIZE)
        bytes_per_tile = (size + 2 * halo_y) * (size + 2 * halo_x) * 200
        workers = max(1, min(budget["cpu_threads"], budget["memory_mb"] * 1024**2 // 4 // bytes_per_tile))
        executor = stack.enter_context(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ground"))
        pending = deque()
        counts = np.zeros(4, dtype="int64")
        total = math.ceil(cols / size) * math.ceil(rows / size)
        completed = 0

        def write_one():
            nonlocal completed
            future, window, crop = pending.popleft()
            heights, codes = future.result()
            heights, codes = heights[crop], codes[crop]
            terrain.write(heights, 1, window=window)
            quality.write(codes, 1, window=window)
            counts[:] += np.bincount(codes.ravel(), minlength=4)
            completed += 1
            progress(round(100 * completed / total), "تخمین سطح زمین از DSM و محیط اطراف عوارض")

        for row in range(0, rows, size):
            for col in range(0, cols, size):
                window = Window(col, row, min(size, cols - col), min(size, rows - row))
                x, y = int(region.col_off) + col, int(region.row_off) + row
                left, top = max(0, x - halo_x), max(0, y - halo_y)
                right, bottom = (
                    min(width, x + int(window.width) + halo_x),
                    min(height, y + int(window.height) + halo_y),
                )
                sample = grid.read(1, window=Window(left, top, right - left, bottom - top), masked=True)
                values = (sample.filled(np.nan) * src.scales[0] + src.offsets[0]) * UNIT_FACTORS[dsm_unit]
                crop = (
                    slice(y - top, y - top + int(window.height)),
                    slice(x - left, x - left + int(window.width)),
                )
                pending.append(
                    (
                        executor.submit(
                            estimate_tile, values, dx=dx, dy=dy, max_width=max_width, profile=profile
                        ),
                        window,
                        crop,
                    )
                )
                # Only one input and one result per worker can be in flight.
                if len(pending) >= workers:
                    write_one()
        while pending:
            write_one()
    return {
        "method": "multiscale_opening_local_planes_v1",
        "estimated": True,
        "profile": profile,
        "max_object_width_m": max_width,
        "requested_grid_m": grid_size,
        "grid_resolution_m": [dx, dy],
        "crs": crs.to_string(),
        "worker_threads": workers,
        "quality_codes": {
            "0": "invalid",
            "1": "candidate ground",
            "2": "local plane",
            "3": "limited support",
        },
        "quality_pixels": counts.tolist(),
        "limitation": LIMITATION,
        "quality_note": "Support codes are internal model diagnostics, not confidence or verified ground.",
        "context_outside_aoi": True,
    }
