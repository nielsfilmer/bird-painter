"""Run the wall: python -m bird_painter [port]

  python -m bird_painter                 # wall + mic listener, default port
  python -m bird_painter 8600            # on port 8600
  python -m bird_painter --list-devices  # list mic input devices + exit

Port resolution (first wins): CLI arg → BP_PORT env → default 8537.
8537 is an uncommon high port chosen to avoid colliding with other local
dev servers; override it if it's taken. Pick a mic with BP_INPUT_DEVICE
(index or name substring; see --list-devices).
"""

import logging
import sys

import uvicorn

from .config import load_config


def _list_devices() -> None:
    import sounddevice as sd

    default_in = sd.default.device[0]
    print("Input devices (use the index or a name substring as BP_INPUT_DEVICE):")
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            marker = "  <- default" if index == default_in else ""
            print(f"  {index}: {dev['name']} ({int(dev['default_samplerate'])} Hz){marker}")


def main() -> None:
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--list-devices" in flags:
        _list_devices()
        return

    # Surface bird_painter's own INFO logs (startup, listener heartbeat) — they
    # otherwise stay hidden under uvicorn's logging config.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    port = int(positional[0]) if positional else load_config().port
    uvicorn.run("bird_painter.web:app", host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
