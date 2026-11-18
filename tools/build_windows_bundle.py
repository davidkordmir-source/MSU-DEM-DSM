"""Build a portable Windows x64 ZIP from locked official artifacts. Build-time network only."""

import argparse
import base64
import hashlib
import json
import shutil
import urllib.request
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def fetch(item: dict, cache: Path) -> Path:
    target = cache / item["name"]
    if not target.exists():
        partial = target.with_suffix(".partial")
        with urllib.request.urlopen(item["url"], timeout=120) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        partial.replace(target)
    content = target.read_bytes()
    if "sha256" in item:
        valid = hashlib.sha256(content).hexdigest() == item["sha256"]
    else:
        valid = base64.b64encode(hashlib.sha512(content).digest()).decode() == item["sha512_base64"]
    if not valid:
        raise ValueError(f"Checksum mismatch: {target.name}; discard it and retry from the trusted source")
    return target


def unpack(archive: Path, root: Path, *, runtime: bool = False):
    with ZipFile(archive) as source:
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                raise ValueError("Unsafe archive path")
            if entry.is_dir():
                continue
            if runtime:
                if path.parts[0] != "tools":
                    continue
                dest = root / Path(*path.parts[1:])
            elif path.parts[0].endswith(".data"):
                scheme = path.parts[1]
                places = {
                    "purelib": root / "Lib/site-packages",
                    "platlib": root / "Lib/site-packages",
                    "data": root,
                    "scripts": root / "Scripts",
                    "headers": root / "Include",
                }
                if scheme not in places:
                    raise ValueError(f"Unknown wheel scheme: {scheme}")
                dest = places[scheme] / Path(*path.parts[2:])
            else:
                dest = root / "Lib/site-packages" / Path(*path.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=ROOT / "build/artifacts")
    parser.add_argument("--output", type=Path, default=ROOT / "downloads/MSU-Terrain-Windows-x64.zip")
    parser.add_argument("--no-archive", action="store_true", help="Prepare payload for the desktop installer")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "packaging/windows-lock.json").read_text())
    # Only this tool's generated staging directory is refreshed.
    bundle = ROOT / "build/MSU-Terrain-Windows-x64"
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    unpack(fetch(manifest["runtime"], args.cache), bundle / "runtime", runtime=True)
    for item in manifest["wheels"]:
        unpack(fetch(item, args.cache), bundle / "runtime")
    shutil.copytree(ROOT / "app", bundle / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename in ("run_offline.py", "Start-MSU-Terrain.cmd", "README.md"):
        shutil.copy2(ROOT / filename, bundle / filename)
    launcher_exe = ROOT / "build/launcher/MSU-Terrain.exe"
    if launcher_exe.exists():
        shutil.copy2(launcher_exe, bundle / launcher_exe.name)
    shutil.copy2(ROOT / "packaging/windows-lock.json", bundle / "DEPENDENCIES.json")
    shutil.copy2(ROOT / "tools/offline_smoke.py", bundle / "offline_smoke.py")
    # CMD uses Windows line endings regardless of the build machine.
    launcher = bundle / "Start-MSU-Terrain.cmd"
    launcher.write_bytes(launcher.read_text().replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    (bundle / "START-HERE.txt").write_text(
        "MSU Terrain - Windows 10/11 x64 - Fully offline\n\n"
        "Installed edition: open MSU Terrain from the Desktop or Start menu.\n"
        "MSU-Terrain.exe opens a separate Edge/Chrome app window; use Exit to stop.\n"
        "Portable/source alternative: extract ALL files and open Start-MSU-Terrain.cmd.\n"
        "For that alternative, keep its console open and use Ctrl+C to stop.\n\n"
        "No Python installation, pip, uv, account, Internet, or administrator access is required.\n"
        "A local browser (Edge, Chrome or Firefox) is required.\n"
        "Click DSM in the desktop edition to read TIF/BigTIFF directly from disk.\n"
        "There is no fixed raster file-size or pixel-count cap. Available disk/RAM still apply.\n"
        "Ground is ESTIMATED from the DSM; inspect ground.tif and the report.\n"
        "Output: attributed Shapefile (features.zip), height rasters and ground diagnostics.\n"
        "Optional area: one ZIP containing SHP+SHX+DBF+PRJ, or select those files together.\n"
        "Installed results: %LOCALAPPDATA%\\MSU Terrain\\results; portable results: .data.\n"
        "Uninstall preserves your original inputs, downloaded outputs and application data.\n"
        "All runtimes, GDAL/PROJ data, dependencies, scripts and fonts are bundled.\n"
        "See README.md for the Persian user guide.\n",
        encoding="utf-8",
    )
    checksums = {
        str(path.relative_to(bundle)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    (bundle / "MANIFEST.sha256.json").write_text(json.dumps(checksums, indent=2), encoding="utf-8")
    if args.no_archive:
        print(json.dumps({"bundle": str(bundle), "bundled_files": len(checksums)}))
        return
    with ZipFile(args.output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, Path(bundle.name) / path.relative_to(bundle))
    with ZipFile(args.output) as archive:
        assert archive.testzip() is None
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".zip.sha256").write_text(f"{digest}  {args.output.name}\n")
    print(
        json.dumps(
            {
                "archive": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": digest,
                "bundled_files": len(checksums),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
