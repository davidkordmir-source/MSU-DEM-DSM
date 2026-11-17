# Offline Windows package validation

- Target: Windows 10/11 x64; bundled official CPython 3.12.10.
- 28 unit/API tests passed, including threshold boundaries, NoData, CRS conversion, 10 m / 0.5 m grids, and network guards.
- All 3,318 package files are listed with SHA-256 hashes inside the ZIP. Runtime and 22 wheels were validated against their locked upstream checksums.
- The actual Windows Python executable and Windows DLLs passed `offline_smoke.py` under Wine 10 with the official Microsoft UCRT in the test environment.
- The Windows launcher served the application under Wine. Browser checks passed for sample processing, real GeoTIFF upload, output downloads, refresh recovery, input validation, layer controls, and four responsive widths. External browser requests were blocked; none were attempted.
- The portable launcher rejects external Python socket connections and disables PROJ downloads. It requires no package installation at startup.
- Validation used Linux/Wine and headless Chromium. Native Windows desktop and physical GPU performance have not been tested. Wine and the test-specific Microsoft SDK files are not part of the download; Windows 10/11 supplies the operating-system runtime.

Final download checksum:

```text
d2a6e8bc0f3104f9a6f2e7219cf4ff4467d568434349b0b0ce49fb7c2538e417  MSU-Terrain-Windows-x64.zip
```
