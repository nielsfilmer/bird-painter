"""Detection audio clips: cut the moment a bird was heard out of the analysis
window and encode it as WAV, so the wall can replay the actual sound behind a
painting. Stdlib `wave` only — no new dependencies.

The pipeline discards audio after analysis by design ("local ears" — nothing
is recorded permanently); the ONE exception is the few seconds around a
detection that actually painted, stored beside its painting so a click on the
wall can replay it.
"""

from __future__ import annotations

import io
import wave

import numpy as np

# Breathing room kept around the detection inside the window, so the clip has
# a lead-in/out instead of starting mid-song.
CLIP_PAD_SECONDS = 1.5


def detection_clip_wav(
    window: np.ndarray,
    samplerate: int,
    start_seconds: float,
    end_seconds: float,
    pad_seconds: float = CLIP_PAD_SECONDS,
) -> bytes:
    """Cut [start-pad, end+pad] (clamped to the window) out of a mono float
    window in [-1, 1] and encode as 16-bit PCM WAV bytes."""
    start = max(0, int((start_seconds - pad_seconds) * samplerate))
    end = min(len(window), int((end_seconds + pad_seconds) * samplerate))
    if end <= start:  # degenerate detection bounds — keep the whole window
        start, end = 0, len(window)
    clip = np.clip(window[start:end], -1.0, 1.0)
    pcm = (clip * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
