"""#121's deliverable: measured RSS of the bird-painter Python side on the unit
itself — import, Analyzer load, one detection, one clip cleanup.

ru_maxrss is in KB on Linux and in BYTES on macOS; a dev machine's figure
would otherwise come out 1024x high (review of #145).

Usage, on the unit:  .venv/bin/python scripts/memcheck.py
Prints RSS after each stage; the last line is the figure #121 wants."""
import resource
import sys
import time

T0 = time.time()
_DIV = 1024 * 1024 if sys.platform == "darwin" else 1024


def mark(label):
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        f"{label:34s} peak RSS {raw / _DIV:8.1f} MB   t={time.time() - T0:5.1f}s",
        flush=True,
    )


mark("baseline")
import numpy as np  # noqa: E402

mark("+numpy")
import importlib.util as u  # noqa: E402

backend = (
    "tflite-runtime/shim" if u.find_spec("tflite_runtime")
    else ("tensorflow" if u.find_spec("tensorflow") else "NONE")
)
print(f"   ears backend: {backend}")
from bird_painter.ears import Ears  # noqa: E402

mark("+bird_painter.ears")
ears = Ears(confidence_floor=0.5)
window = np.random.default_rng(0).standard_normal(48000 * 3).astype("float32")
window *= 0.05
t = time.time()
dets = ears.detect_samples(window, 48000)
mark(f"Analyzer + detect ({len(dets)} dets)")
print(f"   first detect_samples: {time.time() - t:.2f}s")
t = time.time()
ears.detect_samples(window, 48000)
print(f"   warm detect_samples:  {time.time() - t:.2f}s")
from bird_painter.clip_clean import enhance  # noqa: E402

t = time.time()
enhance(window, 48000)
print(f"   clip_clean.enhance:   {time.time() - t:.2f}s")
mark("after clip_clean")
