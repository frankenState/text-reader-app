"""Start the local TTS server and open the browser.

    .venv\\Scripts\\python.exe run.py
    .venv\\Scripts\\python.exe run.py --no-browser --port 8800
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import HOST, PORT


def free_port(host: str, preferred: int, attempts: int = 10) -> int:
    """Return the preferred port if free, otherwise the next free one."""
    for candidate in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                print(f"port {candidate} is busy, trying {candidate + 1}")
                continue
            return candidate
    raise SystemExit(f"No free port found in {preferred}..{preferred + attempts}")


def open_when_ready(url: str, port: int, timeout: float = 60.0) -> None:
    """Wait for the server to accept connections, then open the browser once."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Kokoro text-to-speech server")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", help="developer auto-reload")
    args = parser.parse_args()

    if args.reload:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True)
        return 0

    # A frozen (PyInstaller) build has no importable "app.main:app" string path,
    # so hand uvicorn the app object itself.
    if getattr(sys, "frozen", False):
        from app.main import app as fastapi_app

        target: object = fastapi_app
    else:
        target = "app.main:app"

    port = free_port(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    print(f"\n  Local Voice  ->  {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Thread(
            target=open_when_ready, args=(url, port), daemon=True
        ).start()

    uvicorn.run(target, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
