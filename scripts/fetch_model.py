"""Download the Kokoro-82M ONNX model and voice bank into ./models.

Run:  .venv\\Scripts\\python.exe scripts/fetch_model.py

Downloads are resumable-ish (partial files are deleted on failure) and are
verified against a pinned SHA-256 once one has been recorded in
models/checksums.json. The first run records the hashes it saw.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = Path(os.environ.get("TTS_MODELS_DIR", ROOT / "models"))
CHECKSUMS = MODELS_DIR / "checksums.json"

FILES = {
    "kokoro-v1.0.onnx": (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/kokoro-v1.0.onnx"
    ),
    "voices-v1.0.bin": (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/voices-v1.0.bin"
    ),
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:3.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def download(name: str, url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {name} ...", flush=True)
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_print = 0.0
            with tmp.open("wb") as out:
                while True:
                    block = resp.read(1024 * 256)
                    if not block:
                        break
                    out.write(block)
                    done += len(block)
                    now = time.time()
                    if now - last_print > 1.5:
                        last_print = now
                        rate = done / max(now - started, 0.001)
                        pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                        print(
                            f"    {pct}  {human(done)} / {human(total)}"
                            f"  ({human(rate)}/s)",
                            flush=True,
                        )
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"Download failed for {name}: {exc}\n"
            "If you are behind a proxy, set HTTPS_PROXY and retry."
        ) from exc

    tmp.replace(dest)
    print(f"    done in {time.time() - started:.1f}s -> {dest}", flush=True)


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    known = {}
    if CHECKSUMS.exists():
        known = json.loads(CHECKSUMS.read_text(encoding="utf-8"))

    for name, url in FILES.items():
        dest = MODELS_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"{name} already present ({human(dest.stat().st_size)})")
        else:
            download(name, url, dest)

        digest = sha256_of(dest)
        expected = known.get(name)
        if expected is None:
            print(f"  {name}: sha256 {digest} (recorded)")
        elif expected != digest:
            raise SystemExit(
                f"Checksum mismatch for {name}!\n  expected {expected}\n  got      {digest}\n"
                "Delete the file and re-run to fetch a clean copy."
            )
        else:
            print(f"  {name}: sha256 verified")
        known[name] = digest if expected is None else expected

    CHECKSUMS.write_text(json.dumps(known, indent=2) + "\n", encoding="utf-8")
    print(f"\nModel files ready in {MODELS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
