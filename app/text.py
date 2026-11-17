"""Text normalization and sentence-aware chunking.

The engine synthesizes one batch at a time and does not stream within a call,
so latency is decided here: short first chunk for fast start, later chunks large
enough to keep throughput up, boundaries always at sentence ends.
"""

from __future__ import annotations

import re
import unicodedata

from .config import MAX_CHUNK_CHARS, MAX_FIRST_CHUNK_CHARS

# Sentence-final punctuation followed by whitespace (or end of paragraph).
_BOUNDARY = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+")

# Tokens that end with a period but do not end a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "fig", "figs", "no", "nos", "inc", "ltd", "co", "corp", "dept", "est", "min",
    "max", "approx", "cf", "al", "ed", "eds", "vol", "pp", "p", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}

# Characters that are safe places to break an over-long sentence.
_SOFT_BREAK = re.compile(r"(?<=[;:,])\s+")
_WORD = re.compile(r"\S+")


def normalize(text: str) -> str:
    """Normalize unicode, quotes and whitespace without changing wording."""
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", " - ")
        .replace("\u00a0", " ")
        .replace("\ufeff", "")
    )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Kill control characters except newlines.
    text = "".join(ch for ch in text if ch == "\n" or ch >= " " or ch == "\t")
    # Collapse runs of spaces/tabs, and 3+ blank lines to a paragraph break.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ends_with_abbreviation(fragment: str) -> bool:
    """True when a fragment ends in something like 'Dr.' or an initial."""
    tail = fragment.rstrip("\"'”’)]").rsplit(" ", 1)[-1].rstrip(".").lower()
    if not tail:
        return False
    if tail in _ABBREVIATIONS:
        return True
    # Single letter initial, e.g. "J." or "U.S" style tokens.
    return len(tail) == 1 and tail.isalpha()


def _split_long(sentence: str, limit: int) -> list[str]:
    """Break a single over-long sentence at commas, then at spaces."""
    if len(sentence) <= limit:
        return [sentence]

    pieces = _SOFT_BREAK.split(sentence)
    if len(pieces) == 1:
        pieces = sentence.split(" ")

    out: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = f"{buf} {piece}".strip() if buf else piece
        if len(candidate) <= limit:
            buf = candidate
            continue
        if buf:
            out.append(buf)
            buf = ""
        if len(piece) <= limit:
            buf = piece
        else:
            # Hopelessly long token (URL, base64 blob): hard-cut on spaces.
            words = _WORD.findall(piece)
            chunk = ""
            for word in words:
                if len(chunk) + len(word) + 1 > limit and chunk:
                    out.append(chunk)
                    chunk = word
                else:
                    chunk = f"{chunk} {word}".strip()
            buf = chunk
    if buf:
        out.append(buf)
    return out


def split_sentences(text: str) -> list[str]:
    """Split normalized text into sentences, keeping paragraph breaks."""
    sentences: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        parts = _BOUNDARY.split(paragraph)
        merged: list[str] = []
        for part in parts:
            if not part:
                continue
            if merged and _ends_with_abbreviation(merged[-1]):
                merged[-1] = f"{merged[-1]} {part}"
            else:
                merged.append(part)
        sentences.extend(s.strip() for s in merged if s.strip())
    return sentences


def split_chunks(
    text: str,
    max_chars: int = MAX_CHUNK_CHARS,
    first_chars: int = MAX_FIRST_CHUNK_CHARS,
) -> list[str]:
    """Group sentences into synthesis chunks.

    The first chunk is deliberately small: synthesis runs at roughly 1.2x
    realtime on this class of CPU, so a short opening sentence is what makes
    audio start quickly. Later chunks are large for efficiency.
    """
    sentences: list[str] = []
    for sentence in split_sentences(normalize(text)):
        sentences.extend(_split_long(sentence, max_chars))

    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        target = first_chars if not chunks else max_chars
        if buf and len(buf) + 1 + len(sentence) > target:
            chunks.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}" if buf else sentence
        if buf and len(buf) >= target - 40:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks
