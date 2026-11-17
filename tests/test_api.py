import time
from io import BytesIO
from zipfile import ZipFile

import pytest
import rasterio
from fastapi.testclient import TestClient

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
