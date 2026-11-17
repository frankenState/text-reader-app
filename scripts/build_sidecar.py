"""Freeze the backend into a one-folder Windows build and stage it for Tauri.

    .venv\\Scripts\\python.exe scripts\\build_sidecar.py

Produces `sidecar/localvoice-server/` (PyInstaller output) and copies it, plus
the model weights, into `src-tauri/` where the Tauri bundler picks them up as
resources.

A one-folder build is deliberate: a one-file build would unpack ~90 MB of
DLLs into a temp folder on every launch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "sidecar"
WORK = ROOT / "build" / "pyinstaller"
MODELS = ROOT / "models"

STAGE = ROOT / "src-tauri" / "sidecar"
STAGE_MODELS = ROOT / "src-tauri" / "models"

MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


def log(message: str) -> None:
    print(f"[sidecar] {message}", flush=True)


def freeze() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--noconsole",
        "--name",
        "localvoice-server",
        "--distpath",
        str(DIST),
        "--workpath",
        str(WORK / "work"),
        "--specpath",
        str(WORK),
        "--paths",
        str(ROOT),
        "--add-data",
        f"{ROOT / 'web'}{os.pathsep}web",
        # espeak-ng ships a data folder inside the package; PyInstaller misses
        # it on its own and the model then returns near-silent audio.
        "--collect-all",
        "espeakng_loader",
        "--collect-data",
        "kokoro_onnx",
        "--collect-binaries",
        "onnxruntime",
        # uvicorn is handed the app object, but the module still has to exist.
        "--hidden-import",
        "app.main",
        str(ROOT / "run.py"),
    ]
    log("running PyInstaller (this takes a minute)")
    subprocess.run(command, check=True, cwd=ROOT)


def stage() -> None:
    built = DIST / "localvoice-server"
    exe = built / "localvoice-server.exe"
    if not exe.exists():
        raise SystemExit("[sidecar] PyInstaller produced no executable")

    if STAGE.exists():
        shutil.rmtree(STAGE)
    log(f"staging the engine in {STAGE.relative_to(ROOT)}")
    shutil.copytree(built, STAGE)

    if STAGE_MODELS.exists():
        shutil.rmtree(STAGE_MODELS)
    STAGE_MODELS.mkdir(parents=True)
    for name in MODEL_FILES:
        source = MODELS / name
        if not source.exists():
            raise SystemExit(f"[sidecar] missing {source}; run scripts/fetch_model.py first")
        log(f"staging {name} ({source.stat().st_size / 1e6:.0f} MB)")
        shutil.copy2(source, STAGE_MODELS / name)


def main() -> int:
    started = time.perf_counter()
    freeze()
    stage()
    size = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    size += sum(f.stat().st_size for f in STAGE_MODELS.rglob("*") if f.is_file())
    log(f"done in {time.perf_counter() - started:.0f}s, {size / 1e6:.0f} MB staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
