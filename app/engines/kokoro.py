"""Kokoro-82M backend (ONNX Runtime, CPU)."""

from __future__ import annotations

import threading
import time

import numpy as np
import onnxruntime as rt
from kokoro_onnx import Kokoro

from ..config import MODEL_PATH, NUM_THREADS, SAMPLE_RATE, VOICES_PATH
from .base import EngineInfo, Voice

# Kokoro voice ids are <lang><gender>_<name>: a=US English, b=UK English,
# e=Spanish, f=French, h=Hindi, i=Italian, j=Japanese, p=Brazilian Portuguese,
# z=Mandarin. The app is English-first but still lists the others.
_LANGUAGES = {
    "a": ("en-US", "English (US)", "American"),
    "b": ("en-GB", "English (UK)", "British"),
    "e": ("es", "Spanish", ""),
    "f": ("fr", "French", ""),
    "h": ("hi", "Hindi", ""),
    "i": ("it", "Italian", ""),
    "j": ("ja", "Japanese", ""),
    "p": ("pt-BR", "Portuguese (Brazil)", ""),
    "z": ("zh", "Chinese", ""),
}

_DISPLAY_NAMES = {
    "af_alloy": "Alloy", "af_aoede": "Aoede", "af_bella": "Bella",
    "af_heart": "Heart", "af_jessica": "Jessica", "af_kore": "Kore",
    "af_nicole": "Nicole", "af_nova": "Nova", "af_river": "River",
    "af_sarah": "Sarah", "af_sky": "Sky",
    "am_adam": "Adam", "am_echo": "Echo", "am_eric": "Eric",
    "am_fenrir": "Fenrir", "am_liam": "Liam", "am_michael": "Michael",
    "am_onyx": "Onyx", "am_puck": "Puck", "am_santa": "Santa",
    "bf_alice": "Alice", "bf_emma": "Emma", "bf_isabella": "Isabella",
    "bf_lily": "Lily", "bf_lauren": "Lauren",
    "bm_daniel": "Daniel", "bm_fable": "Fable", "bm_george": "George",
    "bm_lewis": "Lewis",
}


def _describe(voice_id: str) -> Voice:
    prefix = voice_id[:1]
    language, language_name, accent = _LANGUAGES.get(prefix, ("en-US", "English", ""))
    gender = "female" if voice_id[1:2] == "f" else "male"
    name = _DISPLAY_NAMES.get(voice_id, voice_id.split("_", 1)[-1].title())
    return Voice(
        id=voice_id,
        name=name,
        language=language,
        language_name=language_name,
        gender=gender,
        accent=accent,
    )


class KokoroEngine:
    def __init__(
        self,
        model_path=MODEL_PATH,
        voices_path=VOICES_PATH,
        threads: int = NUM_THREADS,
    ) -> None:
        if not model_path.exists() or not voices_path.exists():
            raise RuntimeError(
                "Model files are missing. Run: .venv\\Scripts\\python.exe scripts/fetch_model.py"
            )

        options = rt.SessionOptions()
        # Physical cores beat hyperthreads for this graph, and the default
        # (all 8 logical cores) also starves the browser running alongside it.
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL

        started = time.time()
        session = rt.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._kokoro = Kokoro.from_session(session, str(voices_path))
        self.load_seconds = time.time() - started

        # One InferenceSession run at a time: concurrent runs just contend for
        # the same cores and make every request slower.
        self._lock = threading.Lock()
        self.warm = False
        self.warm_seconds = 0.0

        voices = [_describe(v) for v in self._kokoro.get_voices()]
        order = {"en-US": 0, "en-GB": 1}
        voices.sort(key=lambda v: (order.get(v.language, 2), v.language, v.name))
        self.info = EngineInfo(
            name="Kokoro-82M",
            model=model_path.name,
            sample_rate=SAMPLE_RATE,
            voices=voices,
        )

    def warm_up(self) -> None:
        """Pay the first-inference cost (ORT kernel init) before the user asks."""
        started = time.time()
        with self._lock:
            self._kokoro.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")
        self.warm_seconds = time.time() - started
        self.warm = True

    def synthesize(self, text: str, voice_id: str, speed: float) -> np.ndarray:
        with self._lock:
            audio, _ = self._kokoro.create(
                text, voice=voice_id, speed=speed, lang="en-us"
            )
        audio = np.asarray(audio, dtype=np.float32)
        if audio.size == 0:
            # The known failure mode is a phonemizer problem returning no audio.
            raise ValueError("The engine produced no audio for this text.")
        return audio
