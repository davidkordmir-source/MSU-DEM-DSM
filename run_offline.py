"""Launch a local browser application using the bundled interpreter; never install at runtime."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # Also works with python -I in the portable distribution.


def app_data_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "MSU Terrain"
    return ROOT / ".data"


def launch_desktop_window(url: str, data: Path):
    """Use the installed browser's app mode: a separate window without tabs/address bar."""
    if os.name != "nt":
        webbrowser.open(url)
        return None
    roots = [os.environ.get(name, "") for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
        for root in roots:
            executable = Path(root) / relative
            if root and executable.is_file():
                return subprocess.Popen(
                    [
                        str(executable),
                        f"--app={url}",
                        f"--user-data-dir={data / 'browser-profile'}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-mode",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--disable-domain-reliability",
                        "--window-size=1440,980",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
    webbrowser.open(url)
    return None


def main():
    parser = argparse.ArgumentParser(description="MSU Terrain offline launcher")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--port", type=int, default=0, help="Local port; 0 selects a free port")
    parser.add_argument("--desktop", action="store_true", help="Open a standalone browser app window")
    parser.add_argument("--installed", action="store_true", help="Store writable files in LocalAppData")
    args = parser.parse_args()
    os.chdir(ROOT)
    data = app_data_directory() if args.installed else ROOT / ".data"
    data.mkdir(parents=True, exist_ok=True)
    if args.installed:
        os.environ.setdefault("MSU_DATA_DIR", str(data / "results"))
    if sys.stdout is None or sys.stderr is None:
        log = (data / "application.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    from app.offline import enable_offline_mode

    enable_offline_mode()
    import uvicorn

    from app.main import app

    # Reserve the actual listening socket before opening a browser, avoiding port races.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    def open_when_ready():
        # Ignore inherited proxies only for this loopback health request.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            try:
                with opener.open(url + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        if args.desktop:
                            process = launch_desktop_window(url, data)
                            if process:
                                time.sleep(3)
                                if process.poll() is None:
                                    process.wait()
                                    server.should_exit = True
                        else:
                            webbrowser.open(url)
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)

    print(f"\nMSU Terrain | Offline\nOpen: {url}\nKeep this window open. Press Ctrl+C to exit.\n", flush=True)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, workers=1, access_log=False, log_level="warning"
    )
    server = uvicorn.Server(config)
    if args.desktop:
        app.state.desktop_session = secrets.token_urlsafe(32)
        app.state.request_exit = lambda: setattr(server, "should_exit", True)
    if args.installed:
        (data / "last-session.json").write_text(json.dumps({"url": url, "pid": os.getpid()}))
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
