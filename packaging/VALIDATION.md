# Windows desktop installer 0.4.0 validation

Target: Windows 10/11 x64. Official CPython 3.12.10, GDAL/PROJ/GEOS, SQLite and 24 locked wheels are included; there are no runtime downloads.

- 53 unit/API tests passed. New coverage includes attributed shapefiles, merging across raster/tile boundaries, holes, diagonal separation, empty vector layers, geographic null areas, splitting at the shapefile component-size limit, input previews, overviews, sampled large-grid previews and single-upload reuse.
- Strict threshold tests select 31, 50, 200, 1,000 and 10,000 m above ground for a 30 m threshold and exclude exactly 30 m. There is no upper extraction threshold. A further cross-tile connectivity check passed on 30 generated scenes.
- Ruff checks, formatting checks and git whitespace checks passed.
- All 3,488 files in the installed payload were compared with the SHA-256 manifest. Installed source, frontend, README and offline self-check were compared with this checkout. Upstream runtime/wheel hashes are locked in packaging/windows-lock.json.
- The actual Windows runtime passed offline_smoke.py under Wine 10: negative sea-level elevations, 10 m / 0.5 m grids, shapefile AOI, attributed shapefile output, input preview, GeoTIFF values, offline CRS conversion and external Python socket blocking.
- The complete browser suite passed against both Linux and the installed Windows backend: independent DEM/DSM previews before extraction, map controls, validation, sample processing, real upload, attributed SHP/DBF download, AOI ZIP/components, cropped output values, ZIP64 downloads, refresh and four viewport widths. External browser requests were blocked and none were attempted.
- Large-raster checks use sparse fixtures with 128 GiB logical size and a 262,144 × 131,072-pixel grid. They validate size handling, direct native selection, bounded preview sampling and AOI extraction; they do not measure processing 100 GB of actual raster samples across a full image.
- The final installed build passed the Win32 file/folder-dialog flow with two such 128 GiB fixtures: each input preview appeared before extraction, a 2 × 2 AOI produced one attributed polygon with H_MAX_M=50, the ZIP64 download contained SHP/DBF and the app exited through its UI. Original input files were preserved.
- Polygonization uses 256-pixel tiles and disk-backed connectivity, then merges each connected feature. Geometry complexity still affects disk and memory consumption; a single exceptionally complex geometry is subject to Shapefile's format limit. Output layers roll over before a component reaches 2 GB.
- Windows validation is through Linux/Wine and headless Chromium. A physical Windows machine, Edge/Chrome app-window integration on Windows and physical GPU throughput have not been tested. Wine and the test-only Microsoft SDK/UCRT are not included in the installer.

The installer is unsigned. Download tag: v0.4.0-desktop. Older releases remain available through their version tags.

```text
47,830,140 bytes
17ad3d239db8be3772aa2bc8bd6e9151a2093f63bc38190d8074d9ce08ed4bfe  MSU-Terrain-Setup-0.4.0-x64.exe
```
