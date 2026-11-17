"""Measure Kokoro-82M speed on this machine.

Run:  .venv\\Scripts\\python.exe scripts/bench.py            # default 4 threads
      .venv\\Scripts\\python.exe scripts/bench.py --sweep    # 2/4/6/8 threads

Reports model load time, cold vs warm real-time factor (RTF = synth seconds per
audio second, lower is better) and the latency until the first playable chunk
when long text is split sentence-by-sentence.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import onnxruntime as rt  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402

MODEL = ROOT / "models" / "kokoro-v1.0.onnx"
VOICES = ROOT / "models" / "voices-v1.0.bin"

SENTENCES = [
    "Local speech synthesis means no accounts, no quotas, and no text ever leaving this machine.",
    "The model reads each sentence, converts it to phonemes, and predicts an audio waveform twenty four thousand times a second.",
    "Because everything runs on the processor, a long article takes a little longer than it would with a cloud service.",
    "Streaming the audio sentence by sentence hides almost all of that waiting from the listener.",
    "You can pick a different voice, slow the delivery down, or export the whole thing as a wave file.",
]

LONG_TEXT = (
    "Reading long documents aloud is where a local engine earns its keep. "
    "A cloud service would bill you by the character, and many of them quietly cap how much you can synthesize each month. "
    "Here the only cost is electricity. The trade off is speed: a laptop processor from a few years ago manages roughly one to two times real time, "
    "so a ten minute article might take five or six minutes of solid computation. "
    "The trick is to start speaking the first sentence while the rest is still being generated. "
    "With sentence level chunking the listener hears sound in about a second, and the queue stays comfortably ahead of playback. "
    "That makes the experience feel instant even though the work behind it is not."
)


def load(threads: int) -> tuple[Kokoro, float]:
    opts = rt.SessionOptions()
    opts.intra_op_num_threads = threads
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
    t0 = time.time()
    sess = rt.InferenceSession(str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"])
    kokoro = Kokoro.from_session(sess, str(VOICES))
    return kokoro, time.time() - t0


def synth(kokoro: Kokoro, text: str) -> tuple[float, float]:
    """Return (audio seconds, synth seconds)."""
    t0 = time.time()
    audio, sr = kokoro.create(text, voice="af_heart", speed=1.0, lang="en-us")
    elapsed = time.time() - t0
    return len(audio) / sr, elapsed


def run(threads: int, verbose: bool = True) -> dict:
    kokoro, load_s = load(threads)
    if verbose:
        print(f"threads={threads}  model load {load_s:.2f}s")

    audio_s, cold_s = synth(kokoro, SENTENCES[0])
    cold_rtf = cold_s / audio_s
    if verbose:
        print(f"  first call (cold): {audio_s:.2f}s audio in {cold_s:.2f}s "
              f"-> RTF {cold_rtf:.3f}  ({audio_s / cold_s:.2f}x realtime)")

    rtf_samples, speed_samples = [], []
    for sentence in SENTENCES:
        audio_s, synth_s = synth(kokoro, sentence)
        rtf_samples.append(synth_s / audio_s)
        speed_samples.append(audio_s / synth_s)
        if verbose:
            print(f"  {audio_s:5.2f}s audio in {synth_s:5.2f}s -> RTF {synth_s / audio_s:.3f}  ({audio_s / synth_s:.2f}x)")

    # First-chunk latency: split long text, time until chunk one is ready
    from app.text import split_chunks  # local import: needs sys.path setup

    chunks = split_chunks(LONG_TEXT)
    t0 = time.time()
    first_audio, sr = kokoro.create(chunks[0], voice="af_heart", speed=1.0, lang="en-us")
    first_latency = time.time() - t0

    if verbose:
        print(f"  long text -> {len(chunks)} chunks; first chunk "
              f"({len(first_audio) / sr:.2f}s audio) ready in {first_latency:.2f}s")

    return {
        "threads": threads,
        "load": load_s,
        "cold_rtf": cold_rtf,
        "warm_rtf": statistics.mean(rtf_samples),
        "speed_x": statistics.mean(speed_samples),
        "first_chunk": first_latency,
        "chunks": len(chunks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    if not MODEL.exists():
        raise SystemExit("Model missing. Run: .venv\\Scripts\\python.exe scripts/fetch_model.py")

    results = [run(t) for t in ([2, 4, 6, 8] if args.sweep else [args.threads])]

    print("\nsummary")
    print(f"{'threads':>8} {'warm RTF':>9} {'speed':>8} {'first chunk':>12}")
    for r in results:
        print(f"{r['threads']:>8} {r['warm_rtf']:>9.3f} {r['speed_x']:>7.2f}x {r['first_chunk']:>11.2f}s")
    best = min(results, key=lambda r: r["warm_rtf"])
    print(f"\nfastest: {best['threads']} threads (RTF {best['warm_rtf']:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
