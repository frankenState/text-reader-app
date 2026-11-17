"use strict";

const el = {
  text: document.getElementById("text"),
  voice: document.getElementById("voice"),
  speed: document.getElementById("speed"),
  speedValue: document.getElementById("speedValue"),
  play: document.getElementById("play"),
  playLabel: document.getElementById("playLabel"),
  stop: document.getElementById("stop"),
  download: document.getElementById("download"),
  downloadLabel: document.getElementById("downloadLabel"),
  rail: document.getElementById("rail"),
  railNote: document.getElementById("railNote"),
  charCount: document.getElementById("charCount"),
  statusDot: document.getElementById("statusDot"),
  engineLine: document.getElementById("engineLine"),
  progressWrap: document.getElementById("progressWrap"),
  progressBar: document.getElementById("progressBar"),
  progressText: document.getElementById("progressText"),
  error: document.getElementById("error"),
  footInfo: document.getElementById("footInfo"),
  footRate: document.getElementById("footRate"),
};

const state = {
  ready: false,
  playing: false,
  ctx: null,
  nextTime: 0,
  sources: new Set(),
  controller: null,
  pending: [], // decoded buffers waiting for playback to begin
  pendingSeconds: 0,
  started: false,
  totalChunks: 0,
  receivedChunks: 0,
  streamDone: false,
  startedAt: 0,
  gaps: 0,
  watchdog: null,
  runId: 0,
};

// Synthesis runs at roughly 1.5-1.7x realtime on this class of CPU. Starting
// with ~3s of audio keeps the queue ahead even when the next chunk is slow.
const PREBUFFER_SECONDS = 3.2;
const PREBUFFER_CHUNKS = 2;

// Debug handle: lets an automated check inspect playback state from the console.
window.__voiceApp = { state, el };

/* ---------- ui helpers ---------- */

function showError(message) {
  el.error.textContent = message;
  el.error.hidden = false;
}

function clearError() {
  el.error.hidden = true;
  el.error.textContent = "";
}

function setBusy(busy) {
  el.play.disabled = busy || !state.ready || !el.text.value.trim();
  el.download.disabled = busy || !state.ready || !el.text.value.trim();
  el.stop.disabled = !busy;
  el.voice.disabled = busy;
  el.speed.disabled = busy;
  el.playLabel.textContent = busy ? "Reading…" : "Play";
  el.rail.classList.toggle("is-playing", busy);
  updateProgress();
}

function updateProgress() {
  if (!state.playing && !state.streamDone) {
    el.progressWrap.hidden = true;
    return;
  }
  el.progressWrap.hidden = false;
  const pct = state.totalChunks
    ? Math.min(100, (state.receivedChunks / state.totalChunks) * 100)
    : 0;
  el.progressBar.style.width = `${pct}%`;
  const done = state.streamDone && state.sources.size === 0;
  const dry = state.sources.size === 0 && state.pending.length === 0;
  if (done) {
    el.progressText.textContent = "Finished";
  } else if (state.started && dry && !state.streamDone) {
    el.progressText.textContent = "Buffering…";
  } else {
    el.progressText.textContent = `Generating ${state.receivedChunks}/${state.totalChunks || "?"}`;
  }
}

/* ---------- audio ---------- */

function audioContext() {
  if (!state.ctx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    state.ctx = new Ctx();
  }
  if (state.ctx.state === "suspended") state.ctx.resume();
  return state.ctx;
}

function stopAllSources() {
  for (const src of state.sources) {
    try {
      src.stop();
    } catch {
      /* already stopped */
    }
  }
  state.sources.clear();
}

function scheduleBuffer(buffer) {
  const ctx = audioContext();
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  const now = ctx.currentTime;
  // A late chunk must not be started in the past; accept a small gap instead.
  if (state.nextTime < now + 0.02) {
    if (state.started && state.nextTime < now - 0.05) state.gaps += 1;
    state.nextTime = now + 0.02;
  }
  src.start(state.nextTime);
  state.nextTime += buffer.duration;
  state.sources.add(src);
  src.onended = () => {
    state.sources.delete(src);
    maybeFinish();
  };
}

// Single place that decides playback is over, so no ordering of onended and
// stream completion can leave the UI stuck in the playing state.
function maybeFinish() {
  if (
    state.playing &&
    state.streamDone &&
    state.sources.size === 0 &&
    state.pending.length === 0
  ) {
    finishPlayback();
  }
}

function startWatchdog() {
  if (state.watchdog) return;
  state.watchdog = setInterval(() => {
    if (!state.playing) return;
    maybeFinish();
    updateProgress();
  }, 400);
}

function stopWatchdog() {
  if (state.watchdog) {
    clearInterval(state.watchdog);
    state.watchdog = null;
  }
}

function flushPending() {
  const ctx = audioContext();
  state.started = true;
  state.nextTime = ctx.currentTime + 0.08;
  for (const buffer of state.pending) scheduleBuffer(buffer);
  state.pending = [];
  state.pendingSeconds = 0;
}

function finishPlayback() {
  state.playing = false;
  state.started = false;
  state.pending = [];
  state.pendingSeconds = 0;
  stopWatchdog();
  updateProgress();
  setBusy(false);
  el.stop.disabled = true;
  state.controller = null;
  if (state.gaps) {
    el.footRate.textContent = `played with ${state.gaps} buffering gap${state.gaps === 1 ? "" : "s"}`;
    state.gaps = 0;
  }
}

function resetPlayback() {
  // Invalidates any in-flight play() run so its tail cannot touch state.
  state.runId += 1;
  if (state.controller) {
    state.controller.abort();
    state.controller = null;
  }
  stopAllSources();
  state.playing = false;
  state.started = false;
  state.pending = [];
  state.pendingSeconds = 0;
  state.totalChunks = 0;
  state.receivedChunks = 0;
  state.streamDone = false;
  state.gaps = 0;
  stopWatchdog();
  el.progressWrap.hidden = true;
  el.progressBar.style.width = "0%";
}

/* ---------- wav stream parsing ---------- */

function concatBytes(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

// Each chunk on the wire is a complete RIFF/WAVE file with a 44-byte header.
function takeWavs(buffer) {
  const wavs = [];
  let offset = 0;
  while (buffer.length - offset >= 44) {
    const view = new DataView(buffer.buffer, buffer.byteOffset + offset);
    const riff = String.fromCharCode(...buffer.slice(offset, offset + 4));
    if (riff !== "RIFF") {
      offset = buffer.length; // desynchronised; drop the rest
      break;
    }
    const total = 8 + view.getUint32(4, true);
    if (buffer.length - offset < total) break;
    wavs.push(buffer.slice(offset, offset + total));
    offset += total;
  }
  return { wavs, rest: buffer.slice(offset) };
}

async function queueWav(wavBytes) {
  const ctx = audioContext();
  // decodeAudioData detaches its input, so hand it a copy of the exact slice.
  const copy = new Uint8Array(wavBytes.length);
  copy.set(wavBytes);
  let buffer;
  try {
    buffer = await ctx.decodeAudioData(copy.buffer);
  } catch {
    return; // tolerate a malformed chunk rather than killing the whole run
  }
  // Once playback has begun, new chunks go straight onto the audio clock.
  if (state.started) {
    scheduleBuffer(buffer);
    return;
  }

  state.pending.push(buffer);
  state.pendingSeconds += buffer.duration;
  if (
    state.pending.length >= PREBUFFER_CHUNKS ||
    state.pendingSeconds >= PREBUFFER_SECONDS
  ) {
    flushPending();
  }
}

/* ---------- actions ---------- */

async function play() {
  const text = el.text.value.trim();
  if (!text || !state.ready) return;

  clearError();
  resetPlayback();
  const run = state.runId;
  audioContext();
  state.playing = true;
  state.startedAt = performance.now();
  startWatchdog();
  setBusy(true);
  state.controller = new AbortController();

  const payload = {
    text,
    voice: el.voice.value,
    speed: Number(el.speed.value),
  };

  let response;
  try {
    response = await fetch("/api/tts/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: state.controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError" || run !== state.runId) return;
    showError(`Could not reach the local engine: ${err.message}`);
    state.playing = false;
    setBusy(false);
    return;
  }

  if (run !== state.runId) return;

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* keep default */
    }
    showError(detail);
    state.playing = false;
    setBusy(false);
    return;
  }

  state.totalChunks = Number(response.headers.get("X-Chunk-Count") || 0);
  const sr = Number(response.headers.get("X-Sample-Rate") || 24000);
  const firstSeconds = response.headers.get("X-First-Chunk-Seconds");
  const firstLatency = response.headers.get("X-First-Chunk-Latency");
  if (firstSeconds && firstLatency) {
    el.footRate.textContent = `first ${firstSeconds}s of audio in ${firstLatency}s`;
  }
  el.footInfo.textContent = `Kokoro-82M · ${(sr / 1000).toFixed(0)} kHz · offline`;
  updateProgress();

  const reader = response.body.getReader();
  let buffer = new Uint8Array(0);
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = concatBytes(buffer, value);
      const { wavs, rest } = takeWavs(buffer);
      buffer = rest;
      for (const wav of wavs) {
        if (run !== state.runId) return;
        state.receivedChunks += 1;
        await queueWav(wav);
        updateProgress();
      }
    }
  } catch (err) {
    if (err.name !== "AbortError" && run === state.runId) {
      showError(`Stream interrupted: ${err.message}`);
    }
  } finally {
    reader.cancel().catch(() => {});
  }

  if (run !== state.runId) return;
  state.streamDone = true;
  state.controller = null;
  if (!state.started && state.pending.length > 0) flushPending();
  maybeFinish();
  updateProgress();
}

function stop() {
  resetPlayback();
  setBusy(false);
  el.playLabel.textContent = "Play";
}

async function download() {
  const text = el.text.value.trim();
  if (!text || !state.ready) return;
  clearError();
  el.download.disabled = true;
  el.downloadLabel.textContent = "Rendering…";
  try {
    const response = await fetch("/api/tts/file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: el.voice.value, speed: Number(el.speed.value) }),
    });
    if (!response.ok) {
      let detail = `Export failed (${response.status})`;
      try {
        const body = await response.json();
        if (body.detail) detail = body.detail;
      } catch {
        /* keep default */
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = /filename="([^"]+)"/.exec(disposition);
    link.href = url;
    link.download = match ? match[1] : "speech.wav";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    const seconds = response.headers.get("X-Audio-Seconds");
    if (seconds) el.footRate.textContent = `saved ${Number(seconds).toFixed(1)}s of audio`;
  } catch (err) {
    showError(err.message);
  } finally {
    el.downloadLabel.textContent = "Save WAV";
    el.download.disabled = !state.ready || !el.text.value.trim();
  }
}

/* ---------- boot ---------- */

async function loadVoices() {
  const response = await fetch("/api/voices");
  if (!response.ok) throw new Error("Could not load voices");
  const data = await response.json();
  const groups = new Map();
  for (const voice of data.voices) {
    if (!groups.has(voice.language_name)) groups.set(voice.language_name, []);
    groups.get(voice.language_name).push(voice);
  }
  el.voice.innerHTML = "";
  for (const [language, list] of groups) {
    const group = document.createElement("optgroup");
    group.label = language;
    for (const voice of list) {
      const option = document.createElement("option");
      option.value = voice.id;
      option.textContent = `${voice.name} · ${voice.gender}`;
      if (voice.id === data.default) option.selected = true;
      group.appendChild(option);
    }
    el.voice.appendChild(group);
  }
}

async function waitUntilReady() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch("/api/health");
      const health = await response.json();
      if (health.ready) {
        state.ready = true;
        el.statusDot.classList.add("ready");
        el.statusDot.classList.remove("error");
        el.engineLine.textContent = `${health.engine} · ${health.voices} voices · runs on this machine`;
        el.railNote.textContent = `${health.voices} voices`;
        el.footInfo.textContent = `${health.engine} · ${(health.sample_rate / 1000).toFixed(0)} kHz · offline`;
        setBusy(false);
        return;
      }
      el.engineLine.textContent = "Warming up the engine… (first run takes a few seconds)";
    } catch {
      el.engineLine.textContent = "Waiting for the local engine…";
    }
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  el.statusDot.classList.add("error");
  el.engineLine.textContent = "Engine did not start. Check the server console.";
}

function syncInputs() {
  const text = el.text.value;
  el.charCount.textContent = `${text.length.toLocaleString()} character${text.length === 1 ? "" : "s"}`;
  if (!state.playing) {
    el.play.disabled = !state.ready || !text.trim();
    el.download.disabled = !state.ready || !text.trim();
  }
}

el.text.addEventListener("input", syncInputs);

el.speed.addEventListener("input", () => {
  el.speedValue.textContent = `${Number(el.speed.value).toFixed(2)}×`;
});

el.play.addEventListener("click", () => {
  if (state.playing) stop();
  play();
});

el.stop.addEventListener("click", stop);
el.download.addEventListener("click", download);

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    if (state.playing) stop();
    else play();
  }
  if (event.key === "Escape" && state.playing) stop();
});

(async function boot() {
  try {
    await loadVoices();
  } catch (err) {
    showError(err.message);
  }
  await waitUntilReady();
  syncInputs();
})();
