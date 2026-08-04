"""The API's self-description: one structure documenting every endpoint and
every WebSocket event.

This is the single source of truth behind both documentation surfaces — `/api`
serves it as JSON (for anything scripting against the wall) and `/api/docs`
renders that same JSON as a human page. Keeping one description means the two
can't drift; adding an endpoint means adding it HERE.

FastAPI's generated `/docs` covers the REST side too, but OpenAPI has no way to
describe a WebSocket stream — which is the half of this API most worth
documenting.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version

from .events import PING_SECONDS


def _version() -> str:
    try:
        return version("bird-painter")
    except PackageNotFoundError:  # running from a source tree, not installed
        return "unknown"


# Example payloads. Kept beside the descriptions on purpose: an example is the
# fastest documentation there is, and a wrong one is worse than none — the
# tests assert these match what the code actually emits.
EXAMPLE_PAINTING_FILE = "1785866477_eurasian-wren_e8665c8c.jpg"
EXAMPLE_CLIP_FILE = "1785866477_eurasian-wren_e8665c8c.wav"

ENDPOINTS: list[dict] = [
    {
        "method": "GET",
        "path": "/",
        "summary": "The wall",
        "description": (
            "The full-screen collage page: birds fade in when heard and out "
            "when their time is up. Meant for a spare screen, not for reading."
        ),
        "returns": "text/html",
    },
    {
        "method": "GET",
        "path": "/api/live",
        "summary": "What's on the wall right now",
        "description": (
            "The live set — paintings younger than the TTL, newest first, "
            "capped at BP_WALL_MAX_LIVE. `audio` is the detection clip's "
            "filename, or null for birds painted without one."
        ),
        "returns": "application/json",
        "example": {
            "ttl_seconds": 10800,
            "paintings": [
                {
                    "file": EXAMPLE_PAINTING_FILE,
                    "species_common": "Eurasian Wren",
                    "species_scientific": "Troglodytes troglodytes",
                    "born_at": 1785866477.948883,
                    "audio": EXAMPLE_CLIP_FILE,
                }
            ],
        },
    },
    {
        "method": "GET",
        "path": "/api/archive",
        "summary": "Everything heard this month",
        "description": (
            "The rolling archive (retention: BP_RETENTION_DAYS), newest first "
            "and paginated. This is what the wall's 'archive' overlay reads."
        ),
        "params": [
            {"name": "offset", "type": "int", "default": 0, "note": "clamped at 0"},
            {
                "name": "limit",
                "type": "int",
                "default": 60,
                "note": "clamped to 1..200",
            },
        ],
        "returns": "application/json",
        "statuses": {
            "200": "a page of the archive",
            "422": "offset/limit weren't numbers (out-of-range values are clamped)",
        },
        "example": {
            "total": 137,
            "offset": 0,
            "paintings": [
                {
                    "file": EXAMPLE_PAINTING_FILE,
                    "species_common": "Eurasian Wren",
                    "born_at": 1785866477.948883,
                    "audio": EXAMPLE_CLIP_FILE,
                }
            ],
        },
    },
    {
        "method": "GET",
        "path": "/images/{filename}",
        "summary": "A painting",
        "description": (
            "An archived painting by filename (as given in `file` fields). "
            "Images only — the archive's metadata is not reachable here."
        ),
        "returns": "image/jpeg, image/png, image/webp or image/svg+xml",
        "statuses": {
            "200": "the painting",
            "404": "no such painting, or a name that isn't a servable image",
        },
    },
    {
        "method": "GET",
        "path": "/audio/{filename}",
        "summary": "The sound a bird was recognised from",
        "description": (
            "The archived detection clip: the seconds of microphone audio that "
            "produced the painting. Streams inline (the wall replays it on "
            "click); add `?download=1` to save it instead — same bytes, served "
            "as an attachment. 404 for birds painted without a clip."
        ),
        "params": [
            {
                "name": "download",
                "type": "flag",
                "default": None,
                "note": (
                    "any value serves an attachment instead of streaming, "
                    "except an explicit no (empty, 0, false, no, off) — "
                    "so a bare ?download streams"
                ),
            }
        ],
        "returns": "audio/wav",
        "statuses": {
            "200": "the clip",
            "404": "no clip for that painting, or a name that isn't a .wav",
        },
    },
    {
        "method": "GET",
        "path": "/wall.png",
        "summary": "The wall as an image",
        "description": (
            "The same collage rendered server-side, for the e-paper frame — "
            "which can't run a browser. Size follows BP_WALL_PNG_WIDTH/HEIGHT."
        ),
        "returns": "image/png",
    },
    {
        "method": "POST",
        "path": "/dev/paint/{species}",
        "summary": "Paint a species by hand",
        "description": (
            "Dev helper: paints the named species immediately, bypassing the "
            "microphone and the trigger gate. Uses the real brush when FAL_KEY "
            "is set, a placeholder plate otherwise. The painting has no "
            "detection clip — nothing was heard."
        ),
        "returns": "application/json",
        "statuses": {
            "201": "painted; `source` is `dev`, or `dev-placeholder` with no FAL_KEY",
            "502": "the brush failed (fal outage) — nothing painted, try again",
        },
        "example": {"painted": EXAMPLE_PAINTING_FILE, "source": "dev"},
    },
    {
        "method": "GET",
        "path": "/api",
        "summary": "This description, as JSON",
        "description": "Machine-readable index of every endpoint and event.",
        "returns": "application/json",
    },
    {
        "method": "GET",
        "path": "/api/docs",
        "summary": "This description, as a page",
        "description": (
            "The human-readable version, with a live console that connects to "
            "the detection stream so you can watch it work."
        ),
        "returns": "text/html",
    },
    {
        "method": "GET",
        "path": "/docs",
        "summary": "OpenAPI (Swagger UI)",
        "description": (
            "FastAPI's generated reference, with /openapi.json behind it. "
            "The stream is listed there too — as the upgrade handshake it "
            "is, since OpenAPI has no WebSocket operation — but only this "
            "page can actually connect to it."
        ),
        "returns": "text/html",
    },
]

WEBSOCKET: dict = {
    "path": "/ws/detections",
    "summary": "Live stream of birds being recognised",
    "description": (
        "Connect and watch recognition happen: an event when the ears hear a "
        "bird, another when its painting lands. Every url in an event is "
        "absolute and addressed the way you connected, so the links are "
        "fetchable as-is from another machine."
    ),
    "notes": [
        "Connecting replays the recent events first (birds are rare — a fresh "
        "connection shouldn't open onto an empty quiet hour).",
        f"A ping event arrives every {PING_SECONDS}s so an idle connection "
        "survives NAT and proxies. Nothing is expected back; the server "
        "ignores what you send.",
        "A client too slow to keep up loses its oldest events rather than "
        "stalling the microphone. Reconnect and the backlog catches you up.",
        "No authentication — like the rest of the wall, it's meant for your "
        "own network.",
    ],
    "events": [
        {
            "type": "hello",
            "description": (
                "Sent once, on connect. `recent` replays the last events "
                "(same shapes as below); `ping_seconds` is the keepalive "
                "interval."
            ),
            "example": {
                "type": "hello",
                "at": 1785866474.154511,
                "ping_seconds": PING_SECONDS,
                "recent": [],
            },
        },
        {
            "type": "detected",
            "description": (
                "A bird was recognised. `will_paint` is the trigger gate's "
                "verdict: false means the per-species cooldown or the hourly "
                "cap swallowed this one, so no painting follows. True means a "
                "painting is being attempted — a `painted` event follows "
                "unless the brush fails."
            ),
            "example": {
                "type": "detected",
                "species_common": "Eurasian Wren",
                "species_scientific": "Troglodytes troglodytes",
                "confidence": 0.8123,
                "at": 1785866477.512,
                "time": "2026-08-04T20:01:17.512000+02:00",
                "will_paint": True,
            },
        },
        {
            "type": "painted",
            "description": (
                "A painting landed on the wall. `audio` is null when no clip "
                "was archived (a hand-painted bird, or a clip that failed); "
                "`download_url` serves the same sound as a file to save."
            ),
            "example": {
                "type": "painted",
                "species_common": "Eurasian Wren",
                "species_scientific": "Troglodytes troglodytes",
                "confidence": 0.8123,
                "at": 1785866477.948883,
                "time": "2026-08-04T20:01:17.948883+02:00",
                "source": "detection",
                "image": {
                    "file": EXAMPLE_PAINTING_FILE,
                    "url": f"http://wall.local:8537/images/{EXAMPLE_PAINTING_FILE}",
                },
                "audio": {
                    "file": EXAMPLE_CLIP_FILE,
                    "url": f"http://wall.local:8537/audio/{EXAMPLE_CLIP_FILE}",
                    "download_url": (
                        f"http://wall.local:8537/audio/{EXAMPLE_CLIP_FILE}?download=1"
                    ),
                },
            },
        },
        {
            "type": "ping",
            "description": "Keepalive. Nothing to do with birds.",
            "example": {"type": "ping", "at": 1785866504.1},
        },
    ],
}


def describe(config) -> dict:
    """The whole API description, with this instance's actual settings folded
    in — the numbers a caller needs (how long a bird lasts, how many the wall
    holds) are configuration, not documentation."""
    return {
        "service": "bird-painter",
        "version": _version(),
        "description": (
            "A microphone listens outside, BirdNET recognises the birds, and "
            "each one is painted onto a wall that fades. This is its local API."
        ),
        "settings": {
            "paint_ttl_seconds": config.paint_ttl_seconds,
            "wall_max_live": config.wall_max_live,
            "max_paints_per_hour": config.max_paints_per_hour,
            "confidence_floor": config.confidence_floor,
            "retention_days": config.retention_days,
            "listener_enabled": config.enable_listener,
        },
        "endpoints": ENDPOINTS,
        "websocket": WEBSOCKET,
    }


def openapi_websocket_path() -> dict:
    """An OpenAPI `paths` entry for the detection stream.

    OpenAPI has no WebSocket operation, so a reader of the generated `/docs`
    would otherwise never learn the stream exists — the complaint that
    prompted this. The compromise every WS-bearing FastAPI app makes: describe
    the handshake as what it literally is, a GET that upgrades, marked plainly
    so nobody mistakes it for a normal request, with the event shapes inline
    and a pointer to the page that can do it justice.
    """
    events = "\n\n".join(
        f"**`{event['type']}`** — {event['description']}\n\n"
        f"```json\n{json.dumps(event['example'], indent=2)}\n```"
        for event in WEBSOCKET["events"]
    )
    notes = "\n".join(f"- {note}" for note in WEBSOCKET["notes"])
    return {
        WEBSOCKET["path"]: {
            "get": {
                "tags": ["websocket"],
                "summary": f"[WebSocket] {WEBSOCKET['summary']}",
                "description": (
                    f"**This is a WebSocket endpoint, not a plain GET** — connect to "
                    f"`ws://<host>{WEBSOCKET['path']}` (`wss://` over TLS). "
                    f"'Try it out' below cannot open one; the live console at "
                    f"[/api/docs](/api/docs) can.\n\n"
                    f"{WEBSOCKET['description']}\n\n"
                    f"{notes}\n\n### Events\n\n{events}"
                ),
                "operationId": "detections_stream",
                "responses": {
                    "101": {"description": "Switching Protocols — the stream is open"},
                    "404": {
                        "description": (
                            "A plain GET. This path answers a WebSocket upgrade "
                            "and nothing else, so an ordinary request finds no "
                            "route here — including Swagger's own 'Try it out'."
                        )
                    },
                },
            }
        }
    }
