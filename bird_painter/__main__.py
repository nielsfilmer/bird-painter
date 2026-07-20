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

from .config import ConfigError, load_config


def _parse_port_arg(positional: list[str]) -> int | None:
    if not positional:
        return None
    try:
        return int(positional[0])
    except ValueError:
        raise ConfigError(f"port must be a number, got: {positional[0]!r}") from None


def main() -> None:
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]

    if "--list-devices" in flags:
        from .capture import list_input_devices

        list_input_devices()
        return

    # Surface bird_painter's own INFO logs (startup, listener heartbeat) — they
    # otherwise stay hidden under uvicorn's logging config.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    # Validate the port arg AND all env knobs up front — so a bad BP_* fails
    # with a friendly message here, not as a traceback inside uvicorn's factory
    # import when a positional port skipped the config load.
    try:
        port_override = _parse_port_arg(positional)
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
    port = port_override if port_override is not None else config.port
    uvicorn.run(
        "bird_painter.web:create_app", factory=True, host="127.0.0.1", port=port
    )


if __name__ == "__main__":
    main()
