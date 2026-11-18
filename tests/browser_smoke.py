"""Run against the app on port 8000: uv run --group browser python tests/browser_smoke.py."""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import numpy as np
import rasterio
import shapefile
from playwright.sync_api import expect, sync_playwright
from rasterio.transform import from_origin

BASE_URL = os.environ.get("MSU_TEST_URL", "http://127.0.0.1:8000")
ARTIFACTS = Path("test-results")
ARTIFACTS.mkdir(exist_ok=True)


def write_raster(path, values, resolution):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float32",
        crs="EPSG:32639",
        nodata=-9999,
        transform=from_origin(500000, 4100000, resolution, resolution),
    ) as dst:
        dst.write(values.astype("float32"), 1)


with tempfile.TemporaryDirectory() as directory, sync_playwright() as p:
    root = Path(directory)
    surface = np.full((60, 60), -100.0)
    surface[10:30, 10:30] = -50
    write_raster(root / "dsm_05m.tif", surface, 0.5)
    with shapefile.Writer(str(root / "boundary")) as writer:
        writer.field("id", "N")
        writer.poly(
            [
                [
                    (500005, 4099990),
                    (500005, 4099995),
                    (500010, 4099995),
                    (500010, 4099990),
                    (500005, 4099990),
                ]
            ]
        )
        writer.record(1)
    (root / "boundary.prj").write_text(rasterio.CRS.from_epsg(32639).to_wkt())
    with ZipFile(root / "boundary.zip", "w") as archive:
        for ext in ("shp", "shx", "dbf", "prj"):
            archive.write(root / f"boundary.{ext}", f"boundary.{ext}")
    browser = p.chromium.launch(
        executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
        headless=True,
        args=["--enable-unsafe-swiftshader"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
    errors = []
    external_requests = []

    def offline_route(route):
        if urlparse(route.request.url).hostname == urlparse(BASE_URL).hostname:
            route.continue_()
        else:
            external_requests.append(route.request.url)
            route.abort()

    page.route("**/*", offline_route)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(BASE_URL)
    expect(page.locator("#hardware-status")).not_to_contain_text("در حال")
    page.screenshot(path=str(ARTIFACTS / "desktop-initial.png"), full_page=True)
    page.locator("#help-button").click()
    expect(page.locator("#help-dialog")).to_be_visible()
    page.locator("#close-help").click()
    page.locator("#analyze-button").click()
    expect(page.locator("#error-message")).to_contain_text("یک فایل DSM")
    page.locator("#dsm-file").set_input_files(root / "dsm_05m.tif")
    expect(page.locator('[data-input-layer="dsm"]')).to_be_enabled(timeout=30000)
    expect(page.locator("#viewer-status")).to_contain_text("dsm_05m.tif")
    page.screenshot(path=str(ARTIFACTS / "input-preview-before-extraction.png"), full_page=True)
    page.locator("#dsm-remove").click()
    page.locator("#demo-button").click()
    expect(page.locator("#result-details")).to_be_visible(timeout=30000)
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#map-canvas")).to_be_visible()
    expect(page.locator("#stat-max")).to_have_text("۶۸")
    expect(page.locator("#demo-badge")).to_be_visible()
    page.locator('[data-layer="ground"]').click()
    expect(page.locator("#viewer-status")).to_contain_text("زمین تخمینی")
    page.locator('[data-layer="surface"]').click()
    page.locator("#zoom-in").click()
    page.locator("#reset-view").click()
    page.locator('[data-layer="mask"]').click()
    expect(page.locator("#mask-toggle")).to_be_hidden()
    page.locator('[data-layer="height"]').click()
    page.screenshot(path=str(ARTIFACTS / "desktop-demo.png"), full_page=True)
    with page.expect_download() as download_info:
        page.locator("#download-all").click()
    download_info.value.save_as(str(ARTIFACTS / "demo-results.zip"))
    page.reload()
    expect(page.locator("#result-details")).to_be_visible(timeout=30000)
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    page.locator("#threshold").fill("40")
    expect(page.locator("#stale-notice")).to_be_visible()
    page.locator("#threshold").fill("30")
    page.locator("#dsm-file").set_input_files(root / "dsm_05m.tif")
    page.locator("#analyze-button").click()
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#stat-pixels")).to_have_text("۴۰۰")
    expect(page.locator("#stat-area")).to_have_text("۱۰۰")
    expect(page.locator("#demo-badge")).to_be_hidden()
    expect(page.locator("#alignment-info")).to_contain_text("تخمین از DSM")
    expect(page.locator("#error-message")).to_be_hidden()
    page.screenshot(path=str(ARTIFACTS / "desktop-upload.png"), full_page=True)
    expect(page.locator("#attribute-preview")).to_be_visible()
    expect(page.locator("#attribute-count")).to_contain_text("۱ عارضه")
    with page.expect_download() as shape_download:
        page.locator("#download-shapefile").click()
    shape_download.value.save_as(str(ARTIFACTS / "features.zip"))
    with ZipFile(ARTIFACTS / "features.zip") as archive:
        from io import BytesIO

        with shapefile.Reader(
            shp=BytesIO(archive.read("features_001.shp")),
            shx=BytesIO(archive.read("features_001.shx")),
            dbf=BytesIO(archive.read("features_001.dbf")),
        ) as reader:
            assert len(reader) == 1
            assert reader.record(0).as_dict()["H_MAX_M"] == 50
            assert reader.record(0).as_dict()["PIXELS"] == 400
            assert reader.record(0).as_dict()["GND_SRC"] == "DSM_EST"
    page.locator("#aoi-files").set_input_files(root / "boundary.zip")
    expect(page.locator("#aoi-name")).to_have_text("boundary.zip")
    expect(page.locator("#stale-notice")).to_be_visible()
    page.locator("#analyze-button").click()
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#stat-pixels")).to_have_text("۱۰۰")
    expect(page.locator("#stat-area")).to_have_text("۲۵")
    expect(page.locator("#aoi-legend")).to_be_visible()
    expect(page.locator("#alignment-info")).to_contain_text("boundary")
    with page.expect_download() as aoi_download:
        page.locator("#download-all").click()
    aoi_download.value.save_as(str(ARTIFACTS / "aoi-results.zip"))
    with ZipFile(ARTIFACTS / "aoi-results.zip") as archive:
        report = json.loads(archive.read("report.json"))
        assert report["selected_pixels"] == 100
        assert report["ground_estimation"]["estimated"]
        assert "ground.tif" in archive.namelist()
        assert "ground_quality.tif" in archive.namelist()
        with rasterio.MemoryFile(archive.read("height.tif")) as memory, memory.open() as output:
            assert output.shape == (10, 10)
            np.testing.assert_allclose(output.read(1), 50)
    page.screenshot(path=str(ARTIFACTS / "desktop-aoi.png"), full_page=True)
    page.locator("#aoi-remove").click()
    expect(page.locator("#aoi-remove")).to_be_hidden()
    page.locator("#aoi-files").set_input_files(
        [root / f"boundary.{ext}" for ext in ("shp", "shx", "dbf", "prj")]
    )
    expect(page.locator("#aoi-name")).to_have_text("boundary.shp")
    page.locator("#analyze-button").click()
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#stat-pixels")).to_have_text("۱۰۰")
    for width in (390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}px"
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
    assert not errors, errors
    assert not external_requests, external_requests
    print(
        json.dumps(
            {
                "status": "passed",
                "checks": [
                    "demo",
                    "input previews before extraction",
                    "attributed shapefile download",
                    "single DSM, native 0.5m extraction",
                    "estimated ground preview and diagnostics",
                    "AOI ZIP and shapefile components",
                    "AOI crop and downloaded pixel values",
                    "downloads",
                    "refresh",
                    "validation",
                    "map controls",
                    "4 viewport widths",
                ],
                "renderer": page.locator("#renderer-status").inner_text(),
                "browser_errors": errors,
            },
            ensure_ascii=False,
        )
    )
    browser.close()
