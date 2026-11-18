"""Small input previews: use overviews, or sample a bounded number of native blocks."""

import math
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.windows import Window

from app.errors import ProcessingError
from app.processing import validate_raster

MAX_SIDE = 768
SAMPLE_BUDGET = 64 * 1024**2
MAX_SAMPLES = 256


def build_preview(path: Path, directory: Path, progress=lambda *_: None):
    try:
        with (
            rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"),
            rasterio.open(path, driver="GTiff") as src,
        ):
            validate_raster(src, "ورودی")
            progress(10, "خواندن نمای کوچک فایل")
            overview = bool(src.overviews(1))
            sparse = not overview and src.width * src.height > 4_000_000
            if sparse:
                block_bytes = math.prod(src.block_shapes[0]) * np.dtype(src.dtypes[0]).itemsize
                cells = min(MAX_SAMPLES, max(1, SAMPLE_BUDGET // max(1, block_bytes)))
                width = min(src.width, cells, max(1, round(math.sqrt(cells * src.width / src.height))))
                height = min(src.height, max(1, cells // width))
                sample = np.ma.masked_all((height, width), dtype="float64")
                for row in range(height):
                    y = min(src.height - 1, int((row + 0.5) * src.height / height))
                    for col in range(width):
                        x = min(src.width - 1, int((col + 0.5) * src.width / width))
                        sample[row, col] = src.read(1, window=Window(x, y, 1, 1), masked=True)[0, 0]
                    progress(10 + int(75 * (row + 1) / height), "نمونه‌برداری سریع برای پیش‌نمایش")
            else:
                scale = min(1, MAX_SIDE / max(src.width, src.height))
                width, height = max(1, round(src.width * scale)), max(1, round(src.height * scale))
                sample = src.read(1, out_shape=(height, width), masked=True, resampling=Resampling.nearest)
            values = np.asarray(sample.data, dtype="float64") * src.scales[0] + src.offsets[0]
            valid = ~np.ma.getmaskarray(sample) & np.isfinite(values)
            actual = values[valid]
            lo, hi = np.percentile(actual, [2, 98]) if actual.size else (0, 1)
            if actual.size and hi <= lo:
                lo, hi = actual.min(), actual.max()
            norm = (
                np.clip((np.where(valid, values, lo) - lo) / float(hi - lo), 0, 1)
                if hi > lo
                else np.full(values.shape, 0.5)
            )
            colors = np.zeros((height, width, 4), dtype="uint8")
            stops = np.array([[24, 47, 57], [35, 86, 97], [44, 140, 137], [119, 183, 141], [220, 220, 156]])
            for channel in range(3):
                colors[..., channel] = np.interp(norm, np.linspace(0, 1, len(stops)), stops[:, channel])
            colors[..., 3] = valid * 255
            Image.fromarray(colors).save(directory / "preview.png")
            return {
                "width": src.width,
                "height": src.height,
                "crs": src.crs.to_string(),
                "resolution": list(src.res),
                "preview_width": width,
                "preview_height": height,
                "aspect": src.width / src.height,
                "sampled": sparse,
                "has_overviews": overview,
                "valid_samples": int(valid.sum()),
                "filename": path.name,
                "note": (
                    "نمای سریع نمونه‌برداری‌شده؛ فایل Overview ندارد و جزئیات کوچک ممکن است دیده نشوند."
                    if sparse
                    else "پیش‌نمایش ورودی؛ رنگ‌ها ارتفاع فایل را نشان می‌دهند، نه نتیجهٔ استخراج."
                ),
            }
    except RasterioError as exc:
        raise ProcessingError("پیش‌نمایش ساخته نشد؛ سلامت GeoTIFF و سیستم مختصات آن را بررسی کنید.") from exc
