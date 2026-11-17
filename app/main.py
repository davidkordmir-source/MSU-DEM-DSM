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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.demo import make_demo
from app.processing import ProcessingError, process_rasters
from app.resources import resource_budget

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MSU_DATA_DIR", BASE.parent / ".data")).resolve()
MAX_FILE_BYTES = 512 * 1024**2
RETENTION_SECONDS = 24 * 3600
FILES = {
    "height.tif": "image/tiff",
    "extracted.tif": "image/tiff",
    "mask.tif": "image/tiff",
    "report.json": "application/json",
    "results.zip": "application/zip",
    "height.png": "image/png",
    "mask.png": "image/png",
    "surface.png": "image/png",
}
jobs: dict[str, dict] = {}
lock = threading.RLock()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA.mkdir(parents=True, exist_ok=True)
    # One raster job at a time can use all available cores without oversubscription.
    app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raster")
    yield
    app.state.executor.shutdown(wait=True)


app = FastAPI(title="MSU Terrain · DEM / DSM", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def limits_and_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > 2 * MAX_FILE_BYTES + 1024**2):
        return JSONResponse({"detail": "حداکثر حجم هر فایل ۵۱۲ مگابایت است."}, status_code=413)
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
def health():
    return {"status": "ok", "resources": resource_budget(), "max_file_mb": MAX_FILE_BYTES // 1024**2}


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


def _run(job_id: str, directory: Path, options: dict, demo: bool = False):
    _update(job_id, status="processing", message="آماده‌سازی")
    try:
        if demo:
            make_demo(directory)
        report = process_rasters(
            directory / "dem.tif",
            directory / "dsm.tif",
            directory / "output",
            progress=lambda pct, msg: _update(job_id, progress=pct, message=msg),
            **options,
        )
        _update(job_id, status="complete", progress=100, message="پردازش کامل شد", result=report, demo=demo)
        with lock:
            (directory / "job.json").write_text(
                json.dumps(jobs[job_id], ensure_ascii=False), encoding="utf-8"
            )
    except ProcessingError as exc:
        _update(job_id, status="error", message=str(exc))
        shutil.rmtree(directory / "output", ignore_errors=True)
    except Exception:
        logger.exception("Raster processing failed for job %s", job_id)
        _update(job_id, status="error", message="پردازش انجام نشد. سلامت فایل و فضای دیسک را بررسی کنید.")
        shutil.rmtree(directory / "output", ignore_errors=True)
    finally:
        for name in ("dem.tif", "dsm.tif"):
            (directory / name).unlink(missing_ok=True)


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


async def _save_upload(upload: UploadFile, path: Path):
    size = 0
    with path.open("wb") as target:
        while chunk := await upload.read(1024**2):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise HTTPException(413, "حداکثر حجم هر فایل ۵۱۲ مگابایت است.")
            target.write(chunk)
    if size == 0:
        raise HTTPException(422, "فایل خالی است.")


@app.post("/api/jobs", status_code=202)
async def create_job(
    request: Request,
    dem: Annotated[UploadFile, File()],
    dsm: Annotated[UploadFile, File()],
    threshold: Annotated[float, Form()] = 30,
    inclusive: Annotated[bool, Form()] = False,
    dem_unit: Annotated[str, Form()] = "m",
    dsm_unit: Annotated[str, Form()] = "m",
    resampling: Annotated[str, Form()] = "auto",
    same_datum: Annotated[bool, Form()] = False,
):
    options = _options(threshold, inclusive, dem_unit, dsm_unit, resampling, same_datum)
    names = tuple(Path((f.filename or "").replace("\\", "/")).name[:180] for f in (dem, dsm))
    if any(Path(name).suffix.lower() not in (".tif", ".tiff") for name in names):
        raise HTTPException(422, "فقط فایل GeoTIFF با پسوند tif یا tiff قابل استفاده است.")
    options["filenames"] = names
    job_id, directory = _reserve()
    try:
        await _save_upload(dem, directory / "dem.tif")
        await _save_upload(dsm, directory / "dsm.tif")
        _update(job_id, status="queued", message="در صف پردازش")
        request.app.state.executor.submit(_run, job_id, directory, options)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        with lock:
            jobs.pop(job_id, None)
        raise
    finally:
        await dem.close()
        await dsm.close()
    return {"id": job_id}


@app.post("/api/demo", status_code=202)
def run_demo(request: Request, threshold: float = 30, inclusive: bool = False):
    options = _options(threshold, inclusive, "m", "m", "auto", True)
    options["filenames"] = ("demo_dem.tif", "demo_dsm.tif")
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
    if job["status"] != "complete" or filename not in FILES:
        raise HTTPException(404, "خروجی هنوز آماده نیست.")
    path = DATA / job_id / "output" / filename
    if not path.is_file():
        raise HTTPException(404, "فایل خروجی در دسترس نیست.")
    return FileResponse(
        path, media_type=FILES[filename], filename=filename if not filename.endswith(".png") else None
    )


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="frontend")
