"""Run the live mic and print detections as they come:

    python -m bird_painter.listen_cli

Uses the configured confidence floor and analysis window. Ctrl-C to stop.
This is the ears + mic end-to-end; the trigger gate that paints them is
slice 5.
"""

from __future__ import annotations

import logging

from .capture import MicListener
from .config import load_config
from .ears import Detection, Ears


def _print_detections(detections: list[Detection]) -> None:
    for d in detections:
        print(
            f"{d.confidence:.2f}  {d.species_common} ({d.species_scientific})",
            flush=True,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config()
    print(f"Loading BirdNET (floor {config.confidence_floor})…", flush=True)
    ears = Ears(confidence_floor=config.confidence_floor)
    listener = MicListener(
        ears,
        window_seconds=config.analysis_window_seconds,
        device=config.input_device,
    )
    listener.listen(_print_detections)


if __name__ == "__main__":
    main()
