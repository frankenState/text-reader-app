# Local Voice

Offline text-to-speech for Windows. Paste text, pick a voice, press play — or save a WAV.
No account, no API key, no character quota, and nothing ever leaves the machine.

Powered by **Kokoro-82M** running on ONNX Runtime on the CPU. No GPU required, no PyTorch.

```
text → sentence chunks → Kokoro (ONNX, 4 threads) → WAV chunks → browser Web Audio
```


---

## Installer Download Links:
- Local Voice: [v1.0.1](https://drive.google.com/file/d/1b4Uwy6vDQoPRZF2O1SDxTMYYz3HFlWqB/view?usp=sharing)

## Quick start

Double-click **`start.bat`**. It starts the server, opens the browser, and downloads the
model on first run (~340 MB).

Or from a terminal:

```bash
.venv\Scripts\python.exe run.py
```

The app opens at <http://127.0.0.1:8765/>. Press `Ctrl+C` in the console to stop it.

## Desktop app

Double-click **`package.bat`** to build a Windows installer. It asks for the release version
(`X.X.X`), offering the next patch as the recommended value — press Enter to accept it.

```
Current version   : 1.0.0
Recommended       : 1.0.1

Release version [X.X.X], Enter for 1.0.1:
```

The script writes that version into `src-tauri/tauri.conf.json`, `Cargo.toml` and
`package.json`, freezes the Python engine, and bundles it with the model weights. The result
is `releases/Local Voice_<version>_x64-setup.exe` (~350 MB, everything included: the first
launch needs no download).

How the installed app runs:

```
Local Voice.exe (Tauri, WebView2)
   └─ spawns sidecar\localvoice-server.exe on a free loopback port
         └─ serves the same UI and the /api routes from 127.0.0.1:<port>
   ├─ splash page while the model loads (~4 s)
   └─ navigates the window to the app once the port answers
```

Closing the window kills the engine process. The engine's log is in
`%LOCALAPPDATA%\com.localvoice.desktop\logs\engine.log`.

The build needs Node, the Rust toolchain with MSVC build tools, and about 4 GB of free space
for cargo output. `CARGO_TARGET_DIR` is set to `.cargo-target` inside the project so the build
never fills the system drive.

## First-time setup

Only needed once, or after moving the project to another machine.

```powershell
# 1. uv (used only to fetch Python 3.12 and resolve packages)
python -m pip install --user uv

# 2. Python 3.12 — the TTS runtime does not support 3.14
uv python install 3.12

# 3. virtualenv + dependencies from uv.lock (no PyTorch: ONNX Runtime only)
uv sync

# 4. model files (~340 MB, SHA-256 recorded in models/checksums.json)
.venv\Scripts\python.exe scripts\fetch_model.py
```

Everything lives on the D: drive by default: the venv, the model, the caches and exported
audio. Nothing is written to `C:\Users\...` except a few kilobytes of tooling.

## Using it

| Control | What it does |
|---|---|
| **Voice** | 54 built-in voices; the 28 English ones are listed first (20 US, 8 UK) |
| **Speed** | 0.6× – 1.6×, applied at synthesis time (not a pitch-shifting resample) |
| **Play / Stop** | Streams and speaks; Stop aborts the request so the server stops generating |
| `Ctrl` + `Enter` | Play, or stop if already speaking |
| `Esc` | Stop |
| **Save WAV** | Renders the whole text to a 24 kHz 16-bit mono file |

Playback starts as soon as the first sentence is ready (typically 2–5 s) instead of waiting
for the whole document. The footer shows the measured time-to-first-audio for the last run.

## Measured performance (this machine)

Intel i5-8265U, 4 cores / 8 threads, 16 GB RAM, no GPU, `TTS_THREADS=4`:

| Metric | Measured |
|---|---|
| Model load (cold / warm) | 5.4–7.6 s / ~2 s |
| Engine warm-up | ~1 s |
| Sustained throughput | **1.2–1.7× realtime** (RTF 0.60–0.84) |
| First audio chunk | 1.5–4.8 s |
| Model disk / RAM | 310 MB + 27 MB / roughly 1 GB RSS |
| Repeat playback of cached text | instant (LRU of 256 chunks) |

Thread sweep from `scripts/bench.py --sweep`: 2 threads 0.96×, **4 threads 1.22×**,
6 threads 1.27×, 8 threads 1.14×. Four threads leaves headroom for the browser and audio,
which matters more than the last few percent of synthesis speed.

The practical consequence: a 10-minute article takes roughly 6–8 minutes of computation,
but playback begins in seconds and the queue stays ahead of the voice. If synthesis ever
falls behind (heavy CPU load), the UI shows `Buffering…` rather than silently skipping audio.

## Architecture

```
run.py                     launcher: picks a free port, opens the browser
app/main.py                FastAPI: /api/health, /api/voices, /api/tts/stream, /api/tts/file
app/engines/kokoro.py      ONNX session (intra_op=4, sequential), voice metadata, warm-up
app/text.py                normalization, abbreviation-aware sentence split, chunk merging
app/audio.py               float -> int16, RIFF/WAVE framing
app/cache.py               256-entry LRU keyed on (voice, speed, sha1(text))
web/                       single page: vanilla JS + Web Audio API, no build step
web/fonts/                 self-hosted Figtree (SIL OFL), so the UI needs no network
shell/                     splash page shown by the desktop app while the model loads
scripts/fetch_model.py     resumable download + SHA-256 verification
scripts/bench.py           RTF, first-chunk latency, thread sweep
scripts/build_sidecar.py   PyInstaller freeze + staging for the desktop bundle
scripts/set_version.py     writes the release version into all three manifests
tests/test_smoke.py        real synthesis assertions (stdlib unittest)
package.bat                interactive installer build
src-tauri/                 Tauri shell: spawns the sidecar, owns the window
```

Two details carry the design:

1. **Chunking.** The first chunk is capped at ~140 chars so audio starts fast; later chunks
   are up to 320 chars, which stays below Kokoro's ~510-phoneme batch limit while keeping
   per-call overhead low. Each chunk is sent as a *complete* WAV file, so the browser can
   decode and schedule it without knowing anything about the transport.
2. **Web Audio scheduling.** Chunks are queued on a single `AudioContext` clock and played
   back to back, with a ~3 s prebuffer before the first note. A late chunk shifts the clock
   forward instead of playing in the past, which converts a stall into a visible gap rather
   than garbled audio.

Synthesis is serialized behind one lock: running two batches concurrently on 4 cores only
makes both slower. A request that disconnects is abandoned at the next chunk boundary.

## Configuration

All optional, read from the environment at startup:

| Variable | Default | Meaning |
|---|---|---|
| `TTS_PORT` | `8765` | Preferred port; the launcher picks the next free one if busy |
| `TTS_THREADS` | `4` | ONNX intra-op threads (physical cores) |
| `TTS_CHUNK_CHARS` | `320` | Max characters per synthesis chunk |
| `TTS_FIRST_CHUNK_CHARS` | `140` | First-chunk cap; lower = faster start, less efficient |
| `TTS_MODELS_DIR` | `./models` | Where the model files live |
| `TTS_EXPORTS_DIR` | `./exports` | Where `save()` helpers write files |

## Design

The UI deliberately follows one system rather than a stack of effects: a single warm neutral
ground, one sienna accent used only for the primary action, focus and progress, one 4/8/12/16
spacing scale, one radius family and one button pair. The text you are about to hear is set in
a serif manuscript face at a 62-character measure, while the surrounding chrome stays in
Figtree. Exactly one button is filled at a time — Stop takes the fill while speaking, and Play
steps back. Hover and press states move by 1px; nothing animates on scroll; everything rests
under `prefers-reduced-motion`.

## Development

```bash
.venv\Scripts\python.exe run.py --reload --no-browser   # auto-reload
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts\bench.py --sweep
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Model files are missing` | Run `scripts\fetch_model.py` (needs internet once) |
| Browser shows "Engine did not start" | Check the console window; usually a missing model file |
| Audio is silent or a fraction of a second | Phonemizer failure. Re-run `scripts\fetch_model.py`; the smoke test asserts real audio |
| `DLL load failed` importing onnxruntime | Install the Visual C++ 2019+ redistributable |
| Playback keeps saying `Buffering…` | Something else is using the CPU; close it or lower `TTS_THREADS` |
| Very slow after a few minutes | Laptop thermal throttling; 1.6 GHz ultrabooks sustain ~1.2× realtime |
| Port already in use | The launcher picks the next free port and prints it |
| `python -m pip install kokoro-onnx` fails | You are on Python 3.14; the package requires < 3.14 — use the 3.12 venv |

## Licences

- Kokoro-82M model weights: Apache-2.0 — commercial use of the generated audio is allowed.
- `kokoro-onnx` wrapper: MIT.
- ONNX Runtime: MIT. espeak-ng (bundled for phonemization): GPL-3.0, invoked as a separate
  component and not linked into this application's code.
- This app: use it however you like.

## Limitations

- English is the tested path. Other voices are listed and phonemize, but long non-English
  text is not chunked for that language's rules.
- No voice cloning (that needs a different, much slower CPU model — Kyutai Pocket TTS).
- WAV only. MP3/Opus would need ffmpeg on `PATH`.
- Loopback only and no authentication: it is a personal local tool, not a server to expose.
