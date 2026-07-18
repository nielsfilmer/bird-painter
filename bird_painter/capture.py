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
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from .ears import Detection, Ears

logger = logging.getLogger(__name__)

# BirdNET's fixed input rate; do not change without adding resampling.
BIRDNET_SAMPLERATE = 48000


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
                except Exception as exc:  # noqa: BLE001 — one bad window never
                    logger.error("capture: window failed: %s", exc)  # kills the loop
                    continue
                if detections:
                    on_detections(detections)
        except KeyboardInterrupt:
            logger.info("listening stopped")
