import os
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import rasterio
from fastapi.testclient import TestClient
from shapely.geometry import box

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path / "jobs")
    main.jobs.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.jobs.clear()


def await_job(client, job_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in ("complete", "error"):
            return job
        time.sleep(0.05)
    pytest.fail("Job did not complete")


def test_demo_download_and_restart_recovery(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200
    response = client.post("/api/demo?threshold=30")
    assert response.status_code == 202
    job_id = response.json()["id"]
    job = await_job(client, job_id)
    assert job["status"] == "complete", job
    assert job["result"]["max_height_m"] >= 68
    response = client.get(f"/api/jobs/{job_id}/files/results.zip")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert "extracted.tif" in archive.namelist()
        assert archive.testzip() is None
    assert not (main.DATA / job_id / "output/results.zip").exists()
    main.jobs.clear()
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "complete"
    assert client.get(f"/api/jobs/{job_id}/files/unexpected.txt").status_code == 404


def test_actual_upload(raster, client):
    dem = raster("dem.tif", [[-100, -100]])
    dsm = raster("dsm.tif", [[-50, -70]])
    response = client.post(
        "/api/jobs",
        files={"dem": ("terrain.tif", dem.read_bytes()), "dsm": ("surface.tif", dsm.read_bytes())},
        data={"threshold": 30, "same_datum": "true"},
    )
    assert response.status_code == 202
    job = await_job(client, response.json()["id"])
    assert job["status"] == "complete"
    assert job["result"]["selected_pixels"] == 1
    response = client.get(f"/api/jobs/{job['id']}/files/height.tif")
    with rasterio.MemoryFile(response.content) as mem, mem.open() as src:
        assert src.read(1).tolist() == [[50, 30]]
        assert src.crs.to_epsg() == 32639


def test_input_errors(client):
    files = {"dem": ("dem.tif", b"bad"), "dsm": ("dsm.tif", b"bad")}
    assert client.post("/api/jobs", files=files).status_code == 422
    response = client.post("/api/jobs", files=files, data={"same_datum": True})
    assert response.status_code == 202
    assert await_job(client, response.json()["id"])["status"] == "error"
    assert client.post("/api/demo?threshold=-1").status_code == 422
    assert client.get("/api/jobs/unknown").status_code == 404


def test_shapefile_upload(raster, aoi, client):
    dem = raster("dem.tif", [[-100, -100]])
    dsm = raster("dsm.tif", [[-50, -50]])
    area = aoi([box(500000, 4099999, 500001, 4100000)], zipped=True)
    response = client.post(
        "/api/jobs",
        data={"same_datum": True},
        files=[
            ("dem", (dem.name, dem.read_bytes())),
            ("dsm", (dsm.name, dsm.read_bytes())),
            ("aoi_files", ("area.zip", (area / "area.zip").read_bytes())),
        ],
    )
    assert response.status_code == 202
    job = await_job(client, response.json()["id"])
    assert job["status"] == "complete", job
    assert job["result"]["selected_pixels"] == 1


def test_desktop_direct_large_files_are_not_copied_or_deleted(raster, client, monkeypatch):
    dem = raster("dem.tif", [[-100]])
    dsm = raster("dsm.tif", [[-50]])
    for path in (dem, dsm):
        with path.open("ab") as file:
            if os.name == "nt":
                import ctypes
                import msvcrt
                from ctypes import wintypes

                ioctl = ctypes.windll.kernel32.DeviceIoControl
                ioctl.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.c_void_p,
                ]
                returned = wintypes.DWORD()
                if not ioctl(
                    msvcrt.get_osfhandle(file.fileno()),
                    0x900C4,
                    None,
                    0,
                    None,
                    0,
                    ctypes.byref(returned),
                    None,
                ):
                    pytest.skip("Sparse files are required for the large-file fixture")
            file.truncate(128 * 1024**3)
    monkeypatch.setattr(main.app.state, "desktop_session", "test-local-session", raising=False)
    headers = {"X-MSU-Session": "test-local-session"}
    tokens = []
    for path in (dem, dsm):
        monkeypatch.setattr(main, "pick_raster", lambda _title, selected=path: selected)
        response = client.post("/api/desktop/pick-raster", headers=headers)
        assert response.status_code == 200
        tokens.append(response.json()["file"]["localToken"])
    response = client.post(
        "/api/jobs",
        headers=headers,
        data={"same_datum": True, "dem_token": tokens[0], "dsm_token": tokens[1]},
    )
    assert response.status_code == 202
    job = await_job(client, response.json()["id"])
    assert job["status"] == "complete", job
    assert job["result"]["selected_pixels"] == 1
    assert not (main.DATA / job["id"] / "dem.tif").exists()
    assert dem.stat().st_size == dsm.stat().st_size == 128 * 1024**3


def test_direct_picker_requires_desktop_session(client):
    assert client.post("/api/desktop/pick-raster").status_code == 403
    assert client.post("/api/desktop/pick-output").status_code == 403
    assert client.post("/api/desktop/exit").status_code == 403


def test_selected_output_drive_is_permanent(raster, client, monkeypatch, tmp_path):
    destination = tmp_path / "user-results"
    destination.mkdir()
    sentinel = destination / "existing.txt"
    sentinel.write_text("keep")
    monkeypatch.setattr(main.app.state, "desktop_session", "folder-session", raising=False)
    monkeypatch.setattr(main, "pick_output_directory", lambda: destination)
    headers = {"X-MSU-Session": "folder-session"}
    folder = client.post("/api/desktop/pick-output", headers=headers).json()["folder"]
    dem, dsm = raster("dem.tif", [[-100]]), raster("dsm.tif", [[-50]])
    response = client.post(
        "/api/jobs",
        headers=headers,
        files={"dem": (dem.name, dem.read_bytes()), "dsm": (dsm.name, dsm.read_bytes())},
        data={"same_datum": True, "output_token": folder["token"]},
    )
    assert response.status_code == 202
    job = await_job(client, response.json()["id"])
    assert job["status"] == "complete", job
    output = Path(job["output_directory"])
    assert output.parent == destination
    assert job["permanent_output"]
    assert (output / "height.tif").is_file()
    response = client.get(f"/api/jobs/{job['id']}/files/results.zip")
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.testzip() is None
    old = time.time() - main.RETENTION_SECONDS - 100
    os.utime(main.DATA / job["id"], (old, old))
    main._cleanup()
    assert not (main.DATA / job["id"]).exists()
    assert (output / "height.tif").is_file()
    assert sentinel.read_text() == "keep"
