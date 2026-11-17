"""Run against the app on port 8000: uv run --group browser python tests/browser_smoke.py."""

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import rasterio
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
    write_raster(root / "dem_10m.tif", np.full((3, 3), -100), 10)
    surface = np.full((60, 60), -100.0)
    surface[10:30, 10:30] = -50
    write_raster(root / "dsm_05m.tif", surface, 0.5)
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
    expect(page.locator("#error-message")).to_contain_text("هر دو فایل")
    page.locator("#demo-button").click()
    expect(page.locator("#result-details")).to_be_visible(timeout=30000)
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#map-canvas")).to_be_visible()
    expect(page.locator("#stat-max")).to_have_text("۶۸")
    expect(page.locator("#demo-badge")).to_be_visible()
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
    page.locator("#dem-file").set_input_files(root / "dem_10m.tif")
    page.locator("#dsm-file").set_input_files(root / "dsm_05m.tif")
    page.locator("#analyze-button").click()
    expect(page.locator("#error-message")).to_contain_text("مبنای ارتفاعی")
    page.locator("#same-datum").check()
    page.locator("#analyze-button").click()
    expect(page.locator("#processing-overlay")).to_be_hidden(timeout=30000)
    expect(page.locator("#stat-pixels")).to_have_text("۴۰۰")
    expect(page.locator("#stat-area")).to_have_text("۱۰۰")
    expect(page.locator("#demo-badge")).to_be_hidden()
    expect(page.locator("#alignment-info")).to_contain_text("درون‌یابی دوخطی")
    expect(page.locator("#error-message")).to_be_hidden()
    page.screenshot(path=str(ARTIFACTS / "desktop-upload.png"), full_page=True)
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
                    "real upload at 10m/0.5m",
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
