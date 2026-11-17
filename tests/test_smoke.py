"""Real-synthesis smoke tests (stdlib unittest, no extra dependencies).

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

These load the actual 326 MB model on purpose: the failure mode this guards
against is a phonemizer problem that returns audio of near-zero length instead
of raising, which a mocked test would never catch.
"""

from __future__ import annotations

import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio import wav_bytes  # noqa: E402
from app.cache import AudioCache  # noqa: E402
from app.config import SAMPLE_RATE  # noqa: E402
from app.text import normalize, split_chunks  # noqa: E402

MODEL = Path(__file__).resolve().parent.parent / "models" / "kokoro-v1.0.onnx"


@unittest.skipUnless(MODEL.exists(), "model files not downloaded")
class TestSynthesis(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app.engines.kokoro import KokoroEngine

        cls.engine = KokoroEngine()

    def test_english_voice_count(self) -> None:
        english = [v for v in self.engine.info.voices if v.language.startswith("en")]
        self.assertEqual(len(english), 28)
        self.assertEqual(self.engine.info.sample_rate, SAMPLE_RATE)

    def test_audio_is_real(self) -> None:
        audio = self.engine.synthesize(
            "Hello from the test suite. This should be audible speech.",
            "af_heart",
            1.0,
        )
        duration = len(audio) / SAMPLE_RATE
        self.assertGreater(duration, 1.0, "audio is implausibly short")
        self.assertGreater(float(abs(audio).max()), 0.05, "audio is silent")
        self.assertLessEqual(float(abs(audio).max()), 1.0, "samples are clipped")

    def test_wav_output_parses(self) -> None:
        audio = self.engine.synthesize("One short sentence.", "am_michael", 1.1)
        with wave.open(io.BytesIO(wav_bytes(audio))) as handle:
            self.assertEqual(handle.getframerate(), SAMPLE_RATE)
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertGreater(handle.getnframes(), SAMPLE_RATE // 2)


class TestChunking(unittest.TestCase):
    def test_limits_respected(self) -> None:
        text = ("A sentence with some words in it. " * 60).strip()
        chunks = split_chunks(text, max_chars=200, first_chars=100)
        self.assertGreater(len(chunks), 5)
        self.assertTrue(all(len(c) <= 200 for c in chunks), "chunk over limit")
        self.assertLessEqual(len(chunks[0]), 100, "first chunk not shortened")
        self.assertEqual(" ".join(chunks).replace("  ", " "), " ".join(text.split()))

    def test_abbreviations_do_not_split(self) -> None:
        chunks = split_chunks("Dr. Smith arrived at 3.14 p.m. on Jan. 5. He left.")
        self.assertEqual(len(chunks), 1)
        self.assertIn("3.14", chunks[0])

    def test_paragraphs_and_blank_text(self) -> None:
        self.assertEqual(split_chunks("   \n\n  "), [])
        self.assertEqual(normalize("Hi\u00a0there \u2014 friend"), "Hi there - friend")


class TestCache(unittest.TestCase):
    def test_lru_roundtrip(self) -> None:
        cache = AudioCache(max_entries=2)
        key = AudioCache.make_key("hello", "af_heart", 1.0)
        self.assertIsNone(cache.get(key))
        cache.put(key, object())
        self.assertIsNotNone(cache.get(key))
        self.assertEqual(cache.stats()["hits"], 1)
        cache.put(AudioCache.make_key("a", "v", 1.0), object())
        cache.put(AudioCache.make_key("b", "v", 1.0), object())
        self.assertEqual(cache.stats()["entries"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
