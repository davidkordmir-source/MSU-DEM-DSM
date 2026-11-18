"""Build the x64 desktop launcher and offline NSIS installer with no runtime downloads."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-root", type=Path, help="Optional extracted Debian toolchain prefix")
    parser.add_argument("--cache", type=Path, default=ROOT / "build/artifacts")
    args = parser.parse_args()
    prefix = args.tool_root
    tool_bin = prefix / "usr/bin" if prefix else None
    compiler = (
        str(tool_bin / "x86_64-w64-mingw32-gcc-win32") if prefix else shutil.which("x86_64-w64-mingw32-gcc")
    )
    windres = (
        str(tool_bin / "x86_64-w64-mingw32-windres") if prefix else shutil.which("x86_64-w64-mingw32-windres")
    )
    makensis = str(tool_bin / "makensis") if prefix else shutil.which("makensis")
    if not all((compiler, windres, makensis)):
        raise RuntimeError("MinGW-w64 and NSIS 3.11 or newer are required for installer builds")
    build = ROOT / "build/launcher"
    build.mkdir(parents=True, exist_ok=True)
    flags = [f"-B{tool_bin}/"] if prefix else []
    if prefix:
        flags += [
            f"-I{prefix / 'usr/x86_64-w64-mingw32/include'}",
            f"-L{prefix / 'usr/x86_64-w64-mingw32/lib'}",
        ]
    subprocess.run(
        [
            windres,
            "--preprocessor",
            compiler,
            *[item for flag in flags for item in ("--preprocessor-arg", flag)],
            "--preprocessor-arg",
            "-E",
            "--preprocessor-arg",
            "-xc",
            "--preprocessor-arg",
            "-DRC_INVOKED",
            "windows_launcher.rc",
            "-O",
            "coff",
            "-o",
            str(build / "icon.o"),
        ],
        cwd=ROOT / "packaging",
        check=True,
    )
    subprocess.run(
        [
            compiler,
            *flags,
            "-municode",
            "-mwindows",
            "-O2",
            "-s",
            "-static-libgcc",
            str(ROOT / "packaging/windows_launcher.c"),
            str(build / "icon.o"),
            "-lshell32",
            "-lole32",
            "-o",
            str(build / "MSU-Terrain.exe"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/build_windows_bundle.py"),
            "--cache",
            str(args.cache),
            "--no-archive",
        ],
        check=True,
    )
    payload = ROOT / "build/MSU-Terrain-Windows-x64"
    uninstall_lines = []

    def nsis_path(path):
        return (
            str(path.relative_to(payload))
            .replace("\\", "/")
            .replace("/", "\\")
            .replace("$", "$$")
            .replace('"', '$\\"')
        )

    for path in sorted(payload.rglob("*")):
        if path.is_file():
            uninstall_lines.append(f'Delete "$INSTDIR\\{nsis_path(path)}"')
    for path in sorted(
        (p for p in payload.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        uninstall_lines.append(f'RMDir "$INSTDIR\\{nsis_path(path)}"')
    (ROOT / "build/uninstall-files.nsh").write_text("\n".join(uninstall_lines) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    if prefix:
        environment["NSISDIR"] = str(prefix / "usr/share/nsis")
    subprocess.run([makensis, "-V2", "installer.nsi"], cwd=ROOT / "packaging", env=environment, check=True)
    output = ROOT / "downloads/MSU-Terrain-Setup-0.4.0-x64.exe"
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".exe.sha256").write_text(f"{digest}  {output.name}\n")
    print(json.dumps({"installer": str(output), "bytes": output.stat().st_size, "sha256": digest}))


if __name__ == "__main__":
    main()
