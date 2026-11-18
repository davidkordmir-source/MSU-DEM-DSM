import json
import logging
import math
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.aoi import EXTENSIONS, MAX_AOI_BYTES
from app.demo import make_demo
from app.desktop import pick_output_directory, pick_raster
from app.downloads import VECTOR_FILE, result_files, stream_results, vector_files
from app.previews import build_preview
from app.processing import ProcessingError, process_dsm, process_rasters
from app.resources import resource_budget

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MSU_DATA_DIR", BASE.parent / ".data")).resolve()
RETENTION_SECONDS = 24 * 3600
FILES = {
    "height.tif": "image/tiff",
    "extracted.tif": "image/tiff",
    "mask.tif": "image/tiff",
    "report.json": "application/json",
    "results.zip": "application/zip",
    "features.zip": "application/zip",
    "height.png": "image/png",
    "mask.png": "image/png",
    "surface.png": "image/png",
    "aoi.png": "image/png",
    "ground.png": "image/png",
    "ground.tif": "image/tiff",
    "ground_quality.tif": "image/tiff",
}
jobs: dict[str, dict] = {}
lock = threading.RLock()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA.mkdir(parents=True, exist_ok=True)
    # One raster job at a time can use all available cores without oversubscription.
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raster")
    app.state.preview_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
    app.state.previews = {}
    app.state.uploaded_rasters = {}
    yield
    app.state.executor.shutdown(wait=True)
    app.state.preview_executor.shutdown(wait=True)


app = FastAPI(title="MSU Terrain · DEM / DSM", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def limits_and_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )
    return response


@app.get("/api/health")
def health(request: Request):
    return {
        "status": "ok",
        "resources": resource_budget(),
        "max_file_mb": None,
        "desktop_session": getattr(request.app.state, "desktop_session", None),
    }


@app.post("/api/desktop/exit")
async def desktop_exit(request: Request):
    import asyncio
    import secrets

    session = getattr(request.app.state, "desktop_session", None)
    supplied = request.headers.get("X-MSU-Session", "")
    if not session or not secrets.compare_digest(session, supplied):
        raise HTTPException(403, "این درخواست فقط از پنجرهٔ خود برنامه مجاز است.")
    asyncio.get_running_loop().call_later(0.3, request.app.state.request_exit)
    return {"status": "closing"}


def _require_desktop(request: Request):
    import secrets

    session = getattr(request.app.state, "desktop_session", None)
    if not session or not secrets.compare_digest(session, request.headers.get("X-MSU-Session", "")):
        raise HTTPException(403, "انتخاب مستقیم فایل فقط از پنجرهٔ خود برنامه مجاز است.")


@app.post("/api/desktop/pick-raster")
def select_local_raster(request: Request, kind: str = "dem"):
    _require_desktop(request)
    if kind not in ("dem", "dsm"):
        raise HTTPException(422, "نوع مدل ارتفاعی معتبر نیست.")
    try:
        path = pick_raster("انتخاب مدل زمین DEM" if kind == "dem" else "انتخاب مدل سطح DSM")
        if path is None:
            return {"file": None}
        if path.suffix.lower() not in (".tif", ".tiff") or not path.is_file():
            raise HTTPException(422, "یک فایل TIF یا TIFF معتبر انتخاب کنید.")
        size = path.stat().st_size
        token = uuid4().hex
        with lock:
            if not hasattr(request.app.state, "local_rasters"):
                request.app.state.local_rasters = {}
            registry = request.app.state.local_rasters
            if len(registry) >= 64:
                registry.pop(next(iter(registry)))
            registry[token] = path
        return {"file": {"localToken": token, "name": path.name, "size": size}}
    except (OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _local_path(request: Request, token: str) -> Path:
    path = request.app.state.uploaded_rasters.get(token)
    if path is not None and path.is_file():
        path.parent.touch()
    if path is None:
        _require_desktop(request)
        path = getattr(request.app.state, "local_rasters", {}).get(token)
    if path is None or not path.is_file():
        raise HTTPException(422, "فایل انتخاب‌شده در دسترس نیست؛ دوباره آن را انتخاب کنید.")
    return path


@app.post("/api/inputs", status_code=201)
async def stage_input(request: Request, file: Annotated[UploadFile, File()]):
    name = Path((file.filename or "").replace("\\", "/")).name
    if Path(name).suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(422, "فایل باید TIF یا TIFF باشد.")
    token = uuid4().hex
    directory = DATA / "inputs" / token
    directory.mkdir(parents=True)
    path = directory / name
    try:
        size = await _save_upload(file, path)
        request.app.state.uploaded_rasters[token] = path
        return {"token": token, "name": name, "size": size}
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.post("/api/previews", status_code=202)
def start_preview(request: Request, token: Annotated[str, Form()]):
    path = _local_path(request, token)
    registry = request.app.state.previews
    with lock:
        for preview in registry.values():
            if preview["token"] == token:
                return {"id": preview["id"]}
        if sum(p["status"] in ("queued", "processing") for p in registry.values()) >= 6:
            raise HTTPException(429, "چند پیش‌نمایش در صف است؛ کمی بعد دوباره فایل را انتخاب کنید.")
        identity = uuid4().hex
        directory = DATA / "previews" / identity
        directory.mkdir(parents=True)
        entry = {"id": identity, "token": token, "status": "queued", "progress": 0}
        registry[identity] = entry

    def generate():
        try:
            with lock:
                entry["status"] = "processing"

            def progress(value, message):
                with lock:
                    entry.update(progress=value, message=message)

            result = build_preview(path, directory, progress)
            result["filename"] = path.name
            with lock:
                entry.update(status="complete", progress=100, result=result)
        except Exception as exc:
            logger.exception("Input preview failed")
            with lock:
                entry.update(
                    status="error",
                    message=str(exc) if isinstance(exc, ProcessingError) else "پیش‌نمایش فایل ساخته نشد.",
                )

    request.app.state.preview_executor.submit(generate)
    return {"id": identity}


@app.get("/api/previews/{preview_id}")
def get_preview(request: Request, preview_id: str):
    with lock:
        entry = request.app.state.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "پیش‌نمایش یافت نشد.")
        return {k: v for k, v in entry.items() if k != "token"}


@app.get("/api/previews/{preview_id}/image.png")
def preview_image(request: Request, preview_id: str):
    entry = get_preview(request, preview_id)
    if entry["status"] != "complete":
        raise HTTPException(404, "پیش‌نمایش هنوز آماده نیست.")
    return FileResponse(DATA / "previews" / preview_id / "preview.png", media_type="image/png")


@app.post("/api/desktop/pick-output")
def select_output_directory(request: Request):
    _require_desktop(request)
    try:
        path = pick_output_directory()
        if path is None:
            return {"folder": None}
        if not path.is_dir():
            raise HTTPException(422, "پوشه در دسترس نیست.")
        token = uuid4().hex
        with lock:
            if not hasattr(request.app.state, "output_folders"):
                request.app.state.output_folders = {}
            registry = request.app.state.output_folders
            if len(registry) >= 64:
                registry.pop(next(iter(registry)))
            registry[token] = path
        return {"folder": {"token": token, "path": str(path), "free_bytes": shutil.disk_usage(path).free}}
    except (OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _cleanup() -> None:
    cutoff = time.time() - RETENTION_SECONDS
    for path in DATA.iterdir():
        if not path.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", path.name):
            continue
        with lock:
            active = jobs.get(path.name, {}).get("status") in ("uploading", "queued", "processing")
            if not active and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                jobs.pop(path.name, None)
    with lock:
        active_inputs = {
            Path(source).parent
            for job in jobs.values()
            if job["status"] in ("uploading", "queued", "processing")
            for source in job.get("input_paths", [])
        }
        previews = getattr(app.state, "previews", {})
        uploaded = getattr(app.state, "uploaded_rasters", {})
        for preview in previews.values():
            if preview["status"] in ("queued", "processing") and preview["token"] in uploaded:
                active_inputs.add(uploaded[preview["token"]].parent)
        for category in ("inputs", "previews"):
            root = DATA / category
            if not root.exists():
                continue
            for directory in root.iterdir():
                if not directory.is_dir() or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
                    continue
                active = (
                    directory in active_inputs
                    if category == "inputs"
                    else previews.get(directory.name, {}).get("status") in ("queued", "processing")
                )
                if not active and directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
                    (uploaded if category == "inputs" else previews).pop(directory.name, None)


def _reserve() -> tuple[str, Path]:
    _cleanup()
    with lock:
        if sum(j["status"] in ("uploading", "queued", "processing") for j in jobs.values()) >= 3:
            raise HTTPException(429, "صف پردازش پر است؛ پس از پایان یک پردازش دوباره تلاش کنید.")
        job_id = uuid4().hex
        directory = DATA / job_id
        directory.mkdir(parents=True)
        jobs[job_id] = {
            "id": job_id,
            "status": "uploading",
            "progress": 0,
            "message": "دریافت فایل‌ها",
            "created_at": time.time(),
        }
    return job_id, directory


def _update(job_id: str, **changes):
    with lock:
        jobs[job_id].update(changes)


def _run(job_id: str, directory: Path, options: dict, demo: bool = False, sources=None, destination=None):
    _update(job_id, status="processing", message="آماده‌سازی")
    output = destination / f"MSU-{job_id}" if destination else directory / "output"
    own_output = False
    try:
        output.mkdir(exist_ok=False)
        own_output = True
        _update(job_id, output_directory=str(output), permanent_output=destination is not None)
        if demo:
            make_demo(directory)
        inputs = sources or (directory / "dsm.tif",)
        processor = process_dsm if len(inputs) == 1 else process_rasters
        report = processor(
            *inputs, output, progress=lambda pct, msg: _update(job_id, progress=pct, message=msg), **options
        )
        _update(job_id, status="complete", progress=100, message="پردازش کامل شد", result=report, demo=demo)
        with lock:
            (directory / "job.json").write_text(
                json.dumps(jobs[job_id], ensure_ascii=False), encoding="utf-8"
            )
    except ProcessingError as exc:
        _update(job_id, status="error", message=str(exc))
        if own_output:
            shutil.rmtree(output, ignore_errors=True)
    except Exception:
        logger.exception("Raster processing failed for job %s", job_id)
        _update(job_id, status="error", message="پردازش انجام نشد. سلامت فایل و فضای دیسک را بررسی کنید.")
        if own_output:
            shutil.rmtree(output, ignore_errors=True)
    finally:
        for name in ("dem.tif", "dsm.tif"):
            (directory / name).unlink(missing_ok=True)
        shutil.rmtree(directory / "aoi", ignore_errors=True)


def _options(
    threshold: float, inclusive: bool, dem_unit: str, dsm_unit: str, resampling: str, same_datum: bool
) -> dict:
    if not math.isfinite(threshold) or threshold < 0:
        raise HTTPException(422, "آستانه باید عددی متناهی و نامنفی بر حسب متر باشد.")
    if not same_datum:
        raise HTTPException(422, "یکسان بودن مبنای ارتفاعی DEM و DSM باید تأیید شود.")
    if dem_unit not in ("m", "ft", "us-ft") or dsm_unit not in ("m", "ft", "us-ft"):
        raise HTTPException(422, "واحد ارتفاع نامعتبر است.")
    if resampling not in ("auto", "bilinear", "average", "nearest"):
        raise HTTPException(422, "روش بازنمونه‌برداری نامعتبر است.")
    return {
        "threshold": threshold,
        "inclusive": inclusive,
        "dem_unit": dem_unit,
        "dsm_unit": dsm_unit,
        "resampling": resampling,
    }


def _dsm_options(
    threshold, inclusive, dsm_unit, ground_profile="urban", max_object_width=200.0, ground_grid=2.0
):
    from app.ground import validate_options

    if not math.isfinite(threshold) or threshold < 0:
        raise HTTPException(422, "آستانه باید عددی متناهی و نامنفی بر حسب متر باشد.")
    if dsm_unit not in ("m", "ft", "us-ft"):
        raise HTTPException(422, "واحد ارتفاع نامعتبر است.")
    try:
        validate_options(ground_profile, max_object_width, ground_grid)
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "threshold": threshold,
        "inclusive": inclusive,
        "dsm_unit": dsm_unit,
        "ground_profile": ground_profile,
        "max_object_width": max_object_width,
        "ground_grid": ground_grid,
    }


async def _save_upload(upload: UploadFile, path: Path, limit=None):
    size = 0
    with path.open("wb") as target:
        while chunk := await upload.read(1024**2):
            size += len(chunk)
            if limit is not None and size > limit:
                raise HTTPException(413, "حجم مجموع فایل‌های محدوده بیش از ۵۰ مگابایت است.")
            target.write(chunk)
    if size == 0:
        raise HTTPException(422, "فایل خالی است.")
    return size


async def _save_aoi(uploads: list[UploadFile], directory: Path):
    if len(uploads) > 8:
        raise HTTPException(422, "یک ZIP یا اجزای یک شیپ‌فایل را انتخاب کنید.")
    directory.mkdir()
    names = set()
    remaining = MAX_AOI_BYTES
    for upload in uploads:
        name = Path((upload.filename or "").replace("\\", "/")).name
        if Path(name).suffix.lower() not in EXTENSIONS | {".zip"} or name.casefold() in names:
            raise HTTPException(422, "پسوند یا نام فایل محدوده معتبر نیست.")
        names.add(name.casefold())
        remaining -= await _save_upload(upload, directory / name, limit=remaining)


@app.post("/api/jobs", status_code=202)
async def create_job(
    request: Request,
    output_token: Annotated[str | None, Form()] = None,
    dem: Annotated[UploadFile | None, File()] = None,
    dsm: Annotated[UploadFile | None, File()] = None,
    dem_token: Annotated[str | None, Form()] = None,
    dsm_token: Annotated[str | None, Form()] = None,
    threshold: Annotated[float, Form()] = 30,
    inclusive: Annotated[bool, Form()] = False,
    dem_unit: Annotated[str, Form()] = "m",
    dsm_unit: Annotated[str, Form()] = "m",
    resampling: Annotated[str, Form()] = "auto",
    same_datum: Annotated[bool, Form()] = False,
    ground_profile: Annotated[str, Form()] = "urban",
    max_object_width: Annotated[float, Form()] = 200.0,
    ground_grid: Annotated[float, Form()] = 2.0,
    aoi_files: Annotated[list[UploadFile] | None, File()] = None,
):
    legacy = bool(dem or dem_token)
    options = (
        _options(threshold, inclusive, dem_unit, dsm_unit, resampling, same_datum)
        if legacy
        else _dsm_options(threshold, inclusive, dsm_unit, ground_profile, max_object_width, ground_grid)
    )
    destination = None
    if output_token:
        _require_desktop(request)
        destination = getattr(request.app.state, "output_folders", {}).get(output_token)
        if destination is None or not destination.is_dir():
            raise HTTPException(422, "پوشهٔ خروجی در دسترس نیست؛ دوباره انتخاب کنید.")
    inputs = []
    kinds = ("dem", "dsm") if legacy else ("dsm",)
    pairs = ((dem, dem_token), (dsm, dsm_token)) if legacy else ((dsm, dsm_token),)
    for upload, token in pairs:
        if bool(upload) == bool(token):
            raise HTTPException(422, "یک فایل DSM انتخاب کنید.")
        inputs.append(_local_path(request, token) if token else upload)
    names = tuple(
        item.name if isinstance(item, Path) else Path((item.filename or "").replace("\\", "/")).name[:180]
        for item in inputs
    )
    if any(Path(name).suffix.lower() not in (".tif", ".tiff") for name in names):
        raise HTTPException(422, "فقط فایل GeoTIFF با پسوند tif یا tiff قابل استفاده است.")
    options["filenames" if legacy else "filename"] = names if legacy else names[0]
    job_id, directory = _reserve()
    try:
        sources = []
        for kind, item in zip(kinds, inputs, strict=True):
            if isinstance(item, Path):
                sources.append(item)
            else:
                target = directory / f"{kind}.tif"
                await _save_upload(item, target)
                sources.append(target)
        if aoi_files:
            await _save_aoi(aoi_files, directory / "aoi")
            options["aoi_directory"] = directory / "aoi"
        _update(job_id, status="queued", message="در صف پردازش", input_paths=[str(p) for p in sources])
        request.app.state.executor.submit(
            _run, job_id, directory, options, sources=tuple(sources), destination=destination
        )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        with lock:
            jobs.pop(job_id, None)
        raise
    finally:
        for upload in (dem, dsm):
            if upload:
                await upload.close()
        for upload in aoi_files or []:
            await upload.close()
    return {"id": job_id}


@app.post("/api/demo", status_code=202)
def run_demo(request: Request, threshold: float = 30, inclusive: bool = False):
    options = _dsm_options(threshold, inclusive, "m")
    options["filename"] = "demo_dsm.tif"
    job_id, directory = _reserve()
    _update(job_id, status="queued", message="در صف پردازش")
    request.app.state.executor.submit(_run, job_id, directory, options, True)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "پردازش یافت نشد.")
    with lock:
        if job_id not in jobs:
            saved = DATA / job_id / "job.json"
            if saved.exists() and saved.stat().st_mtime >= time.time() - RETENTION_SECONDS:
                jobs[job_id] = json.loads(saved.read_text(encoding="utf-8"))
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "پردازش یافت نشد یا فایل‌های موقت آن منقضی شده‌اند.")
        return dict(job)


@app.get("/api/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    job = get_job(job_id)
    if job["status"] != "complete" or (filename not in FILES and not VECTOR_FILE.fullmatch(filename)):
        raise HTTPException(404, "خروجی هنوز آماده نیست.")
    directory = Path(job.get("output_directory", DATA / job_id / "output"))
    if filename in ("results.zip", "features.zip"):
        vector_only = filename == "features.zip"
        if (vector_only and not vector_files(directory)) or not all(
            (directory / name).is_file() for name in result_files(directory, vector_only=vector_only)
        ):
            raise HTTPException(404, "فایل خروجی در دسترس نیست.")
        return StreamingResponse(
            stream_results(directory, vector_only=vector_only),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    path = directory / filename
    if not path.is_file():
        raise HTTPException(404, "فایل خروجی در دسترس نیست.")
    return FileResponse(
        path,
        media_type=FILES.get(filename, "application/octet-stream"),
        filename=filename if not filename.endswith(".png") else None,
    )


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="frontend")
