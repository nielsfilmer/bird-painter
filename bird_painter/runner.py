"""The runner: ties detections → gate → brush → store. This is the callback
the live mic feeds, and the seam where a heard bird becomes a painting on the
wall."""

from __future__ import annotations

import datetime
import logging
import time

import numpy as np

from .audio import detection_clip_wav
from .brush import Rejected
from .brush import paint as paint_species
from .config import Config
from .ears import Detection
from .events import EventHub, announce_painted, detected_event
from .gate import TriggerGate
from .occasions import hat_for
from .store import Store
from .trim import trim_to_bird

logger = logging.getLogger(__name__)


class PaintRunner:
    def __init__(
        self,
        config: Config,
        store: Store,
        gate: TriggerGate,
        events: EventHub | None = None,
    ):
        self.config = config
        self.store = store
        self.gate = gate
        # Where recognitions are broadcast from (the /ws/detections stream);
        # optional so tests and any headless use can run without one.
        self.events = events

    def on_detections(
        self,
        detections: list[Detection],
        window: np.ndarray | None = None,
        samplerate: int | None = None,
    ) -> None:
        for detection in detections:
            self._maybe_paint(detection, window, samplerate)

    def _maybe_paint(
        self,
        detection: Detection,
        window: np.ndarray | None = None,
        samplerate: int | None = None,
    ) -> None:
        species = detection.species_common
        allowed = self.gate.allows(species)
        # Every recognition is broadcast, gated or not — the stream is about
        # what was HEARD; the wall is about what got painted.
        self._publish(
            detected_event(
                species_common=species,
                species_scientific=detection.species_scientific,
                confidence=detection.confidence,
                at=time.time(),
                will_paint=allowed,
            )
        )
        if not allowed:
            return
        result = paint_species(
            species,
            detection.species_scientific,
            fal_key=self.config.fal_key,
            model=self.config.fal_model,
            hat=hat_for(
                datetime.date.today(), self.config.hat_days, self.config.hat_dates
            ),
        )
        if isinstance(result, Rejected):
            # The model keeps painting this species as something that isn't a
            # bird on white. That's deterministic, not transient — so the
            # species waits out its cooldown rather than buying two more
            # generations on the next detection a few seconds from now. The
            # hourly cap is left alone on purpose: it belongs to the other
            # birds, and one bad species shouldn't spend it.
            self.gate.record_failure(species)
            logger.warning(
                "gave up on %s for now (%s); it waits out the cooldown",
                species,
                result.reason,
            )
            return
        if result is None:
            # Soft failure (fal outage / no key): nothing marked painted, no
            # cap slot consumed — the species retries on its next detection.
            return
        image_bytes, extension = result
        # Crop the flat-white margin so the bird fills its plate on the wall.
        image_bytes = trim_to_bird(image_bytes, extension)
        # Archive the sound behind the painting so the wall can replay it.
        # Best-effort: a clip failure must never cost the painting.
        audio_bytes = None
        if window is not None and samplerate:
            try:
                audio_bytes = detection_clip_wav(
                    window,
                    samplerate,
                    detection.start_seconds,
                    detection.end_seconds,
                    enhance=self.config.enhance_clips,
                )
            except Exception:  # noqa: BLE001
                logger.exception("clip failed for %s; painting without audio", species)
        painting = self.store.add(
            image_bytes=image_bytes,
            extension=extension,
            species_common=species,
            species_scientific=detection.species_scientific,
            confidence=detection.confidence,
            source="detection",
            audio_bytes=audio_bytes,
        )
        self.gate.record()
        announce_painted(self.events, self.store, painting)
        logger.info("painted %s (%.2f)", species, detection.confidence)

    def _publish(self, event: dict) -> None:
        """Broadcasting must never cost a detection — the hub swallows its own
        errors, and a missing hub is simply a wall nobody is watching."""
        if self.events is not None:
            self.events.publish(event)
