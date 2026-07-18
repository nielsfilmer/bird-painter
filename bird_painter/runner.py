"""The runner: ties detections → gate → brush → store. This is the callback
the live mic feeds, and the seam where a heard bird becomes a painting on the
wall."""

from __future__ import annotations

import logging

from .brush import paint as paint_species
from .config import Config
from .ears import Detection
from .gate import TriggerGate
from .store import Store

logger = logging.getLogger(__name__)


class PaintRunner:
    def __init__(self, config: Config, store: Store, gate: TriggerGate):
        self.config = config
        self.store = store
        self.gate = gate

    def on_detections(self, detections: list[Detection]) -> None:
        for detection in detections:
            self._maybe_paint(detection)

    def _maybe_paint(self, detection: Detection) -> None:
        species = detection.species_common
        if not self.gate.allows(species):
            return
        result = paint_species(
            species, detection.species_scientific, fal_key=self.config.fal_key
        )
        if result is None:
            # Soft failure (fal outage / no key): nothing marked painted, no
            # cap slot consumed — the species retries on its next detection.
            return
        image_bytes, extension = result
        self.store.add(
            image_bytes=image_bytes,
            extension=extension,
            species_common=species,
            species_scientific=detection.species_scientific,
            confidence=detection.confidence,
            source="detection",
        )
        self.gate.record()
        logger.info("painted %s (%.2f)", species, detection.confidence)
