"""Run the live mic and print detections as they come:

    python -m bird_painter.listen_cli
    python -m bird_painter.listen_cli --list-devices   # list mics + exit

Uses the configured confidence floor and analysis window. Pick the mic with
BP_INPUT_DEVICE (an index or a name substring; --list-devices shows them).
Ctrl-C to stop. This is the ears + mic end-to-end; the trigger gate that
paints them is slice 5.
"""

from __future__ import annotations

import logging
import sys

from .capture import MicListener, device_name, list_input_devices
from .config import load_config_or_exit
from .ears import Detection, Ears


def _print_detections(detections: list[Detection]) -> None:
    for d in detections:
        print(
            f"{d.confidence:.2f}  {d.species_common} ({d.species_scientific})",
            flush=True,
        )


def main() -> None:
    if "--list-devices" in sys.argv[1:]:
        list_input_devices()
        return

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config_or_exit()
    print(
        f"Input device: {device_name(config.input_device)}. "
        "Select another with BP_INPUT_DEVICE=<index|name> "
        "(list them: python -m bird_painter.listen_cli --list-devices).",
        flush=True,
    )
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
