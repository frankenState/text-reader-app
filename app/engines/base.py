"""Engine interface. A second backend (Piper, Pocket TTS) only needs to match this."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str        # BCP-47-ish tag, e.g. "en-US"
    language_name: str
    gender: str
    accent: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class EngineInfo:
    name: str
    model: str
    sample_rate: int
    voices: list[Voice] = field(default_factory=list)


@runtime_checkable
class TTSBackend(Protocol):
    info: EngineInfo

    def synthesize(self, text: str, voice_id: str, speed: float) -> np.ndarray:
        """Return mono float32 samples in [-1, 1] at info.sample_rate."""
        ...
