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
            "when their time is up. Meant for a spare screen, not for reading. "
            "The params tune it for the table model's portrait panel, "
            "which is read from across a room rather than at a desk; both "
            "default to the look the wall has always had, so a plain / is "
            "unchanged. They live in the URL rather than in BP_* env vars "
            "because they describe the DISPLAY, not the service — one "
            "recorder serves a phone, a laptop and a panel at once, and each "
            "wants its own."
        ),
        "params": [
            {
                "name": "spread",
                "type": "number",
                "default": "0",
                "note": (
                    "a floor on the collage's width, as a fraction of the "
                    "viewport (0 to 0.92). 0 leaves placement entirely to the "
                    "widen-to-fit rule, which on a tall portrait panel stops "
                    "early and uses about 58% of the width; raising it claims "
                    "more of the panel. Out-of-range values clamp; anything "
                    "unreadable falls back to the default"
                ),
            },
            {
                "name": "caption",
                "type": "number",
                "default": "1",
                "note": (
                    "multiplier on the per-bird lettering (0.5 to 2). At 1 "
                    "the type sits on a clamp rail at both panel sizes — 8px "
                    "at 720 wide, 12px at 1200 — around a millimetre of "
                    "glyph. It also grows the room the layout reserves under "
                    "each plate, though that reserve is linear while a wrapped "
                    "caption is not, so it holds on the panels this is built "
                    "for (vmin 7.2 and 12) and not on a small-vmin viewport, "
                    "where labels can still reach the bird below from about "
                    "1.3 up. Capped at 2; see issue #136. With ?style=panel "
                    "the same value is forwarded to /api/layout instead, "
                    "where the plan scales type and reserve together"
                ),
            },
            {
                "name": "ui",
                "type": "number",
                "default": "1",
                "note": (
                    "multiplier on the archive chrome (0.5 to 2): the corner "
                    "button, the overlay's heading and close, 'more', and "
                    "the card lettering. Independent of caption, so a frame "
                    "can size its plates' type and its controls separately; "
                    "the 7\" table model runs 1.5 for both"
                ),
            },
        ],
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
            "Images only — the archive's metadata is not reachable here. "
            "`?bare=1` returns the bird as the e-paper frame pastes it: "
            "cropped to its own ink with the plate's ground keyed out to "
            "alpha, as a PNG — what the browser wall's panel mode shows, so "
            "screen and frame show the same bird in the same cell. Plates "
            "with nothing to crop (an SVG placeholder, a speck) come back "
            "plain."
        ),
        "params": [
            {
                "name": "bare",
                "type": "flag",
                "default": None,
                "note": (
                    "any value serves the ink-cropped, ground-keyed PNG, "
                    "except an explicit no (empty, 0, false, no, off)"
                ),
            }
        ],
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
            "which can't run a browser. Size follows BP_WALL_PNG_WIDTH/HEIGHT. "
            "The defaults render the browser wall exactly as it has always "
            "been; the e-paper frame asks for style=panel and fetches the two "
            "layers separately, because dithering an 8px italic turns it into "
            "speckle — it dithers the picture and stamps the text through the "
            "mask afterwards in flat panel black."
        ),
        "params": [
            {
                "name": "style",
                "type": "enum",
                "default": "wall",
                "note": (
                    "wall = the browser's cream paper and spiral collage; "
                    "panel = the e-paper frame — the panel's own white as the "
                    "ground (cream isn't one of its six colours and dithers "
                    "into a speckle everywhere), the focal scatter, birds "
                    "fitted to their own ink, and no title"
                ),
            },
            {
                "name": "layer",
                "type": "enum",
                "default": "all",
                "note": (
                    "all = one finished image; picture = the collage with no "
                    "lettering; text = the lettering alone, as an 8-bit "
                    "grayscale mask (white where ink goes)"
                ),
            },
        ],
        "returns": "image/png",
        "statuses": {
            "200": "the render",
            "422": "style or layer wasn't one of the values above",
        },
    },
    {
        "method": "GET",
        "path": "/api/layout",
        "summary": "Where the birds go, as data",
        "description": (
            "The placement /wall.png draws, as JSON, for a viewport of the "
            "given size. The table model's browser wall asks for this with "
            "style=panel so it places its plates exactly where the e-paper "
            "frame does — the frame's layout depends on measurements a "
            "browser can't make (each bird's ink, each caption in the house "
            "serif), so the browser fetches the plan instead of porting the "
            "maths. Offsets are from the centre; size_vmin and height_vmin "
            "are in the plan's own vmin; ink is each bird's own bounding "
            "box as fractions of its plate (null for a plate with nothing "
            "to crop, such as an SVG placeholder) — informational: the "
            "browser shows /images/{file}?bare=1, which is already that crop."
        ),
        "params": [
            {
                "name": "style",
                "type": "enum",
                "default": "panel",
                "note": "panel = the frame's focal scatter; wall = the spiral",
            },
            {
                "name": "width",
                "type": "int",
                "default": "BP_WALL_PNG_WIDTH",
                "note": "64..8192; omit both to get the frame's own plan",
            },
            {
                "name": "height",
                "type": "int",
                "default": "BP_WALL_PNG_HEIGHT",
                "note": "64..8192",
            },
            {
                "name": "caption",
                "type": "number",
                "default": "1",
                "note": (
                    "0.5..2; scales the panel's fixed-size type, and with it "
                    "the room the plan reserves under each bird and the "
                    "measured caption widths — so bigger lettering never "
                    "lands on a bird. The 7\" table model runs 1.5. Ignored "
                    "for style=wall, whose type is the browser's own"
                ),
            },
        ],
        "returns": "application/json",
        "statuses": {
            "200": "the plan",
            "422": (
                "unknown style, a size outside 64..8192, or a caption "
                "outside 0.5..2"
            ),
        },
        "example": {
            "style": "panel",
            "width": 1200,
            "height": 1920,
            "band_top": 86.4,
            "layout_h": 1894,
            "vmin": 12.0,
            "species_size": 14,
            "heard_size": 16,
            "caption_gap": 16.8,
            "tracking": 1.2,
            "placements": [
                {
                    "file": EXAMPLE_PAINTING_FILE,
                    "x": -41.7,
                    "y": 122.3,
                    "size_vmin": 31.4,
                    "height_vmin": 38.2,
                    "z": 12,
                }
            ],
            "ink": {EXAMPLE_PAINTING_FILE: [0.21, 0.12, 0.58, 0.79]},
        },
    },
    {
        "method": "POST",
        "path": "/dev/paint/{species}",
        "summary": "Paint a species by hand",
        "description": (
            "Dev helper: paints the named species immediately, bypassing the "
            "microphone and the trigger gate. Uses the real brush when FAL_KEY "
            "is set, a placeholder plate otherwise. The painting has no "
            "detection clip — nothing was heard. "
            # Plain prose, no markdown: these descriptions are rendered as text
            # on /api/docs (its `prose()` does code spans and nothing else), so
            # a `**bold**` here would show its own asterisks to the reader.
            "Reachable only from the wall's own machine — it skips the hourly "
            "cap and, with a key set, spends real money per call, so "
            "off-machine callers get a 404. Everything else here is open on "
            "your network; this one isn't."
        ),
        "returns": "application/json",
        "statuses": {
            "201": "painted; `source` is `dev`, or `dev-placeholder` with no FAL_KEY",
            "404": "you're not on the wall's own machine",
            "502": (
                "no painting: either the brush failed (fal outage — try again) "
                "or every plate came back as something that isn't a bird on "
                "white; the reason says which"
            ),
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
