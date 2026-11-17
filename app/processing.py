"""Windowed, datum-independent above-ground extraction on the native DSM grid."""

import json
import math
import shutil
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.errors import RasterioError, WindowError
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.windows import Window

from app.aoi import load_aoi
from app.errors import ProcessingError
from app.resources import resource_budget

UNIT_FACTORS = {"m": 1.0, "ft": 0.3048, "us-ft": 1200 / 3937}


def validate_raster(src, label: str) -> None:
    if src.driver != "GTiff" or src.count != 1:
        raise ProcessingError(f"فایل {label} باید یک GeoTIFF تک‌باند ارتفاعی باشد.")
    if src.crs is None:
        raise ProcessingError(f"سیستم مختصات فایل {label} مشخص نیست؛ ابتدا CRS صحیح را به فایل اختصاص دهید.")
    if np.dtype(src.dtypes[0]).kind not in "fiu":
        raise ProcessingError(f"نوع دادهٔ ارتفاعی {label} پشتیبانی نمی‌شود.")
    if not all(math.isfinite(x) for x in (*src.transform[:6], src.scales[0], src.offsets[0])):
        raise ProcessingError(f"مشخصات مکانی یا ضریب ارتفاع فایل {label} معتبر نیست.")
    if abs(src.transform.determinant) == 0:
        raise ProcessingError(f"اندازهٔ پیکسل فایل {label} معتبر نیست.")


def raster_info(src, unit: str, filename: str) -> dict:
    return {
        "filename": filename,
        "width": src.width,
        "height": src.height,
        "crs": src.crs.to_string(),
        "resolution": list(src.res),
        "horizontal_unit": src.crs.linear_units if src.crs.is_projected else "degree",
        "vertical_unit": unit,
        "scale": src.scales[0],
        "offset": src.offsets[0],
        "transform": list(src.transform)[:6],
    }


def choose_resampling(dem, dsm, method: str) -> tuple[str, bool]:
    aligned = dem.crs == dsm.crs and dem.transform == dsm.transform and dem.shape == dsm.shape
    if aligned:
        return "none", True
    if method != "auto":
        return method, False
    transform = dem.transform
    if dem.crs != dsm.crs:
        transform, _, _ = calculate_default_transform(dem.crs, dsm.crs, dem.width, dem.height, *dem.bounds)
    source_area = abs(transform.determinant)
    target_area = abs(dsm.transform.determinant)
    return ("average" if target_area > source_area * 1.01 else "bilinear"), False


def _preview(path: Path, out: Path, kind: str, max_size: int, *, window=None, validity=None) -> None:
    with rasterio.open(path) as src:
        width, height = (window.width, window.height) if window else (src.width, src.height)
        scale = min(1, max_size / max(width, height))
        shape = (max(1, round(height * scale)), max(1, round(width * scale)))
        sample = src.read(1, window=window, out_shape=shape, masked=True, resampling=Resampling.nearest)
        values = np.asarray(sample.filled(0), dtype="float32")
        valid = ~np.ma.getmaskarray(sample) & np.isfinite(values)
        if validity:
            with rasterio.open(validity) as mask_src:
                valid &= mask_src.read_masks(1, out_shape=shape, resampling=Resampling.nearest) != 0
        colors = np.zeros((*shape, 4), dtype="uint8")
        if kind == "mask":
            colors[..., :3] = (42, 224, 166)
            colors[..., 3] = np.where(valid & (values == 1), 240, 0)
        else:
            actual = values[valid]
            low, high = np.percentile(actual, (2, 98)) if actual.size else (0, 1)
            norm = np.clip((values - low) / max(float(high - low), 1e-6), 0, 1)
            stops = np.array(
                [[24, 47, 57], [35, 86, 97], [44, 140, 137], [119, 183, 141], [220, 220, 156]], dtype=float
            )
            if kind == "height":
                norm = np.clip(values / max(float(high), 1), 0, 1)
                stops = np.array(
                    [[22, 40, 52], [27, 95, 111], [37, 177, 160], [162, 210, 132], [246, 220, 119]],
                    dtype=float,
                )
            for channel in range(3):
                colors[..., channel] = np.interp(norm, np.linspace(0, 1, len(stops)), stops[:, channel])
            colors[..., 3] = valid * 255
        Image.fromarray(colors).save(out, optimize=True)


def process_rasters(
    dem_path: Path,
    dsm_path: Path,
    output: Path,
    *,
    threshold: float = 30,
    inclusive: bool = False,
    dem_unit: str = "m",
    dsm_unit: str = "m",
    resampling: str = "auto",
    progress: Callable[[int, str], None] = lambda *_: None,
    filenames: tuple[str, str] = ("dem.tif", "dsm.tif"),
    aoi_directory: Path | None = None,
) -> dict:
    if not math.isfinite(threshold) or threshold < 0:
        raise ProcessingError("آستانه باید عددی متناهی و نامنفی بر حسب متر باشد.")
    if dem_unit not in UNIT_FACTORS or dsm_unit not in UNIT_FACTORS:
        raise ProcessingError("واحد ارتفاع نامعتبر است.")
    if resampling not in ("auto", "bilinear", "average", "nearest"):
        raise ProcessingError("روش بازنمونه‌برداری نامعتبر است.")
    started = time.monotonic()
    budget = resource_budget()
    output.mkdir(parents=True, exist_ok=True)
    progress(3, "بررسی فایل‌ها و شبکهٔ مکانی")
    try:
        with (
            rasterio.Env(
                GDAL_NUM_THREADS=str(budget["cpu_threads"]),
                GDAL_CACHEMAX=budget["gdal_cache_mb"] * 1024**2,
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            ),
            ExitStack() as stack,
        ):
            # Restrict the driver so a renamed VRT cannot access arbitrary server paths/URLs.
            dem = stack.enter_context(rasterio.open(dem_path, driver="GTiff"))
            dsm = stack.enter_context(rasterio.open(dsm_path, driver="GTiff"))
            validate_raster(dem, "DEM")
            validate_raster(dsm, "DSM")
            aoi = load_aoi(aoi_directory, dsm.crs) if aoi_directory else None
            source_window = Window(0, 0, dsm.width, dsm.height)
            if aoi:
                try:
                    source_window = geometry_window(dsm, [aoi.geometry])
                except WindowError as exc:
                    raise ProcessingError("محدودهٔ شیپ‌فایل با DSM هم‌پوشانی ندارد.") from exc
            width, height = int(source_window.width), int(source_window.height)
            output_transform = dsm.window_transform(source_window)
            method, aligned = choose_resampling(dem, dsm, resampling)
            # Three uncompressed rasters need 9 bytes/pixel. ZIP is streamed on demand.
            required = width * height * 9 + 32 * 1024**2
            if shutil.disk_usage(output).free < required:
                raise ProcessingError(
                    f"برای خروجی این محدوده حدود {required / 1024**3:.1f} گیگابایت فضای آزاد لازم است؛ "
                    "پوشهٔ خروجی را روی درایو بزرگ‌تر انتخاب کنید یا محدوده را کوچک‌تر کنید."
                )
            ground = (
                dem
                if aligned
                else stack.enter_context(
                    WarpedVRT(
                        dem,
                        crs=dsm.crs,
                        transform=dsm.transform,
                        width=dsm.width,
                        height=dsm.height,
                        resampling=Resampling[method],
                        dtype="float64",
                        nodata=float("nan"),
                        warp_mem_limit=budget["warp_memory_mb"],
                        NUM_THREADS=str(budget["cpu_threads"]),
                        UNIFIED_SRC_NODATA="YES",
                    )
                )
            )
            info = {
                "dem": raster_info(dem, dem_unit, filenames[0]),
                "dsm": raster_info(dsm, dsm_unit, filenames[1]),
            }
            profile = {
                "driver": "GTiff",
                "width": width,
                "height": height,
                "count": 1,
                "crs": dsm.crs,
                "transform": output_transform,
                "tiled": True,
                "blockxsize": 512,
                "blockysize": 512,
                "compress": "deflate",
                "BIGTIFF": "IF_SAFER",
                "NUM_THREADS": str(budget["cpu_threads"]),
            }
            writers = {}
            for name in ("height", "extracted", "mask"):
                is_mask = name == "mask"
                writers[name] = stack.enter_context(
                    rasterio.open(
                        output / f"{name}.tif",
                        "w",
                        **profile,
                        dtype="uint8" if is_mask else "float32",
                        nodata=255 if is_mask else float("nan"),
                        predictor=1 if is_mask else 3,
                    )
                )
                writers[name].set_band_description(
                    1,
                    {
                        "height": "DSM minus DEM, metres above local ground",
                        "extracted": "Selected height above local ground in metres",
                        "mask": "1=selected, 0=not selected, 255=invalid or no overlap",
                    }[name],
                )
                if not is_mask:
                    writers[name].set_band_unit(1, "m")
                writers[name].update_tags(
                    threshold_m=threshold,
                    comparison=">=" if inclusive else ">",
                    dem_resampling=method,
                    vertical_reference="local_ground",
                )
            valid_count = selected_count = negative_count = inside_count = 0
            selected_sum = 0.0
            selected_min, selected_max = math.inf, -math.inf
            histogram_edges = np.array([0, 5, 10, 20, 30, 50, 75, 100, np.inf])
            histogram = np.zeros(8, dtype="int64")
            size = budget["block_size"]
            total = math.ceil(width / size) * math.ceil(height / size)
            completed = 0
            for row in range(0, height, size):
                for col in range(0, width, size):
                    window = Window(col, row, min(size, width - col), min(size, height - row))
                    read_window = Window(
                        col + source_window.col_off, row + source_window.row_off, window.width, window.height
                    )
                    shape = (int(window.height), int(window.width))
                    inside = (
                        geometry_mask(
                            [aoi.geometry],
                            out_shape=shape,
                            transform=dsm.window_transform(read_window),
                            invert=True,
                        )
                        if aoi
                        else np.ones(shape, dtype=bool)
                    )
                    inside_count += int(inside.sum())
                    if not inside.any():
                        for name in ("height", "extracted"):
                            writers[name].write(np.full(shape, np.nan, dtype="float32"), 1, window=window)
                        writers["mask"].write(np.full(shape, 255, dtype="uint8"), 1, window=window)
                        completed += 1
                        progress(5 + round(78 * completed / total), "برش محدوده و استخراج ارتفاع")
                        continue
                    terrain = ground.read(1, window=read_window, masked=True, out_dtype="float64")
                    surface = dsm.read(1, window=read_window, masked=True, out_dtype="float64")
                    terrain_values = (terrain.data * dem.scales[0] + dem.offsets[0]) * UNIT_FACTORS[dem_unit]
                    surface_values = (surface.data * dsm.scales[0] + dsm.offsets[0]) * UNIT_FACTORS[dsm_unit]
                    heights = surface_values - terrain_values
                    zero_epsilon = (
                        np.finfo("float64").eps * 8 * (np.abs(surface_values) + np.abs(terrain_values) + 1)
                    )
                    heights[np.isfinite(heights) & (np.abs(heights) <= zero_epsilon)] = 0
                    valid = (
                        ~np.ma.getmaskarray(terrain)
                        & ~np.ma.getmaskarray(surface)
                        & np.isfinite(terrain_values)
                        & np.isfinite(surface_values)
                        & np.isfinite(heights)
                        & (np.abs(heights) <= np.finfo("float32").max)
                        & inside
                    )
                    # Bilinear warping can leave ~1e-14 noise even on constant terrain.
                    # Bound float64 roundoff so exact threshold values stay on the boundary.
                    epsilon = (
                        np.finfo("float64").eps
                        * 8
                        * (np.abs(surface_values) + np.abs(terrain_values) + abs(threshold) + 1)
                    )
                    delta = heights - threshold
                    selected = valid & (delta >= -epsilon if inclusive else delta > epsilon)
                    valid_count += int(valid.sum())
                    selected_count += int(selected.sum())
                    negative_count += int((valid & (heights < 0)).sum())
                    values = heights[selected]
                    if values.size:
                        selected_sum += float(values.sum())
                        selected_min = min(selected_min, float(values.min()))
                        selected_max = max(selected_max, float(values.max()))
                    histogram += np.histogram(heights[valid & (heights >= 0)], bins=histogram_edges)[0]
                    writers["height"].write(
                        np.where(valid, heights, np.nan).astype("float32"), 1, window=window
                    )
                    writers["extracted"].write(
                        np.where(selected, heights, np.nan).astype("float32"), 1, window=window
                    )
                    writers["mask"].write(np.where(valid, selected, 255).astype("uint8"), 1, window=window)
                    completed += 1
                    progress(5 + round(78 * completed / total), "هم‌مکان‌سازی و استخراج ارتفاع")
            if not valid_count:
                raise ProcessingError(
                    "در محدودهٔ انتخاب‌شده پیکسل معتبر مشترک وجود ندارد. محدوده، CRS و NoData را بررسی کنید."
                )
            pixel_area = None
            if dsm.crs.is_projected:
                pixel_area = abs(dsm.transform.determinant) * dsm.crs.linear_units_factor[1] ** 2
            warnings = [
                "ارتفاع نسبی به‌تنهایی ساختمان را از درخت یا سایر عوارض بلند تفکیک نمی‌کند.",
                "دو ورودی باید مبنای ارتفاعی یکسان داشته باشند؛ تبدیل مبنای ارتفاعی انجام نمی‌شود.",
            ]
            if not aligned:
                warnings.append(
                    "DEM روی شبکهٔ DSM بازنمونه‌برداری شد؛ این تبدیل دقت واقعی دادهٔ مبنا را افزایش نمی‌دهد."
                )
            if negative_count:
                warnings.append(
                    "برخی مقادیر DSM پایین‌تر از DEM هستند؛ این مقادیر در nDSM حفظ و از انتخاب نامنفی حذف شده‌اند."
                )
            if aoi:
                warnings.append(
                    "خروجی به کادر محدوده برش خورده است؛ فقط پیکسل‌هایی که مرکز آن‌ها داخل چندضلعی است معتبرند. حفره‌ها و بیرون مرز NoData هستند."
                )
            if valid_count < inside_count:
                warnings.append("پیکسل‌های NoData و نواحی فاقد پوشش مشترک در خروجی نامعتبر هستند.")
            if pixel_area is None:
                warnings.append("محاسبهٔ مساحت فقط برای شبکهٔ خروجی با سیستم مختصات تصویرشده انجام می‌شود.")
            report = {
                "threshold_m": threshold,
                "comparison": ">=" if inclusive else ">",
                "formula": "DSM - DEM",
                "resampling": method,
                "aligned": aligned,
                "inputs": info,
                "aoi": aoi.info if aoi else None,
                "grid": {
                    "crs": dsm.crs.to_string(),
                    "width": width,
                    "height": height,
                    "resolution": list(dsm.res),
                    "horizontal_unit": info["dsm"]["horizontal_unit"],
                    "bounds": list(rasterio.transform.array_bounds(height, width, output_transform)),
                    "transform": list(output_transform)[:6],
                },
                "valid_pixels": valid_count,
                "selected_pixels": selected_count,
                "invalid_pixels": width * height - valid_count,
                "aoi_pixels": inside_count,
                "outside_aoi_pixels": width * height - inside_count,
                "negative_pixels": negative_count,
                "selected_percent": 100 * selected_count / valid_count,
                "selected_area_m2": selected_count * pixel_area if pixel_area is not None else None,
                "min_height_m": selected_min if selected_count else None,
                "max_height_m": selected_max if selected_count else None,
                "mean_height_m": selected_sum / selected_count if selected_count else None,
                "histogram": {
                    "labels": ["0–5", "5–10", "10–20", "20–30", "30–50", "50–75", "75–100", "100+"],
                    "counts": histogram.tolist(),
                },
                "resources": budget,
                "warnings": warnings,
            }
        progress(87, "ساخت پیش‌نمایش و خروجی‌ها")
        for name, kind in (("height", "height"), ("mask", "mask")):
            _preview(output / f"{name}.tif", output / f"{name}.png", kind, budget["preview_max_size"])
        _preview(
            dsm_path,
            output / "surface.png",
            "surface",
            budget["preview_max_size"],
            window=source_window,
            validity=output / "height.tif",
        )
        if aoi:
            with Image.open(output / "height.png") as preview:
                pw, ph = preview.size
            transform = output_transform * rasterio.Affine.scale(width / pw, height / ph)
            inside = geometry_mask([aoi.geometry], out_shape=(ph, pw), transform=transform, invert=True)
            padded = np.pad(inside, 1, constant_values=False)
            interior = inside & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
            border = inside & ~interior
            rgba = np.zeros((ph, pw, 4), dtype="uint8")
            rgba[border] = [248, 198, 92, 255]
            Image.fromarray(rgba).save(output / "aoi.png")
        else:
            Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(output / "aoi.png")
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        progress(100, "پردازش کامل شد")
        return report
    except RasterioError as exc:
        raise ProcessingError(
            "خواندن یا تبدیل فایل ارتفاعی ممکن نشد؛ GeoTIFF، CRS و سلامت فایل را بررسی کنید."
        ) from exc
