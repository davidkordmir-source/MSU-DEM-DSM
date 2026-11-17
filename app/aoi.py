"""Read a polygon shapefile without extracting arbitrary ZIP contents to disk."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import numpy as np
import shapefile
from rasterio.crs import CRS
from rasterio.errors import CRSError
from rasterio.warp import transform_geom
from shapely import force_2d, union_all
from shapely.geometry import mapping, shape

from app.errors import ProcessingError

MAX_AOI_BYTES = 50 * 1024**2
MAX_UNPACKED_BYTES = 100 * 1024**2
EXTENSIONS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
REQUIRED = {".shp", ".shx", ".dbf", ".prj"}


@dataclass
class AreaOfInterest:
    geometry: dict
    info: dict


def _components(directory: Path) -> dict[str, bytes]:
    files = list(directory.iterdir())
    zips = [path for path in files if path.suffix.lower() == ".zip"]
    components = {}
    if zips:
        if len(files) != 1:
            raise ProcessingError("محدوده را به‌صورت یک ZIP یا مجموعهٔ فایل‌های شیپ‌فایل انتخاب کنید.")
        try:
            with ZipFile(zips[0]) as archive:
                entries = archive.infolist()
                if len(entries) > 512 or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
                    raise ProcessingError("حجم یا تعداد فایل‌های داخل ZIP محدوده بیش از حد مجاز است.")
                for entry in entries:
                    name = PurePosixPath(entry.filename.replace("\\", "/"))
                    if name.is_absolute() or ".." in name.parts or entry.flag_bits & 1:
                        raise ProcessingError("ساختار ZIP محدوده معتبر نیست یا فایل رمزگذاری شده است.")
                    if entry.is_dir() or "__MACOSX" in name.parts or name.name.startswith("."):
                        continue
                    if name.suffix.lower() in EXTENSIONS:
                        key = name.as_posix().casefold()
                        if key in components:
                            raise ProcessingError("نام فایل‌های داخل ZIP محدوده تکراری است.")
                        components[key] = archive.read(entry)
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise ProcessingError(
                "فایل ZIP محدوده خوانده نمی‌شود؛ یک ZIP سالم و بدون رمز انتخاب کنید."
            ) from exc
    else:
        for path in files:
            key = path.name.casefold()
            if key in components or path.suffix.lower() not in EXTENSIONS:
                raise ProcessingError("اجزای شیپ‌فایل محدوده نامعتبر یا تکراری هستند.")
            components[key] = path.read_bytes()
    if sum(map(len, components.values())) > MAX_UNPACKED_BYTES:
        raise ProcessingError("حجم بازشدهٔ محدوده بیش از ۱۰۰ مگابایت است.")
    return components


def load_aoi(directory: Path, target_crs: CRS) -> AreaOfInterest:
    components = _components(directory)
    layers = [PurePosixPath(key).with_suffix("").as_posix() for key in components if key.endswith(".shp")]
    if len(layers) != 1:
        raise ProcessingError("برای محدوده، دقیقاً یک لایهٔ شیپ‌فایل چندضلعی انتخاب کنید.")
    layer = layers[0]
    missing = [ext for ext in sorted(REQUIRED) if layer + ext not in components]
    if missing:
        raise ProcessingError("اجزای شیپ‌فایل کامل نیست. فایل‌های هم‌نام زیر لازم‌اند: " + ", ".join(missing))
    try:
        wkt = components[layer + ".prj"].decode("utf-8-sig").strip()
        source_crs = CRS.from_wkt(wkt)
    except (UnicodeError, CRSError, ValueError) as exc:
        raise ProcessingError("سیستم مختصات فایل PRJ محدوده معتبر نیست.") from exc
    polygons = []
    vertices = 0
    try:
        with shapefile.Reader(
            shp=BytesIO(components[layer + ".shp"]),
            shx=BytesIO(components[layer + ".shx"]),
            dbf=BytesIO(components[layer + ".dbf"]),
        ) as reader:
            for feature in reader.iterShapes():
                if feature.shapeType == shapefile.NULL:
                    continue
                if feature.shapeType not in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                    raise ProcessingError("محدوده باید چندضلعی باشد؛ شیپ‌فایل نقطه یا خط پشتیبانی نمی‌شود.")
                vertices += len(feature.points)
                if len(polygons) >= 10000 or vertices > 2_000_000:
                    raise ProcessingError("محدوده بیش از حد پیچیده است؛ ابتدا مرز را ساده‌سازی کنید.")
                polygon = force_2d(shape(feature.__geo_interface__))
                if polygon.is_empty or not polygon.is_valid or not np.isfinite(polygon.bounds).all():
                    raise ProcessingError("هندسهٔ محدوده نامعتبر است؛ چندضلعی را در نرم‌افزار GIS اصلاح کنید.")
                polygons.append(polygon)
    except (shapefile.ShapefileException, UnicodeError, IndexError, OSError, ValueError) as exc:
        if isinstance(exc, ProcessingError):
            raise
        raise ProcessingError("اجزای شیپ‌فایل خوانده نمی‌شوند یا با یکدیگر سازگار نیستند.") from exc
    if not polygons:
        raise ProcessingError("شیپ‌فایل محدوده هیچ چندضلعی معتبری ندارد.")
    merged = union_all(polygons)
    try:
        transformed = shape(transform_geom(source_crs, target_crs, mapping(merged)))
        if transformed.is_empty or not transformed.is_valid or not np.isfinite(transformed.bounds).all():
            raise ValueError("invalid transformed geometry")
    except Exception as exc:
        raise ProcessingError(
            "تبدیل مختصات محدوده به شبکهٔ DSM ممکن نشد؛ CRS فایل PRJ را بررسی کنید."
        ) from exc
    return AreaOfInterest(
        mapping(transformed),
        {
            "layer": PurePosixPath(layer).name,
            "source_crs": source_crs.to_string(),
            "target_crs": target_crs.to_string(),
            "feature_count": len(polygons),
            "pixel_rule": "center",
            "bounds": list(transformed.bounds),
        },
    )
