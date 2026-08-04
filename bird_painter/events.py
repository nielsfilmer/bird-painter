"""The live event stream: what the ears heard and what the brush painted,
broadcast to WebSocket clients (`/ws/detections`).

The wall polls `/api/live`; this is the push side of the same story, for
anything that wants to *watch* recognition happen (a notifier, a log, a phone
on the couch). Two producers publish here — the mic listener thread (via
`PaintRunner`) and the `/dev/paint` request thread — while the consumers are
coroutines on the event loop, so `EventHub.publish` is thread-safe and hops
onto the loop via `call_soon_threadsafe`.

Events carry ROOT-RELATIVE urls (`/images/x.png`); the WebSocket endpoint
rewrites them to absolute urls per connection, so a client on the network gets
links it can actually fetch.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Idle keepalive on the detection socket. Birds are rare — an hour of silence is
# normal — so the stream pings to keep proxies/NAT from reaping a healthy
# connection. It does NOT detect a client that vanished: writes to a lost
# transport succeed silently (see web.py's _watch_for_disconnect).
PING_SECONDS = 30

# Every event type the stream can emit. The roster lives here so the docs can
# be checked against it — add a type here when you add one.
EVENT_TYPES = frozenset({"hello", "detected", "painted", "ping"})

# Per-subscriber buffer. A client too slow to drain this loses its OLDEST
# events (never blocks the mic thread) — birds are rare, so overflow means a
# client that's effectively gone.
QUEUE_SIZE = 100
# How many recent events a fresh connection gets replayed, so it doesn't open
# onto silence during a quiet hour.
BACKLOG_SIZE = 20


def _iso(timestamp: float) -> str:
    """Local-time ISO 8601 with offset — the wall is a thing in a room, so
    'when' is most useful in the room's own clock."""
    return datetime.datetime.fromtimestamp(timestamp).astimezone().isoformat()


def detected_event(
    *,
    species_common: str,
    species_scientific: str,
    confidence: float,
    at: float,
    will_paint: bool,
) -> dict:
    """A bird recognized by the ears. `will_paint` is the trigger gate's
    verdict (per-species cooldown + hourly cap): false means no painting is
    coming for this detection, true means one is being attempted — a `painted`
    event follows unless the brush soft-fails (fal outage / no key)."""
    return {
        "type": "detected",
        "species_common": species_common,
        "species_scientific": species_scientific,
        "confidence": round(confidence, 4),
        "at": at,
        "time": _iso(at),
        "will_paint": will_paint,
    }


def painted_event(painting, audio_file: str | None) -> dict:
    """A painting that landed: name, time, image, and the detection clip
    (when one was archived — dev paints and clip failures have none)."""
    return {
        "type": "painted",
        "species_common": painting.species_common,
        "species_scientific": painting.species_scientific,
        "confidence": round(painting.confidence, 4),
        "at": painting.born_at,
        "time": _iso(painting.born_at),
        "source": painting.source,
        "image": {"file": painting.file, "url": f"/images/{painting.file}"},
        "audio": (
            {
                "file": audio_file,
                "url": f"/audio/{audio_file}",
                # Same bytes, served as an attachment — for clients that want
                # to save the sound rather than stream it.
                "download_url": f"/audio/{audio_file}?download=1",
            }
            if audio_file
            else None
        ),
    }


def announce_painted(hub: EventHub | None, store, painting) -> None:
    """Broadcast a painting that landed — from either producer (the mic thread
    or a /dev/paint request).

    Everything here is best-effort by contract: the painting is already
    archived, so neither the clip lookup (filesystem I/O, so it CAN raise) nor
    the broadcast may cost it. Both producers go through this, so that promise
    lives in one place."""
    if hub is None:
        return
    try:
        hub.publish(painted_event(painting, store.audio_file_for(painting.file)))
    except OSError:
        logger.exception("events: could not announce %s", painting.file)


def absolutize(event: dict, base_url: str) -> dict:
    """Rewrite the event's root-relative urls against `base_url`
    ('http://host:port'), leaving the rest of the payload untouched."""
    out = dict(event)
    for key in ("image", "audio"):
        asset = out.get(key)
        if not isinstance(asset, dict):
            continue
        out[key] = {
            field: (
                f"{base_url}{value}"
                if field.endswith("url") and isinstance(value, str)
                else value
            )
            for field, value in asset.items()
        }
    return out


class EventHub:
    """Fan-out of events to live subscribers, plus a small replay backlog.

    `publish` is callable from any thread; `subscribe` must be used from the
    event loop. Before the loop is bound (`bind`, at app startup) publishing
    only fills the backlog — which is what tests and a listener that somehow
    beats startup both want."""

    def __init__(self, backlog_size: int = BACKLOG_SIZE, queue_size: int = QUEUE_SIZE):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()
        self._backlog: deque[dict] = deque(maxlen=backlog_size)
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    def unbind(self) -> None:
        """Shutdown: stop handing events to a loop that's going away."""
        with self._lock:
            self._loop = None

    def backlog(self) -> list[dict]:
        with self._lock:
            return list(self._backlog)

    def publish(self, event: dict) -> None:
        """Broadcast an event. Safe from any thread; never blocks on a slow
        client, never raises into the caller (a mic thread must not die
        because a socket did)."""
        with self._lock:
            # Append and schedule under the SAME lock: with two producer
            # threads, releasing between them would let the backlog and the
            # subscribers see the two events in opposite orders.
            self._backlog.append(event)
            if self._loop is None:
                return
            try:
                self._loop.call_soon_threadsafe(self._fanout, event)
            except RuntimeError:
                # Loop already closed (shutdown race) — the backlog still has it.
                logger.debug("events: loop closed, dropping fan-out")

    def _fanout(self, event: dict) -> None:
        """Runs on the event loop, so touching the queues is safe."""
        for queue in list(self._subscribers):
            # One bad subscriber must never cost the others their event, so
            # each delivery stands alone.
            try:
                if queue.full():
                    # Drop the oldest so the newest bird still gets through.
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:  # pragma: no cover — drained
                        pass
                    logger.warning("events: subscriber too slow, dropped an event")
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover — lost a get() race
                logger.warning("events: subscriber queue full, dropped an event")

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
