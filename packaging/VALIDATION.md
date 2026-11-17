# Windows desktop installer 0.3.0 validation

Target: Windows 10/11 x64; official CPython 3.12.10, GDAL/PROJ/GEOS and 24 locked Windows wheels included. No runtime downloads.

- 42 unit/API tests passed. Coverage includes below-sea-level heights, thresholds, NoData, units, 10 m / 0.5 m grids, CRS conversion, shapefile components/ZIP, holes and overlapping polygons, output-folder retention, and bounded ZIP64 streaming. Ruff checks and formatting checks passed.
- All 3,486 payload files were verified against the bundled SHA-256 manifest after installation. App source and README in the installed payload were compared with this checkout. Runtime and dependency archives were checked against the locked upstream hashes.
- The x64 NSIS installer installed the app, Desktop/Start-menu shortcuts and uninstall registry entries under Wine 10. The native launcher started bundled pythonw without a console, served the local app and exited through the UI.
- The actual Windows runtime passed offline_smoke.py: negative elevations, 10 m / 0.5 m resampling, shapefile cropping, output values, offline CRS conversion and blocked external Python socket connections.
- Browser flow tests passed for sample processing, raster upload, shapefile ZIP/components, cropped output values, downloads, refresh recovery, validation, map controls and four responsive widths. External browser requests were blocked; none were attempted.
- A further test of the final installed Windows build used the real Win32 raster and folder dialogs. Two BigTIFF fixtures each had a logical size of 137,438,953,472 bytes (128 GiB), on a grid of 262,144 × 131,072 pixels. Direct file selection, extraction of a 2 × 2-pixel shapefile AOI, permanent results, ZIP64 download, preservation of source files and application exit passed.
- Those large fixtures were sparse files with very little allocated data. This checks 64-bit file sizes, large-grid metadata, native selection and windowed AOI processing. It does **not** benchmark reading or processing 100 GB of real raster samples across the complete image. Real throughput depends on data layout, compression, disk, CPU and the selected area.
- Uninstall removed all payload files, its executable, three shortcuts and the uninstall registry entry. An unknown user file inside the install folder, AppData and permanent selected-folder results were retained.
- Validation used Linux, Wine and headless Chromium. A physical Windows installation, the Edge/Chrome app window on Windows, and physical GPU throughput have not been tested. Wine and test-only Microsoft SDK/UCRT files are not distributed with the app. Windows 10/11 supplies the OS runtime.

The installer is unsigned. Its direct GitHub download is versioned at tag v0.3.0-desktop. The earlier portable package remains accessible at tag v0.2.0-offline.

```text
47,820,970 bytes
c33a4a6b1eb27548594ab946f3076eb687a8db880bcb244e191f8d14618e3fd0  MSU-Terrain-Setup-0.3.0-x64.exe
```
