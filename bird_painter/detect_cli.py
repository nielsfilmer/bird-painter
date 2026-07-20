"""Demo the ears on an audio file:

    python -m bird_painter.detect_cli path/to/clip.wav [confidence_floor]

Prints one line per detection: confidence, common name, scientific name,
and the time window it was heard in.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

from .config import load_config_or_exit
from .ears import Ears


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    path = sys.argv[1]
    if not Path(path).is_file():
        print(f"No such audio file: {path}", file=sys.stderr)
        raise SystemExit(2)
    if len(sys.argv) > 2:
        try:
            floor = float(sys.argv[2])
        except ValueError:
            print(
                f"Confidence floor must be a number, got: {sys.argv[2]!r}",
                file=sys.stderr,
            )
            raise SystemExit(2) from None
    else:
        floor = load_config_or_exit().confidence_floor

    print(f"Loading BirdNET (floor {floor})…", file=sys.stderr)
    # birdnetlib prints progress to stdout; redirect it to stderr so stdout
    # carries only the detection lines (clean for piping).
    try:
        with contextlib.redirect_stdout(sys.stderr):
            ears = Ears(confidence_floor=floor)
            detections = ears.detect_file(path)
    except Exception as exc:  # noqa: BLE001 — demo CLI: a bad clip shouldn't traceback
        print(f"Could not analyse {path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

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
