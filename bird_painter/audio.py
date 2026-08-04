"""Detection audio clips: cut the moment a bird was heard out of the analysis
window, clean it up, and encode it as WAV, so the wall can replay the actual
sound behind a painting — clearly enough to recognise. The cleanup itself
lives in `clip_clean`; encoding is stdlib `wave`.

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
    enhance: bool = True,
) -> bytes:
    """Cut [start-pad, end+pad] (clamped to the window) out of a mono float
    window in [-1, 1], clean it up, and encode as 16-bit PCM WAV bytes.

    `enhance=False` archives exactly what the mic heard — the raw cut, for
    comparing against the cleaned version or for a listener who'd rather judge
    the recording themselves."""
    start = max(0, int((start_seconds - pad_seconds) * samplerate))
    end = min(len(window), int((end_seconds + pad_seconds) * samplerate))
    if end <= start:  # degenerate detection bounds — keep the whole window
        start, end = 0, len(window)
    clip = window[start:end]
    if enhance:
        from .clip_clean import enhance as clean

        # Hand over where the detection sits inside the cut. The padding
        # around it is the same room without the bird, which is a far better
        # noise profile than anything the cleanup could infer from the mixture
        # — and the only one that holds when the bird sings right through its
        # own detection.
        bird_span = (
            max(0, int(start_seconds * samplerate) - start),
            min(end - start, int(end_seconds * samplerate) - start),
        )
        clip = clean(clip, samplerate, bird_span=bird_span)
    clip = np.clip(clip, -1.0, 1.0)
    pcm = (clip * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
