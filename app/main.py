"""FastAPI app: streaming synthesis for the browser UI."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import concat, wav_bytes
from .cache import AudioCache
from .config import (
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    MAX_SPEED,
    MAX_TEXT_CHARS,
    MIN_SPEED,
    SAMPLE_RATE,
    WEB_DIR,
)
from .engines.kokoro import KokoroEngine
from .text import split_chunks

logger = logging.getLogger("tts")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = DEFAULT_VOICE
    speed: float = Field(default=DEFAULT_SPEED, ge=MIN_SPEED, le=MAX_SPEED)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = KokoroEngine()
    app.state.engine = engine
    app.state.cache = AudioCache()
    app.state.synth_lock = asyncio.Semaphore(1)
    warm = threading.Event()
    app.state.warm_event = warm

    def _warm_up() -> None:
        try:
            engine.warm_up()
            logger.info("engine warm-up finished in %.1fs", engine.warm_seconds)
        except Exception:  # noqa: BLE001 - warm-up must never kill the server
            logger.exception("engine warm-up failed; first request will be slow")
        finally:
            warm.set()

    threading.Thread(target=_warm_up, daemon=True, name="kokoro-warmup").start()
    logger.info(
        "Kokoro loaded in %.1fs (%d voices)",
        engine.load_seconds,
        len(engine.info.voices),
    )
    yield
    app.state.cache.clear()


app = FastAPI(title="Local TTS", version="0.1.0", lifespan=lifespan)


def _engine(app_: FastAPI) -> KokoroEngine:
    return app_.state.engine


def _validate(req: SpeakRequest, app_: FastAPI) -> list[str]:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Text is {len(text):,} characters; the limit is {MAX_TEXT_CHARS:,}.",
        )
    valid = {v.id for v in _engine(app_).info.voices}
    if req.voice not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown voice: {req.voice}")
    chunks = split_chunks(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Nothing to synthesize.")
    return chunks


def _synth_chunk(app_: FastAPI, text: str, voice: str, speed: float) -> np.ndarray:
    cache: AudioCache = app_.state.cache
    key = AudioCache.make_key(text, voice, speed)
    cached = cache.get(key)
    if cached is not None:
        return cached
    audio = _engine(app_).synthesize(text, voice, speed)
    cache.put(key, audio)
    return audio


async def _ensure_warm(request: Request, timeout: float = 90.0) -> None:
    warm: threading.Event = request.app.state.warm_event
    if warm.is_set():
        return
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, warm.wait, timeout)
    if not ok:
        raise HTTPException(status_code=503, detail="Engine is still warming up.")


@app.get("/api/health")
async def health(request: Request) -> dict:
    engine: KokoroEngine = request.app.state.engine
    return {
        "status": "ok",
        "ready": engine.warm,
        "warming": not engine.warm,
        "engine": engine.info.name,
        "model": engine.info.model,
        "sample_rate": engine.info.sample_rate,
        "voices": len(engine.info.voices),
        "load_seconds": round(engine.load_seconds, 2),
        "warm_seconds": round(engine.warm_seconds, 2),
        "cache": request.app.state.cache.stats(),
    }


@app.get("/api/voices")
async def voices(request: Request) -> dict:
    engine: KokoroEngine = request.app.state.engine
    return {
        "default": DEFAULT_VOICE,
        "voices": [v.as_dict() for v in engine.info.voices],
    }


@app.post("/api/tts/stream")
async def tts_stream(req: SpeakRequest, request: Request) -> StreamingResponse:
    chunks = _validate(req, request.app)
    await _ensure_warm(request)

    loop = asyncio.get_running_loop()
    lock: asyncio.Semaphore = request.app.state.synth_lock

    # Synthesize the opening chunk before responding: a failure here becomes a
    # real HTTP error instead of a silently empty stream, and the client is not
    # waiting on anything it could have played yet anyway.
    started = time.perf_counter()
    async with lock:
        first = await loop.run_in_executor(
            None, _synth_chunk, request.app, chunks[0], req.voice, req.speed
        )
    first_latency = time.perf_counter() - started
    logger.info(
        "stream: %d chunks, first %.2fs audio in %.2fs",
        len(chunks),
        len(first) / SAMPLE_RATE,
        first_latency,
    )

    async def body():
        yield wav_bytes(first)
        for index, text in enumerate(chunks[1:], start=2):
            if await request.is_disconnected():
                logger.info("stream: client disconnected, stopping synthesis")
                return
            chunk_started = time.perf_counter()
            async with lock:
                audio = await loop.run_in_executor(
                    None, _synth_chunk, request.app, text, req.voice, req.speed
                )
            if await request.is_disconnected():
                logger.info("stream: client disconnected, stopping synthesis")
                return
            audio_s = len(audio) / SAMPLE_RATE
            synth_s = time.perf_counter() - chunk_started
            logger.info(
                "chunk %d/%d: %d chars -> %.2fs audio in %.2fs (%.2fx)",
                index,
                len(chunks),
                len(text),
                audio_s,
                synth_s,
                audio_s / max(synth_s, 1e-6),
            )
            yield wav_bytes(audio)

    headers = {
        "X-Chunk-Count": str(len(chunks)),
        "X-Sample-Rate": str(SAMPLE_RATE),
        "X-First-Chunk-Seconds": f"{len(first) / SAMPLE_RATE:.2f}",
        "X-First-Chunk-Latency": f"{first_latency:.2f}",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(body(), media_type="application/octet-stream", headers=headers)


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", text)[:6]
    slug = "-".join(words).lower()
    return (slug or "speech")[:60]


@app.post("/api/tts/file")
async def tts_file(req: SpeakRequest, request: Request) -> Response:
    chunks = _validate(req, request.app)
    await _ensure_warm(request)

    loop = asyncio.get_running_loop()
    lock: asyncio.Semaphore = request.app.state.synth_lock
    started = time.perf_counter()
    pieces: list[np.ndarray] = []
    for text in chunks:
        async with lock:
            pieces.append(
                await loop.run_in_executor(
                    None, _synth_chunk, request.app, text, req.voice, req.speed
                )
            )
    audio = concat(pieces)
    elapsed = time.perf_counter() - started
    logger.info(
        "file: %.2fs of audio in %.2fs (%.2fx realtime)",
        len(audio) / SAMPLE_RATE,
        elapsed,
        (len(audio) / SAMPLE_RATE) / max(elapsed, 1e-6),
    )

    filename = f"{_slug(req.text)}.wav"
    return Response(
        content=wav_bytes(audio),
        media_type="audio/wav",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audio-Seconds": f"{len(audio) / SAMPLE_RATE:.2f}",
            "Cache-Control": "no-store",
        },
    )


# Serves index.html at "/" and the static assets.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
