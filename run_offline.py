"""Launch a local browser application using the bundled interpreter; never install at runtime."""

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # Also works with python -I in the portable distribution.


def main():
    parser = argparse.ArgumentParser(description="MSU Terrain offline launcher")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--port", type=int, default=0, help="Local port; 0 selects a free port")
    args = parser.parse_args()
    os.chdir(ROOT)
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
                        webbrowser.open(url)
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)

    print(f"\nMSU Terrain | Offline\nOpen: {url}\nKeep this window open. Press Ctrl+C to exit.\n", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, workers=1, access_log=False, log_level="warning"
    )
    try:
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
