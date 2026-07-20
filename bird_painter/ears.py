"""The ears: audio → bird-species detections, via BirdNET (birdnetlib).

Wraps Cornell Lab's BirdNET model. `detect_file` analyses a wav/flac path
(demoable on its own); `detect_samples` analyses an in-memory PCM window and
is what the live-mic capture (slice 4) feeds. Both apply the confidence floor
and return the same `Detection` shape the rest of the pipeline consumes.

Loading the Analyzer pulls the BirdNET TFLite model into memory (a few
seconds) — construct one `Ears` and reuse it, never per-window.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# Quiet TensorFlow's C++ INFO/WARNING logs (the "Created XNNPACK delegate" line
# etc.). Must be set before TF is imported — it is, since birdnetlib (and thus
# TF) is imported lazily inside Ears, after this module loads. setdefault so a
# caller can still override.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")


@contextlib.contextmanager
def _silence_load():
    """Silence the model-load noise. TF Lite writes some lines (the 'Created
    XNNPACK delegate' INFO) straight to the stdout/stderr file descriptors from
    C++, bypassing Python — the fd redirect below is the ONLY thing that
    catches those; it also swallows birdnetlib's print()s and warning output
    (TF_CPP_MIN_LOG_LEVEL handles TF's own logger belt-and-suspenders). On any
    error the fds are restored in `finally` and exceptions still propagate
    (a load failure's traceback prints after the block, unswallowed).

    CAVEAT: os.dup2 on fds 1/2 is PROCESS-GLOBAL and cross-thread. In the app,
    Analyzer() loads in the listener daemon thread, so for the ~seconds of the
    load ANY thread's stdout/stderr (uvicorn's included) goes to /dev/null.
    Startup-only, and in practice uvicorn's logs land outside the window — but
    it's a real race, accepted for a single-process personal installation.
    See the follow-up issue about loading Ears before the listener thread."""
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_out = saved_err = None
    try:
        saved_out, saved_err = os.dup(1), os.dup(2)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        if saved_out is not None:
            os.dup2(saved_out, 1)
            os.close(saved_out)
        if saved_err is not None:
            os.dup2(saved_err, 2)
            os.close(saved_err)
        os.close(devnull)

# BirdNET's label set (BirdNET_GLOBAL_6K_V2.4) isn't birds-only: alongside
# ~6400 birds it carries machine/human noise pseudo-classes and ~86 non-bird
# animals — frogs, toads, crickets, katydids, coneheads, mammals — so the model
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
    def __init__(
        self,
        confidence_floor: float,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
    ):
        self.confidence_floor = confidence_floor
        # Optional location filter: when a lat/lon is set, BirdNET restricts
        # predictions to species plausible at that place + time of year (its
        # meta model), cutting implausible detections. Both must be set to
        # enable it; None = no filter (global model).
        self.latitude = latitude
        self.longitude = longitude
        # Loading BirdNET is noisy: pydub warns about missing ffmpeg (we never
        # decode compressed audio — we feed raw 48 kHz samples), TF Lite warns
        # about deprecation + prints an XNNPACK line, and birdnetlib print()s
        # its load progress ("Labels loaded." …). None of it is actionable.
        # (birdnetlib is imported lazily here — it drags in tensorflow, too
        # heavy to pay just to load config or the wall.)
        with _silence_load():
            from birdnetlib.analyzer import Analyzer

            self._analyzer = Analyzer()

    def _location_kwargs(self) -> dict:
        """lat/lon/date to pass to birdnetlib when the location filter is on.
        The date is 'now' so the species list tracks the season; empty when no
        location is configured (global model)."""
        if self.latitude is None or self.longitude is None:
            return {}
        return {
            "lat": self.latitude,
            "lon": self.longitude,
            "date": datetime.date.today(),
        }

    def detect_file(self, path: str | Path) -> list[Detection]:
        from birdnetlib import Recording

        recording = Recording(
            self._analyzer,
            str(path),
            min_conf=self.confidence_floor,
            **self._location_kwargs(),
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
            self._analyzer,
            samples,
            rate,
            min_conf=self.confidence_floor,
            **self._location_kwargs(),
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
