"""Live mic capture: a continuous stream feeds rolling windows to the ears.

BirdNET wants 48 kHz mono float — we capture at exactly that, so no resampling
is needed (`detect_samples` does none). A `sounddevice.InputStream` runs in
PortAudio's own thread and never stops, appending every block to a ring buffer;
the listen loop pulls windows off that buffer and analyses them. Because
capture and analysis run on separate threads, audio that arrives *during* an
analysis is buffered rather than lost — no gaps between windows (the old
`sd.rec`-then-analyse was serial and dropped those seconds).

Each window's detections are handed to a callback; the trigger gate turns them
into paintings. A failed window is logged and skipped — the listen loop must
never die on one bad read (mirrors the brush's soft-failure stance).
"""

from __future__ import annotations

import logging
import queue
import time
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from .ears import Detection, Ears

logger = logging.getLogger(__name__)

# BirdNET's fixed input rate; do not change without adding resampling.
BIRDNET_SAMPLERATE = 48000

# Backoff after a failed window / stream fault, so a persistent fault (mic
# unplugged, bad rate) can't spin the loop at 100% CPU flooding the log.
ERROR_BACKOFF_SECONDS = 1.0

# Cap the block queue so a wedged analyzer can't grow memory without bound;
# when full we drop the OLDEST block (stale audio is the least useful).
MAX_QUEUED_BLOCKS = 256


class _WindowAccumulator:
    """Collects incoming mono blocks into a ring buffer and emits the most
    recent `window` samples every `hop` samples — the pure, testable core of
    the capture loop (no audio hardware involved)."""

    def __init__(self, window: int, hop: int):
        self.window = window
        self.hop = hop
        self._buf = np.zeros(0, dtype="float32")
        self._since_emit = 0

    def push(self, block: np.ndarray) -> np.ndarray | None:
        """Add a block; return a fresh window snapshot when one is due, else
        None. The returned array is a copy the caller owns and may mutate/hold
        while the stream keeps filling the buffer (push rebinds `_buf`, so it
        never hands back a live view of internal state)."""
        self._buf = np.concatenate([self._buf, block])
        if len(self._buf) > self.window:
            self._buf = self._buf[-self.window :]
        self._since_emit += len(block)
        if len(self._buf) >= self.window and self._since_emit >= self.hop:
            self._since_emit = 0
            return self._buf.copy()
        return None


def device_name(device: int | str | None) -> str:
    """Human-readable name of an input device (index, name substring, or None
    for the system default). Best-effort — never raises."""
    try:
        return sd.query_devices(device, "input")["name"]
    except Exception:  # noqa: BLE001 — naming is best-effort diagnostics
        return "default input" if device is None else str(device)


def _input_devices() -> tuple[list[tuple[int, dict]], int | None]:
    """(input devices as (index, info), system default input index). Best
    effort — returns ([], None) if the audio backend can't be queried."""
    try:
        default_in = sd.default.device[0]
        devices = [
            (i, d) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
        return devices, default_in
    except Exception as exc:  # noqa: BLE001 — no audio backend / PortAudio error
        print(f"Could not query audio devices: {exc}")
        return [], None


def list_input_devices() -> None:
    """Print the available mic input devices with their indices, marking the
    system default — the values usable as BP_INPUT_DEVICE."""
    devices, default_in = _input_devices()
    if not devices:
        return
    print("Input devices (use the index or a name substring as BP_INPUT_DEVICE):")
    for index, dev in devices:
        marker = "  <- default" if index == default_in else ""
        rate = int(dev["default_samplerate"])
        print(f"  {index}: {dev['name']} ({rate} Hz){marker}")


def resolve_device_choice(raw: str, valid_indices: set[int]) -> int | None:
    """Map a picker answer to a device index: blank → None (system default);
    a listed index → that int; anything else → None (fall back to default)."""
    raw = raw.strip()
    if raw == "":
        return None
    if raw.isdigit() and int(raw) in valid_indices:
        return int(raw)
    return None


def select_input_device() -> int | None:
    """Interactively pick a mic. Returns the chosen device index, or None to
    mean 'system default'. Falls back to None if there's nothing to choose or
    the input can't be read."""
    devices, default_in = _input_devices()
    if not devices:
        return None
    print("Select the microphone to listen on:")
    for index, dev in devices:
        marker = " (default)" if index == default_in else ""
        print(f"  [{index}] {dev['name']}{marker}")
    prompt = f"Device index [Enter for default {default_in}]: "
    try:
        raw = input(prompt)
    except EOFError:  # stdin closed / piped-empty — fall back to default
        return None
    except KeyboardInterrupt:  # Ctrl-C at the picker aborts the launch cleanly
        print()
        raise SystemExit(130) from None
    chosen = resolve_device_choice(raw, {i for i, _ in devices})
    if raw.strip() and chosen is None:
        print(f"'{raw.strip()}' isn't a listed index — using the default.")
    if chosen is not None:
        print(f"Listening on: {device_name(chosen)}")
    return chosen


class MicListener:
    def __init__(
        self,
        ears: Ears,
        window_seconds: int,
        *,
        samplerate: int = BIRDNET_SAMPLERATE,
        device: int | str | None = None,
        hop_seconds: float | None = None,
    ):
        self.ears = ears
        self.window_seconds = window_seconds
        # Analyse a fresh window every `hop_seconds`; default = the window
        # length (contiguous, gapless). A smaller hop overlaps windows.
        self.hop_seconds = window_seconds if hop_seconds is None else hop_seconds
        self.samplerate = samplerate
        self.device = device

    def _device_name(self) -> str:
        return device_name(self.device)

    def _analyse_window(
        self,
        window: np.ndarray,
        on_detections: Callable[[list[Detection]], None],
    ) -> None:
        try:
            level = float(np.abs(window).max())
            detections = self.ears.detect_samples(window, self.samplerate)
        except Exception:  # noqa: BLE001 — one bad window never kills the loop
            logger.exception("capture: window failed")
            time.sleep(ERROR_BACKOFF_SECONDS)  # don't spin on a hard fault
            return
        # Heartbeat every window so it's visible the loop is alive and what it
        # heard — the wall alone can't distinguish "listening, nothing yet"
        # from "listener dead". `level` also reveals a silent/wrong input
        # device (near-zero peak).
        if detections:
            logger.info(
                "window (peak %.3f): %s",
                level,
                ", ".join(
                    f"{d.species_common} {d.confidence:.2f}" for d in detections
                ),
            )
        else:
            logger.info("window (peak %.3f): nothing above the floor", level)
            return
        # A raising callback (the gate's paint) must not kill listening either
        # — the loop is the durable part.
        try:
            on_detections(detections)
        except Exception:  # noqa: BLE001
            logger.exception("capture: on_detections callback failed")

    def listen(self, on_detections: Callable[[list[Detection]], None]) -> None:
        """Stream → window → detect → callback, forever. Ctrl-C stops it
        cleanly. Capture runs continuously in PortAudio's thread, so no audio
        is dropped while a window is being analysed. If the stream faults or
        goes silent (mic unplugged), it's logged and reopened after a backoff
        — the listener recovers rather than wedging."""
        logger.info(
            "listening on '%s': %ds windows at %d Hz",
            self._device_name(),
            self.window_seconds,
            self.samplerate,
        )
        while True:
            try:
                self._stream_once(on_detections)
            except KeyboardInterrupt:
                logger.info("listening stopped")
                return
            except Exception:  # noqa: BLE001 — a stream fault must not kill the
                logger.exception("capture: stream error; reopening")  # listener
            time.sleep(ERROR_BACKOFF_SECONDS)

    def _stream_once(self, on_detections: Callable[[list[Detection]], None]) -> None:
        """Open the stream and analyse windows until it faults or falls silent
        (returns so listen() reopens it)."""
        blocks: queue.Queue[np.ndarray] = queue.Queue(maxsize=MAX_QUEUED_BLOCKS)
        warned_statuses: set[str] = set()

        def on_audio(indata, frames, time_info, status) -> None:
            if status:
                # Log each distinct status once per stream, not every callback.
                text = str(status)
                if text not in warned_statuses:
                    warned_statuses.add(text)
                    logger.warning("capture: stream status %s", text)
            # Copy — PortAudio reuses indata's buffer after the callback.
            block = indata[:, 0].copy()
            try:
                blocks.put_nowait(block)
            except queue.Full:  # analyzer lagging — drop the oldest block
                try:
                    blocks.get_nowait()
                except queue.Empty:
                    pass
                blocks.put_nowait(block)

        accumulator = _WindowAccumulator(
            window=int(self.window_seconds * self.samplerate),
            hop=int(self.hop_seconds * self.samplerate),
        )
        # If no audio arrives for a few windows the stream has wedged (mic
        # gone) — give up so listen() reopens it.
        silence_timeout = self.window_seconds * 3 + 5
        with sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=on_audio,
        ):
            while True:
                try:
                    block = blocks.get(timeout=silence_timeout)
                except queue.Empty:
                    logger.warning("capture: no audio for %ss; reopening stream",
                                   silence_timeout)
                    return
                window = accumulator.push(block)
                if window is not None:
                    self._analyse_window(window, on_detections)
