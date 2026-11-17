"""Central configuration. Paths deliberately default to the project folder (D:)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundle_root() -> Path:
    """Where bundled data lives: the source tree, or the frozen app's data dir."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets _MEIPASS to the folder holding the added data
        # (`_internal` in a one-folder build).
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


ROOT = _bundle_root()

MODELS_DIR = Path(os.environ.get("TTS_MODELS_DIR", ROOT / "models"))
WEB_DIR = Path(os.environ.get("TTS_WEB_DIR", ROOT / "web"))
MODEL_PATH = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"
EXPORTS_DIR = Path(os.environ.get("TTS_EXPORTS_DIR", ROOT / "exports"))

# Loopback only: this app has no auth because it is not meant to be exposed.
HOST = "127.0.0.1"
PORT = int(os.environ.get("TTS_PORT", "8765"))

# 4 = physical cores on this CPU. Hyperthreads measured slower for this graph.
NUM_THREADS = int(os.environ.get("TTS_THREADS", "4"))

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
MIN_SPEED = 0.5
MAX_SPEED = 2.0

# One Kokoro batch is reliable up to ~510 phonemes; ~320 chars stays under it.
MAX_CHUNK_CHARS = int(os.environ.get("TTS_CHUNK_CHARS", "320"))
# Small opening chunk: cuts time-to-first-sound from ~11s to ~2s on this CPU.
MAX_FIRST_CHUNK_CHARS = int(os.environ.get("TTS_FIRST_CHUNK_CHARS", "140"))
MIN_CHUNK_CHARS = 60

# Total input guard so a pasted book cannot exhaust memory.
MAX_TEXT_CHARS = 50_000
MAX_CACHE_ENTRIES = 256
