# Local Voice — Offline Text-to-Speech Desktop App

**A local text-to-speech app that reads any text aloud with 54 built-in voices, entirely on your own CPU. No account, no API key, no character quota, and nothing ever leaves the machine.**

Built as a full product rather than a demo: a streaming synthesis server, a no-build web interface, a packaged Windows desktop app, model download and verification tooling, a benchmark harness, and real-synthesis smoke tests.

---

## Table of Contents

- [Overview](#overview)
- [The Problem](#the-problem)
- [What It Does](#what-it-does)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [How a Request Flows](#how-a-request-flows)
- [Key Engineering Decisions](#key-engineering-decisions)
- [Measured Performance](#measured-performance)
- [Desktop Packaging](#desktop-packaging)
- [Testing & Tooling](#testing--tooling)
- [Challenges & What I Learned](#challenges--what-i-learned)
- [Limitations](#limitations)
- [Links](#links)

---

## Overview

Local Voice turns pasted text into speech using **Kokoro-82M**, an 82-million-parameter text-to-speech model, executed through **ONNX Runtime on the CPU**. There is no PyTorch, no GPU requirement, and no cloud call at any point — the model weights live on disk and inference happens in-process.

The interface is a single page: a text area on the left, a playback rail on the right. Press Play and audio begins within a couple of seconds, streaming sentence by sentence while the rest of the document is still being synthesized. Press Save WAV and the whole text is rendered to a single 24 kHz 16-bit mono file.

It ships two ways from the same codebase: a double-click `start.bat` that runs it in the browser, and a `package.bat` pipeline that produces a ~350 MB Windows installer containing the frozen Python engine and the model weights, so an installed copy needs no download on first launch.

```
text → sentence chunks → Kokoro (ONNX, 4 threads) → WAV chunks → browser Web Audio
```

---

## The Problem

Commercial text-to-speech is metered. Character quotas, subscription tiers, API keys, and — for anything sensitive — a copy of your document sitting on someone else's server. Reading a long article or a chapter of a book out loud is exactly the use case those quotas are designed to discourage.

Small open TTS models have meanwhile become good enough to run on a laptop CPU. The gap is not the model; it is the product around it: latency that makes you wait eleven seconds before the first word, packaging that requires a Python environment, and a UI that treats "generate an mp3" as the whole interaction.

Local Voice closes that gap. The constraints I set for myself:

- **Fully offline** after the one-time model download — the UI self-hosts its own font, so it makes zero network requests.
- **Usable on a 4-core ultrabook with no GPU**, not just a workstation.
- **Audio starts in seconds**, not after the whole document is rendered.
- **One artifact a non-developer can install**, not "clone this and pip install".

---

## What It Does

| Capability | Detail |
|---|---|
| **54 built-in voices** | 28 English voices (20 US, 8 UK) listed first, plus Spanish, French, Hindi, Italian, Japanese, Portuguese and Mandarin |
| **Speed control** | 0.6×–1.6×, applied at synthesis time rather than as a pitch-shifting resample, so the voice stays natural |
| **Streaming playback** | Audio starts as soon as the first sentence is ready (typically 1.5–4.8 s), not when the document finishes |
| **Instant Stop** | Aborts the HTTP request so the server stops generating, instead of muting audio that is still being computed |
| **Save WAV** | Renders the full text to a single 24 kHz / 16-bit / mono file with a filename derived from the text |
| **Keyboard-first** | `Ctrl`+`Enter` plays or stops, `Esc` stops |
| **Honest progress** | Shows `Generating 7/23`, and `Buffering…` when synthesis falls behind — never silently skips audio |
| **Repeat playback** | An LRU cache of 256 chunks keyed on `(voice, speed, sha1(text))` makes replaying the same text instant |
| **Portable desktop build** | Tauri shell + bundled engine + bundled weights; closing the window kills the engine process |

---

## Tech Stack

### Backend — Python 3.12

| Layer | Technology | Why |
|---|---|---|
| **TTS model** | Kokoro-82M (`kokoro-onnx` 0.6.1) | 82M parameters, Apache-2.0, genuinely good prosody at a size that runs on CPU |
| **Inference runtime** | ONNX Runtime 1.20+ (`CPUExecutionProvider`) | No PyTorch. A ~90 MB runtime instead of a ~2 GB dependency, and predictable thread control |
| **API server** | FastAPI + Uvicorn | Async endpoints, streaming responses, and dependency-light static file serving |
| **Validation** | Pydantic v2 | Request models with hard bounds on speed and text length |
| **Numerics** | NumPy | Sample concatenation and float→int16 conversion |
| **Audio I/O** | `soundfile`, stdlib `struct` / `wave` | WAV framing written by hand — 44-byte RIFF header, no ffmpeg on `PATH` |
| **Phonemization** | espeak-ng (bundled via `espeakng_loader`) | Kokoro's text→phoneme front end |
| **Environment** | `uv` + `uv.lock` | Reproducible resolution and a pinned Python 3.12 interpreter |
| **Packaging** | PyInstaller (one-folder) | Freezes the engine into a sidecar executable for the desktop bundle |

### Frontend — vanilla, no build step

| Layer | Technology | Why |
|---|---|---|
| **Markup & styling** | Hand-written HTML + CSS | No framework, no bundler, no `node_modules` at runtime. The `web/` folder is served as-is |
| **Logic** | Vanilla ES2020 JavaScript | ~500 lines; the only real complexity is the audio scheduler |
| **Audio** | Web Audio API (`AudioContext`, `decodeAudioData`, `AudioBufferSourceNode`) | Sample-accurate scheduling on a single clock, which is what makes gapless streaming possible |
| **Typography** | Self-hosted Figtree (SIL OFL) | Zero network requests; the app works with the Wi-Fi off |

### Desktop shell

| Layer | Technology | Why |
|---|---|---|
| **Shell** | Tauri 2 (Rust) | Uses the system WebView2 instead of shipping Chromium — the installer stays ~350 MB with the model included |
| **Process management** | Rust `std::process::Command` | Spawns the sidecar on a free loopback port, polls it, then navigates the window |
| **Installer** | NSIS via `tauri build --bundles nsis` | Per-user install, no admin prompt |
| **Build tooling** | Node (Tauri CLI) + Rust/MSVC + PyInstaller | Driven entirely by `package.bat` |

> **Not used, deliberately:** PyTorch, a JS framework, a bundler, a database, authentication, Docker, or any hosted service.

---

## Architecture

### System layout

```
┌─────────────────────────────────────────────────────────────┐
│  Local Voice.exe  (Tauri 2 / Rust / WebView2)               │
│                                                             │
│  1. binds a free loopback port                              │
│  2. spawns sidecar\localvoice-server.exe                    │
│  3. shows splash/index.html while the model loads           │
│  4. polls the port, then navigates to http://127.0.0.1:port │
│  5. kills the child process on window close                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP (loopback only)
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI engine  (frozen with PyInstaller)                   │
│                                                             │
│  /api/health         readiness, model, cache stats           │
│  /api/voices         voice catalogue (54 entries)            │
│  /api/tts/stream     chunked WAV stream for playback         │
│  /api/tts/file       one concatenated WAV for download       │
│  /                   static web/ (html + css + js + fonts)   │
│                                                             │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ app/text.py│→ │app/engines/  │→ │ app/audio.py       │   │
│  │ chunking   │  │kokoro.py     │  │ float32 → WAV      │   │
│  └────────────┘  │ ONNX Runtime │  └────────────────────┘   │
│                  └──────┬───────┘            │              │
│                  ┌──────▼───────┐            │              │
│                  │ app/cache.py │            │              │
│                  │ LRU 256      │            │              │
│                  └──────────────┘            │              │
└──────────────────────────────────────────────┼──────────────┘
                                               │ application/octet-stream
┌──────────────────────────────────────────────▼──────────────┐
│  Browser / WebView  (web/app.js)                             │
│  fetch stream → split RIFF frames → decodeAudioData          │
│  → queue on one AudioContext clock → prebuffer ~3 s → play   │
└─────────────────────────────────────────────────────────────┘
```

### Module responsibilities

| Path | Responsibility |
|---|---|
| `run.py` | Launcher: picks the next free port if 8765 is busy, starts Uvicorn, opens the browser once the port answers, handles the frozen-build case where uvicorn cannot import by string path |
| `app/main.py` | FastAPI app, lifespan-managed engine + cache + synthesis semaphore, four API routes, static mount |
| `app/engines/kokoro.py` | ONNX session configuration (4 intra-op threads, 1 inter-op, sequential, full graph optimization), voice metadata and ordering, warm-up inference, serialized `synthesize()` |
| `app/engines/base.py` | `TTSBackend` protocol + `Voice` / `EngineInfo` dataclasses — a second backend only has to match this interface |
| `app/text.py` | Unicode normalization, abbreviation-aware sentence splitting, greedy chunk merging with a shortened first chunk, hard-split fallback for pathological tokens |
| `app/audio.py` | Clip to [-1, 1], pack little-endian int16, frame a complete RIFF/WAVE file, concatenate, write |
| `app/cache.py` | Thread-safe LRU keyed on `(voice, speed, sha1(text))` with hit/miss stats |
| `app/config.py` | Every path, port, thread count and limit in one place, all overridable by environment variables; resolves the bundle root differently when frozen |
| `web/` | Single page: markup, stylesheet, ~500-line player, self-hosted font |
| `shell/` | Splash page shown by the desktop app while the engine loads |
| `src-tauri/` | Rust shell: port binding, child process lifecycle, port polling, log redirection, splash error reporting |
| `scripts/fetch_model.py` | Resumable download of the model + voice bank (~340 MB) with SHA-256 verification against recorded checksums |
| `scripts/build_sidecar.py` | PyInstaller one-folder freeze, then stages the executable and weights where the Tauri bundler will find them |
| `scripts/bench.py` | Real-time factor, first-chunk latency, and a thread-count sweep |
| `scripts/set_version.py` | Writes a release version into the Tauri config, `Cargo.toml` and `package.json` in one step |
| `tests/test_smoke.py` | Real synthesis assertions (stdlib `unittest`) |

---

## How a Request Flows

**1 · Validation.** `POST /api/tts/stream` receives `{text, voice, speed}`. Pydantic bounds the speed to the configured range; the handler rejects empty text, text over 50,000 characters (413), and unknown voice ids (400). Text is normalized and split into chunks.

**2 · First chunk, eagerly.** The opening chunk is synthesized *before* the response begins. A failure here surfaces as a real HTTP error rather than an empty stream, and the client was not waiting on anything playable yet anyway.

**3 · Streaming body.** The response is a chunked `application/octet-stream` where each part is a **complete, self-contained WAV file**. Between chunks the server checks `request.is_disconnected()` and abandons synthesis at the next boundary if the user pressed Stop. Response headers carry `X-Chunk-Count`, `X-Sample-Rate`, `X-First-Chunk-Seconds` and `X-First-Chunk-Latency`, which the UI uses to report real numbers instead of a fake spinner.

**4 · Client reassembly.** `app.js` accumulates bytes and walks the buffer looking for `RIFF` magic, reading each header's declared length to slice out one WAV at a time, keeping any partial tail for the next read. This means the transport never needs a delimiter — the file format *is* the framing.

**5 · Playback scheduling.** Each slice goes through `decodeAudioData` and onto a single `AudioContext` clock: `src.start(nextTime)`, then `nextTime += buffer.duration`. The first ~3.2 s (or two chunks) is buffered before playback begins. If a chunk arrives late, the clock is pushed forward rather than starting a source in the past — a stall becomes an audible gap (and a counted, reported `Buffering…` state) instead of garbled or overlapping audio.

**6 · Completion.** A single `maybeFinish()` function — called from both `onended` and stream completion, plus a 400 ms watchdog — decides playback is over, so no ordering of those events can leave the UI stuck in "Reading…".

---

## Key Engineering Decisions

### Short first chunk, large later chunks

Synthesis runs at roughly 1.2–1.7× realtime on this CPU class, so latency is decided by chunk size. The first chunk is capped at **140 characters**; later chunks run up to **320**. That drops time-to-first-audio from about 11 s to about 2 s while keeping per-call overhead low for the rest of the document. Chunk boundaries always fall on sentence ends, and the splitter knows that `Dr.`, `Jan.`, `3.14` and single-letter initials are not sentence terminators.

### 320 characters is not an arbitrary number

One Kokoro batch is reliable up to roughly 510 phonemes. 320 characters stays comfortably under that, so a chunk never silently degrades into truncated or near-empty audio. Over-long sentences break at commas first, then at spaces, with a hard-cut path for unbroken tokens like URLs.

### Four ONNX threads, not eight

Measured with `scripts/bench.py --sweep`: 2 threads → 0.96× realtime, **4 → 1.22×**, 6 → 1.27×, 8 → 1.14×. Hyperthreads slow this graph down, and leaving cores free for the browser and audio pipeline matters more than the last few percent of synthesis speed.

### One synthesis at a time

An `asyncio.Semaphore(1)` in the app and a `threading.Lock` in the engine serialize synthesis. Two concurrent batches on four cores just contend for the same physical cores and make both requests slower. The lock is also what makes the LRU cache safe without coarse locking elsewhere.

### Warm-up on a background thread

Frozen ORT kernel initialisation costs about a second. The lifespan handler starts a daemon warm-up thread immediately, and requests wait on `warm_event` (up to 90 s) instead of racing a cold session. `/api/health` reports `warming` vs `ready`, which is what the status dot in the header reads.

### WAV per chunk instead of a custom protocol

Each chunk is a valid file with its own 44-byte header. The browser decodes it with the standard `decodeAudioData` and needs to know nothing about the producer. Debugging is trivial — concatenate the parts and you have a working WAV — and there is no bespoke container format to maintain on two sides of the wire.

### Server-side abort, not client-side mute

Stop calls `AbortController.abort()`, the connection closes, and the server notices at the next chunk boundary and stops synthesizing. On a CPU-bound model, muting without aborting would leave the machine hot for the full duration of a document nobody is listening to.

### One page, no build step

The frontend is ~500 lines of vanilla JS against the Web Audio API. No framework, no bundler, no `node_modules`, no transpiler — the folder that ships is the folder that runs. For an app with one screen, a build step would have been pure overhead.

### Honest instrumentation

The footer shows the measured first-chunk audio length and latency for the last run, and reports buffering gaps if any occurred. `/api/health` exposes load time, warm-up time and cache hit/miss counts. Numbers in the README come from `scripts/bench.py` on the baseline machine, not from optimistic guesses.

---

## Measured Performance

Baseline: **Intel i5-8265U — 4 cores / 8 threads, 16 GB RAM, no GPU, `TTS_THREADS=4`**.

| Metric | Measured |
|---|---|
| Model load (cold / warm OS cache) | 5.4–7.6 s / ~2 s |
| Engine warm-up | ~1 s |
| Sustained throughput | **1.2–1.7× realtime** (real-time factor 0.60–0.84) |
| Time to first audio | 1.5–4.8 s |
| Model on disk | 310 MB weights + 27 MB voice bank |
| Memory | roughly 1 GB RSS |
| Repeat playback of cached text | instant (256-entry LRU) |

**What that means in practice:** a 10-minute article takes about 6–8 minutes of computation, but playback starts within seconds and the queue stays ahead of the voice throughout. On a slower or thermally-throttled machine the queue can fall behind, and the UI says `Buffering…` rather than dropping audio.

---

## Desktop Packaging

The desktop build is a three-stage pipeline in `package.bat`:

1. **Version** — prompts for `X.X.X`, suggesting the next patch, then writes it into `src-tauri/tauri.conf.json`, `Cargo.toml` and `package.json`.
2. **Freeze** — PyInstaller builds a **one-folder** engine (`--onedir`, deliberately: a one-file build would unpack ~90 MB of DLLs to a temp folder on every launch). The script collects `espeakng_loader` data explicitly — PyInstaller misses it on its own, and the failure mode is near-silent audio, not an error.
3. **Bundle** — Tauri builds an NSIS installer with the engine and model weights declared as resources, into `releases/Local Voice_<version>_x64-setup.exe` (~350 MB, everything included).

At runtime the Rust shell binds an ephemeral loopback port, spawns the sidecar with `--no-browser` and `TTS_MODELS_DIR` / `TTS_EXPORTS_DIR` pointed at the install and app-data directories, redirects the engine's output to `%LOCALAPPDATA%\com.localvoice.desktop\logs\engine.log`, shows a splash page, polls the port every 150 ms for up to 120 s, and navigates the window once it answers. Every failure path writes a specific, human-readable message into the splash page instead of failing silently. On `RunEvent::Exit` the child is killed and reaped, so closing the window never leaves an orphaned engine.

`CARGO_TARGET_DIR` is redirected to `.cargo-target` inside the project so a build never fills the system drive.

---

## Testing & Tooling

**Smoke tests (`tests/test_smoke.py`).** Stdlib `unittest`, no extra dependencies. They load the real 326 MB model on purpose, because the failure mode worth guarding against is a phonemizer problem that returns audio of near-zero length instead of raising — something a mocked test would never catch. Assertions cover: exactly 28 English voices, audio longer than one second, peak amplitude above 0.05 and within [-1, 1], and that the framed WAV re-parses with the expected rate, channel count and sample width. Chunking tests verify limits, abbreviation handling, and that concatenating chunks reproduces the input text exactly. Cache tests verify LRU eviction and hit counting.

**Model verification (`scripts/fetch_model.py`).** Downloads are resumable, partial files are deleted on failure, and every file is checked against a SHA-256 recorded in `models/checksums.json` — the first run records what it saw, later runs refuse a mismatch.

**Benchmarking (`scripts/bench.py`).** Reports real-time factor, first-chunk latency, and a per-thread-count sweep, which is where the "4 threads" decision came from.

---

## Challenges & What I Learned

**Latency is a chunking problem, not just a model problem.** The model's speed was fixed; what I could change was how much text had to be synthesized before anything was playable. A 140-character first chunk cut time-to-first-audio by roughly 5× with no change to the model at all.

**Audio scheduling needs one clock and an explicit policy for lateness.** My first attempt started each buffer at "now", which produced clicks and overlaps whenever a chunk arrived late. Scheduling everything against one `AudioContext` clock, plus the rule that a late chunk shifts the timeline forward instead of starting in the past, turned a timing bug into a visible, countable gap.

**Streaming means the failure surface moves.** With chunked responses, an exception raised halfway through the body can no longer become an HTTP status — the client already has a 200. The fix was to synthesize the first chunk *before* responding, so the common failure is still a real error code, and to make the client's completion logic converge from any event ordering.

**Frozen Python apps break in ways source-tree apps never do.** PyInstaller's `_MEIPASS` changes where data lives, uvicorn cannot import an app by string path in a frozen build, and missing `espeakng_loader` data yields silent audio rather than an exception. Each of these is now handled explicitly — the config module resolves its root differently when frozen, `run.py` hands uvicorn the app object, and `build_sidecar.py` collects the data folder by name.

**Packaging is a feature.** The gap between "works on my machine in a venv" and "someone double-clicks an installer" is a sidecar build, a process supervisor, a splash screen, port negotiation, a log file and a clean shutdown path. That work is what turns a script into something you can hand to another person.

---

## Limitations

Stated plainly, because a portfolio piece is more credible with edges drawn:

- **English is the tested path.** The other 26 voices are listed and phonemize, but long non-English text is not chunked according to that language's rules.
- **No voice cloning.** That requires a different, much slower CPU model.
- **WAV only.** MP3 or Opus would need ffmpeg on `PATH`.
- **Loopback only, no authentication.** It is a personal local tool, not a server intended for exposure.
- **Windows-focused.** The engine and its dependencies are cross-platform, but the launcher, packaging pipeline and installer are Windows-specific as built.
- **CPU-bound.** Throughput scales with cores; a 10-minute article is minutes of computation, not seconds. The design keeps playback ahead of the voice, but it cannot make synthesis faster than the hardware.

---

## Links

- **Installer (v1.0.1):** [Local Voice for Windows](https://drive.google.com/file/d/1b4Uwy6vDQoPRZF2O1SDxTMYYz3HFlWqB/view?usp=sharing)

### Licences

| Component | Licence |
|---|---|
| Kokoro-82M model weights | Apache-2.0 (commercial use of generated audio permitted) |
| `kokoro-onnx` | MIT |
| ONNX Runtime | MIT |
| espeak-ng | GPL-3.0 — invoked as a separate component, not linked into this application's code |
| This application | Use it however you like |
