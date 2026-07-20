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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# BirdNET's label set (BirdNET_GLOBAL_6K_V2.4) isn't birds-only: alongside
# ~6400 birds it carries machine/human noise pseudo-classes and ~86 non-bird
# animals — frogs, toads, crickets, katydids, cicadas, mammals — so the model
# can report "that wasn't a bird". We paint birds, so drop all of these.
#
# Matched on the SCIENTIFIC name (a stable identifier) rather than the common
# name: common names are a trap — "Squirrel Cuckoo", "Cricket Longtail", the
# frogmouths, "Cicadabird" are all real birds. The list below is the exact set
# of non-bird labels in this model version; tests/test_ears.py re-derives it
# from the shipped label file and fails if a birdnetlib bump changes it.
NON_BIRD_SCIENTIFIC = frozenset({
    # machine / human / environment pseudo-classes (scientific == common)
    "Dog", "Engine", "Environmental", "Fireworks", "Gun",
    "Human non-vocal", "Human vocal", "Human whistle",
    "Noise", "Power tools", "Siren",
    # mammals
    "Canis latrans", "Canis lupus", "Odocoileus virginianus",
    "Sciurus carolinensis", "Tamias striatus", "Tamiasciurus hudsonicus",
    # frogs & toads
    "Acris crepitans", "Acris gryllus", "Anaxyrus americanus",
    "Anaxyrus canorus", "Anaxyrus cognatus", "Anaxyrus fowleri",
    "Anaxyrus houstonensis", "Anaxyrus microscaphus", "Anaxyrus quercicus",
    "Anaxyrus speciosus", "Anaxyrus terrestris", "Anaxyrus woodhousii",
    "Dryophytes andersonii", "Dryophytes arenicolor", "Dryophytes avivoca",
    "Dryophytes chrysoscelis", "Dryophytes cinereus", "Dryophytes femoralis",
    "Dryophytes gratiosus", "Dryophytes squirellus", "Dryophytes versicolor",
    "Eleutherodactylus planirostris", "Gastrophryne carolinensis",
    "Gastrophryne olivacea", "Hyliola regilla", "Incilius valliceps",
    "Lithobates catesbeianus", "Lithobates clamitans", "Lithobates palustris",
    "Lithobates sylvaticus", "Pseudacris brimleyi", "Pseudacris clarkii",
    "Pseudacris crucifer", "Pseudacris feriarum", "Pseudacris nigrita",
    "Pseudacris ocularis", "Pseudacris ornata", "Pseudacris streckeri",
    "Pseudacris triseriata", "Scaphiopus couchii", "Spea bombifrons",
    # crickets, katydids, coneheads
    "Allonemobius allardi", "Allonemobius tinnulus", "Allonemobius walkeri",
    "Amblycorypha alexanderi", "Amblycorypha longinicta",
    "Amblycorypha oblongifolia", "Amblycorypha rotundifolia", "Anaxipha exigua",
    "Atlanticus testaceus", "Conocephalus brevipennis",
    "Conocephalus fasciatus", "Cyrtoxipha columbiana", "Eunemobius carolinus",
    "Eunemobius confusus", "Gryllus assimilis", "Gryllus fultoni",
    "Gryllus pennsylvanicus", "Gryllus rubens", "Microcentrum rhombifolium",
    "Miogryllus saussurei", "Neoconocephalus bivocatus",
    "Neoconocephalus ensiger", "Neoconocephalus retusus",
    "Neoconocephalus robustus", "Neonemobius cubensis", "Oecanthus celerinictus",
    "Oecanthus exclamationis", "Oecanthus fultoni", "Oecanthus nigricornis",
    "Oecanthus niveus", "Oecanthus pini", "Oecanthus quadripunctatus",
    "Orchelimum agile", "Orchelimum concinnum", "Orchelimum pulchellum",
    "Orocharis saltator", "Phyllopalpus pulchellus", "Pterophylla camellifolia",
    "Scudderia curvicauda", "Scudderia furcata", "Scudderia texensis",
})


def is_bird(scientific_name: str) -> bool:
    return scientific_name.strip() not in NON_BIRD_SCIENTIFIC


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

    def detect_samples(self, samples: np.ndarray, rate: int) -> list[Detection]:
        """Analyse one in-memory PCM window.

        `samples` must already be what BirdNET expects: a mono float array at
        48 kHz. Unlike `detect_file`, `RecordingBuffer` does NOT resample — the
        live-mic capture (slice 4) is responsible for delivering 48 kHz mono.
        """
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
                start_seconds=float(d["start_time"]),
                end_seconds=float(d["end_time"]),
            )
            for d in raw
            # Drop BirdNET's non-bird noise/human/machine/animal classes.
            if is_bird(d["scientific_name"])
        ]
        # BirdNET already filters by min_conf (strict >, and it clamps the
        # floor to [0.01, 0.99]); sort strongest-first for callers.
        return sorted(detections, key=lambda d: d.confidence, reverse=True)
