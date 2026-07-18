"""The ears: audio → bird-species detections, via BirdNET (birdnetlib).

Wraps Cornell Lab's BirdNET model. `detect_file` analyses a wav/flac path
(demoable on its own); `detect_samples` analyses an in-memory PCM window and
is what the live-mic capture (slice 4) feeds. Both apply the confidence floor
and return the same `Detection` shape the rest of the pipeline consumes.

Loading the Analyzer pulls the BirdNET TFLite model into memory (a few
seconds) — construct one `Ears` and reuse it, never per-window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    species_common: str
    species_scientific: str
    confidence: float
    start_seconds: float
    end_seconds: float


class Ears:
    def __init__(self, confidence_floor: float):
        # Imported lazily: birdnetlib drags in tensorflow, a heavy import we
        # don't want to pay just to load config or the web app.
        from birdnetlib.analyzer import Analyzer

        self.confidence_floor = confidence_floor
        self._analyzer = Analyzer()

    def detect_file(self, path: str | Path) -> list[Detection]:
        from birdnetlib import Recording

        recording = Recording(
            self._analyzer, str(path), min_conf=self.confidence_floor
        )
        recording.analyze()
        return self._to_detections(recording.detections)

    def detect_samples(self, samples, rate: int) -> list[Detection]:
        """Analyse one in-memory PCM window (float array + sample rate)."""
        from birdnetlib import RecordingBuffer

        recording = RecordingBuffer(
            self._analyzer, samples, rate, min_conf=self.confidence_floor
        )
        recording.analyze()
        return self._to_detections(recording.detections)

    def _to_detections(self, raw: list[dict]) -> list[Detection]:
        detections = [
            Detection(
                species_common=d["common_name"],
                species_scientific=d["scientific_name"],
                confidence=float(d["confidence"]),
                start_seconds=float(d.get("start_time", 0.0)),
                end_seconds=float(d.get("end_time", 0.0)),
            )
            for d in raw
        ]
        # BirdNET already filters by min_conf; sort strongest-first for callers.
        return sorted(detections, key=lambda d: d.confidence, reverse=True)
