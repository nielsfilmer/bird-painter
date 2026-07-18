"""Live mic capture: record rolling windows and feed them to the ears.

BirdNET wants 48 kHz mono float — we record at exactly that, so no resampling
is needed (`detect_samples` does none). Each window is analysed and any
detections handed to a callback; the trigger gate (slice 5) is what will turn
those detections into paintings. A failed window is logged and skipped — the
listen loop must never die on one bad read (mirrors the brush's soft-failure
stance).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from .ears import Detection, Ears

logger = logging.getLogger(__name__)

# BirdNET's fixed input rate; do not change without adding resampling.
BIRDNET_SAMPLERATE = 48000

# Backoff after a failed window, so a persistent fault (mic unplugged, bad
# rate) can't spin the loop at 100% CPU flooding the log.
ERROR_BACKOFF_SECONDS = 1.0


class MicListener:
    def __init__(
        self,
        ears: Ears,
        window_seconds: int,
        *,
        samplerate: int = BIRDNET_SAMPLERATE,
        device: int | str | None = None,
    ):
        self.ears = ears
        self.window_seconds = window_seconds
        self.samplerate = samplerate
        self.device = device

    def _record_window(self) -> np.ndarray:
        frames = int(self.window_seconds * self.samplerate)
        recording = sd.rec(
            frames,
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            device=self.device,
        )
        sd.wait()
        return recording.reshape(-1)

    def listen(self, on_detections: Callable[[list[Detection]], None]) -> None:
        """Record → detect → callback, forever. Ctrl-C stops it cleanly."""
        logger.info(
            "listening: %ds windows at %d Hz", self.window_seconds, self.samplerate
        )
        try:
            while True:
                try:
                    samples = self._record_window()
                    detections = self.ears.detect_samples(samples, self.samplerate)
                except Exception:  # noqa: BLE001 — one bad window never kills
                    logger.exception("capture: window failed")  # the loop
                    time.sleep(ERROR_BACKOFF_SECONDS)  # don't spin on a hard fault
                    continue
                if not detections:
                    continue
                # A raising callback (e.g. slice 5's paint) must not kill
                # listening either — the loop is the durable part.
                try:
                    on_detections(detections)
                except Exception:  # noqa: BLE001
                    logger.exception("capture: on_detections callback failed")
        except KeyboardInterrupt:
            logger.info("listening stopped")
