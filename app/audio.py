"""PCM/WAV helpers.

Every streamed chunk is sent as a *complete* WAV file so the browser can decode
it with decodeAudioData without knowing anything about the transport.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from .config import SAMPLE_RATE

_SAMPLE_WIDTH = 2  # 16-bit PCM
_CHANNELS = 1
_HEADER_SIZE = 44
_MAX_INT16 = 32767.0


def to_pcm16(samples: np.ndarray) -> bytes:
    """Clip float samples to [-1, 1] and pack them as little-endian int16."""
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * _MAX_INT16).astype("<i2").tobytes()


def wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Frame float samples as a mono 16-bit PCM WAV file."""
    payload = to_pcm16(samples)
    byte_rate = sample_rate * _CHANNELS * _SAMPLE_WIDTH
    block_align = _CHANNELS * _SAMPLE_WIDTH
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH",
        16,               # fmt chunk size
        1,                # PCM
        _CHANNELS,
        sample_rate,
        byte_rate,
        block_align,
        8 * _SAMPLE_WIDTH,
    )
    header += b"data" + struct.pack("<I", len(payload))
    return header + payload


def concat(chunks: list[np.ndarray]) -> np.ndarray:
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    if len(chunks) == 1:
        return np.asarray(chunks[0], dtype=np.float32)
    return np.concatenate([np.asarray(c, dtype=np.float32) for c in chunks])


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(samples, sample_rate))
    return path


def duration_seconds(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    return len(samples) / float(sample_rate)
