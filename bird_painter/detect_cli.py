"""Demo the ears on an audio file:

    python -m bird_painter.detect_cli path/to/clip.wav [confidence_floor]

Prints one line per detection: confidence, common name, scientific name,
and the time window it was heard in.
"""

from __future__ import annotations

import sys

from .config import load_config
from .ears import Ears


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    floor = float(sys.argv[2]) if len(sys.argv) > 2 else load_config().confidence_floor

    print(f"Loading BirdNET (floor {floor})…", file=sys.stderr)
    ears = Ears(confidence_floor=floor)
    detections = ears.detect_file(path)

    if not detections:
        print("No birds detected above the confidence floor.")
        return
    for d in detections:
        print(
            f"{d.confidence:.2f}  {d.species_common} ({d.species_scientific})"
            f"  [{d.start_seconds:.0f}-{d.end_seconds:.0f}s]"
        )


if __name__ == "__main__":
    main()
