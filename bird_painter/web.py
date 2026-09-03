"""FastAPI app: serves the wall page, the live-set API, the live detection
WebSocket, archived images, and a dev endpoint that paints a named species
(real brush with FAL_KEY, else a placeholder) alongside the detection-driven
trigger gate.

`create_app(config)` is the factory (tests inject throwaway config/archives).
There is deliberately NO module-level app instance — importing this module has
no side effects; uvicorn builds the production app via
`uvicorn.run("bird_painter.web:create_app", factory=True)` (see __main__)."""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import logging
import mimetypes
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import brush
from .api_docs import describe, openapi_websocket_path
from .config import Config, load_config
from .events import PING_SECONDS, EventHub, absolutize, announce_painted
from .gate import TriggerGate
from .night import watch_from_config
from .occasions import hat_for
from .placeholder import placeholder_svg
from .runner import PaintRunner
from .store import Store
from .trim import trim_to_bird

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
# The /wall.png knobs. Named here so the endpoint, its validation and the API
# documentation all read the same sets — api_docs.py imports them.
WALL_LAYERS = ("all", "picture", "text")
WALL_STYLES = ("wall", "panel")
# /api/layout sizes a plan for any viewport a caller names. The bound is a
# sanity range, not a defence: the plan's cost is set by the live set
# (wall_max_live plates, ink measured once and cached), not by the size —
# review measured ~4 ms at both 64² and 8192². What the bound refuses is a
# size no screen has, which would only produce a plan nobody can draw.
LAYOUT_MIN_SIDE = 64
LAYOUT_MAX_SIDE = 8192
# /api/layout?caption= scales the panel's fixed-size type through the plan.
# Same bounds as layout.js's CAPTION_SCALE_MIN/MAX for the spiral's knob — a
# test pins the two together — so one number in a kiosk URL means one thing.
LAYOUT_CAPTION_MIN = 0.5
LAYOUT_CAPTION_MAX = 2.0


# The page and the module it imports must come from the same deploy. A
# kiosk Chromium keeps its disk cache across relaunches and, with no
# directive, reuses a "fresh enough" layout.js against a newly fetched
# index.html — the import then fails silently and the wall shows its empty
# state (#151, seen on the first unit). `no-cache` means "ask before
# reusing", not "don't cache". A bare FileResponse sends an ETag but does
# not answer a conditional request with 304 (that lives in StaticFiles), so
# every load re-downloads the 21 KB module over loopback — cheap, and not
# worth conditional handling here. The issue's optional `?v=<mtime>` on the
# import is skipped on purpose: with both files revalidated the mismatch
# window is the milliseconds between two requests of one page load.
REVALIDATE = {"Cache-Control": "no-cache"}


def _is_loopback(client: tuple[str, int] | None) -> bool:
    """Whether a request came from this machine.

    Decided on the peer address only — never on a header. `X-Forwarded-For`
    and friends are attacker-supplied, and this wall has no authentication to
    fall back on (__main__ turns uvicorn's proxy-header rewriting off so the
    peer really is the peer). A missing peer — a transport that reports none —
    counts as remote, so the restriction fails closed.

    A dual-stack listener (BP_HOST=::) reports a v4 client as the
    IPv4-mapped `::ffff:127.0.0.1`, which Python only calls loopback from
    3.13; this project targets 3.11, and a Pi's own curl shouldn't depend on
    the interpreter's minor version, so mapped addresses are unmapped first.
    """
    if client is None:
        return False
    try:
        address = ipaddress.ip_address(client[0])
    except ValueError:
        return False
    unmapped = getattr(address, "ipv4_mapped", None)
    return (unmapped or address).is_loopback


# Prefixes only this machine may reach. /dev spends money past the cap;
# /unit (the table model's own settings screen, #123) changes the unit.
# Matched on a path boundary: `/unit` and `/unit/...`, never `/unittest`.
LOCAL_ONLY_PREFIXES = ("/dev", "/unit")


def _route_path(scope: Scope) -> str:
    """The path Starlette routes on — a mirror of starlette's
    `get_route_path`: `root_path` is stripped only when it ends on a `/`
    boundary (a mount at `/d` does not own `/dev/...`). The old guard
    compared the raw path, so a wall served under `--root-path /wall` let
    `/wall/dev/...` through to the handler's own check — refused there, but
    with the 405/307 answers that map the routes for the network (#95)."""
    path: str = scope.get("path", "")
    root = scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return ""
    if path[len(root)] == "/":
        return path[len(root):]
    return path


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


class LocalOnly:
    """Pure-ASGI guard: refuse the loopback-only prefixes at the door, for
    HTTP and WebSocket alike — an `@app.middleware("http")` never sees a
    websocket scope, so a future /dev socket would have bypassed it (#95).

    Refusing before routing means an off-machine caller sees the same 404
    for /dev/paint as for /dev/anything-else; a handler-level check answers
    405 to a GET and 307 to a trailing slash, and only a real path does. The
    handlers keep their own checks: these endpoints spend money or change
    the unit, and one misordered middleware shouldn't be all that stands
    between the network and them.

    A refused websocket is closed before accept. uvicorn turns that into a
    403 on the handshake and drops the code; the 1008 is what an in-process
    client (the test suite) sees, and distinguishes "refused" from
    Starlette's own 1000 for a socket path that doesn't exist."""

    def __init__(self, app: ASGIApp, prefixes: tuple[str, ...] = LOCAL_ONLY_PREFIXES):
        self.app = app
        self.prefixes = tuple(prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] in ("http", "websocket")
            and _under(_route_path(scope), self.prefixes)
            and not _is_loopback(scope.get("client"))
        ):
            # Debug, not info: an unauthenticated remote caller would otherwise
            # choose how fast this wall's disk fills up.
            logger.debug("local-only route refused for %s", scope.get("client"))
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse({"detail": "Not Found"}, status_code=404)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _base_url(websocket: WebSocket) -> str:
    """The http origin matching how this client reached us ('ws' → 'http'), so
    the urls in the stream are fetchable by whoever is listening — a phone on
    the LAN gets the LAN address, not localhost.

    Starlette builds the netloc from the Host header, falling back to the
    socket the request arrived on, so a client that sends no Host still gets a
    reachable origin."""
    url = websocket.url
    scheme = "https" if url.scheme == "wss" else "http"
    return f"{scheme}://{url.netloc}"


# The values that mean "no" for a query-string flag (`?download=`, `?bare=`)
# — everything else means yes. A bare `?download` is indistinguishable from
# `?download=` (both parse to the empty string), so it counts as no too; the
# documented form is `?download=1`. Flags on links people type and share are
# read leniently: a typo should hand over the thing, not a 422.
_FLAG_OFF = {"", "0", "false", "no", "off"}


def _flag_set(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _FLAG_OFF


def _is_replay_duplicate(event: dict, replayed: list[dict]) -> bool:
    """Whether this event was already sent in the `hello` replay.

    Matched by identity, never equality: two different birds can produce equal
    dicts. The overlap can only be a PREFIX of the queue — everything the
    snapshot held was published before everything that follows — so the first
    fresh event ends it, and the list is dropped rather than searched forever.
    """
    if not replayed:
        return False
    if any(event is already_sent for already_sent in replayed):
        return True
    replayed.clear()
    return False


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client goes away. The stream is send-only, so anything
    a client sends is ignored — but the receive itself is what makes a dropped
    connection observable at all."""
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
    except (WebSocketDisconnect, RuntimeError, OSError):
        # RuntimeError: starlette refuses a receive after the disconnect it
        # already delivered — same meaning, the client is gone.
        return


def _start_listener(config: Config, runner: PaintRunner) -> None:
    """Load BirdNET and run the mic loop, painting heard birds onto the wall.
    Runs in a daemon thread so the wall serves immediately; a mic/model
    failure is logged and leaves the wall running (still usable via
    /dev/paint)."""
    try:
        from .capture import MicListener
        from .ears import Ears

        ears = Ears(
            confidence_floor=config.confidence_floor,
            latitude=config.latitude,
            longitude=config.longitude,
            seasonal=config.seasonal_filter,
        )
        listener = MicListener(
            ears,
            window_seconds=config.analysis_window_seconds,
            device=config.input_device,
        )
        # Say what the filter allows, not just that one is on. A species the
        # filter excludes produces no detection and no log line, so without
        # this a working microphone and a filtered-out bird look identical
        # from the console. The count carries the model's own total beside it,
        # since "259 species" means nothing without knowing it started at 6522.
        filter_note = ""
        if config.latitude is not None:
            scope = "location + season" if config.seasonal_filter else "location"
            allowed = ears.allowed_species_count()
            counts = ""
            if allowed is not None:
                # Only ask for the baseline once there's a number to compare
                # it against.
                total = ears.species_count()
                counts = f" — {allowed} species" + (f" of {total}" if total else "")
            filter_note = (
                f"; {scope} filter {config.latitude}, {config.longitude}{counts}"
            )
        logger.info(
            "listener: painting birds heard on the mic (floor %.2f%s)",
            config.confidence_floor,
            filter_note,
        )
        listener.listen(runner.on_detections)
    except Exception:  # noqa: BLE001 — the wall must survive a broken listener
        logger.exception("listener failed to start; wall runs without it")


def create_app(config: Config | None = None) -> FastAPI:
    config = load_config() if config is None else config
    store = Store(
        config.archive_dir,
        config.paint_ttl_seconds,
        retention_seconds=config.retention_days * 24 * 60 * 60,
    )
    gate = TriggerGate(store, config.paint_ttl_seconds, config.max_paints_per_hour)
    events = EventHub()
    runner = PaintRunner(config, store, gate, events)
    night = watch_from_config(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The mic thread publishes from off-loop; the hub needs the loop to
        # hop onto before any detection can reach a socket.
        events.bind(asyncio.get_running_loop())
        night.start()
        if config.enable_listener:
            threading.Thread(
                target=_start_listener, args=(config, runner), daemon=True
            ).start()
        else:
            logger.info("listener disabled (BP_ENABLE_LISTENER); wall-only")
        try:
            yield
        finally:
            night.stop()
            events.unbind()

    app = FastAPI(
        title="bird-painter",
        # OpenAPI can't describe a WebSocket, so Swagger's reader is pointed at
        # the page that can — see /api/docs.
        description=(
            "The local API of a wall that paints the birds it hears. The live "
            "detection stream is listed below as the upgrade handshake it is "
            "(OpenAPI has no WebSocket operation); to actually watch it, and "
            "for the same endpoints in prose, see [/api/docs](/api/docs)."
        ),
        lifespan=lifespan,
    )
    generated_openapi = app.openapi

    def openapi() -> dict:
        """FastAPI's own schema, plus the WebSocket it cannot see — otherwise a
        reader of /docs never learns the stream exists. Decorating rather than
        rebuilding keeps everything FastAPI puts in there (tags, servers, its
        own caching) instead of quietly dropping it."""
        schema = generated_openapi()
        schema["paths"].update(openapi_websocket_path())
        return schema

    app.openapi = openapi

    app.add_middleware(LocalOnly)

    # Exposed for tests and debugging; not part of any API contract.
    app.state.config = config
    app.state.store = store
    app.state.events = events
    app.state.night = night

    @app.get("/", response_class=HTMLResponse)
    def wall() -> HTMLResponse:
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page, headers=REVALIDATE)

    @app.get("/layout.js")
    def layout_js() -> FileResponse:
        # The wall imports this ES module (its layout maths, unit-tested).
        return FileResponse(
            STATIC_DIR / "layout.js", media_type="text/javascript", headers=REVALIDATE
        )

    @app.get("/api/docs", response_class=HTMLResponse)
    def api_docs_page() -> HTMLResponse:
        """The API, documented for a human: every endpoint, every WebSocket
        event, and a live console wired to this wall's own stream. It renders
        `/api` — so the page can't drift from the description."""
        page = (STATIC_DIR / "api-docs.html").read_text(encoding="utf-8")
        return HTMLResponse(page, headers=REVALIDATE)

    @app.get("/api")
    def api_description() -> JSONResponse:
        """The same documentation as JSON, with this instance's settings —
        what `/api/docs` reads, and what a script would."""
        return JSONResponse(describe(config))

    @app.get("/api/live")
    def live() -> JSONResponse:
        paintings = store.live()[: config.wall_max_live]
        return JSONResponse(
            {
                "ttl_seconds": config.paint_ttl_seconds,
                # True between the night hours: the page dims itself on it
                # (the backlight, where there is one, is dimmed server-side).
                "night": bool(night.is_night),
                "paintings": [
                    {
                        "file": p.file,
                        "species_common": p.species_common,
                        "species_scientific": p.species_scientific,
                        "born_at": p.born_at,
                        # The detection clip, when one was archived — the wall
                        # makes such plates clickable to replay the sound.
                        "audio": store.audio_file_for(p.file),
                    }
                    for p in paintings
                ],
            }
        )

    @app.websocket("/ws/detections")
    async def ws_detections(websocket: WebSocket) -> None:
        """Live stream of what the ears hear: a `detected` event per
        recognition (gated or not) and a `painted` event per painting, the
        latter carrying the bird's name, the time, the image url and the
        detection clip's url. Connecting replays the recent backlog first, so
        a client opening during a quiet hour still sees the last birds."""
        await websocket.accept()
        base = _base_url(websocket)
        with events.subscribe() as queue:
            # Subscribe FIRST, then snapshot: an event published in between is
            # replayed and queued, never lost. The pump drops the duplicate.
            replayed = events.backlog()
            # A client that vanishes rudely — lid shut, wifi gone, process
            # killed — never sends a close frame, and asyncio discards writes
            # to a lost transport silently, so SENDING can't detect it. Only a
            # pending receive can. Race it against the send pump so the
            # subscriber is always reaped; otherwise a 24/7 wall accumulates
            # zombie subscribers, each with a queue and a coroutine.
            watcher = asyncio.create_task(_watch_for_disconnect(websocket))
            getter: asyncio.Task | None = None
            try:
                await websocket.send_json(
                    {
                        "type": "hello",
                        "at": time.time(),
                        "ping_seconds": PING_SECONDS,
                        "recent": [absolutize(event, base) for event in replayed],
                    }
                )
                while True:
                    # The getter outlives a ping timeout on purpose: cancelling
                    # a queue.get() that has already taken an item would drop
                    # that bird on the floor.
                    if getter is None:
                        getter = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        {getter, watcher},
                        timeout=PING_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if watcher in done:
                        break
                    if getter not in done:
                        await websocket.send_json({"type": "ping", "at": time.time()})
                        continue
                    event, getter = getter.result(), None
                    # The backlog snapshot above was taken after subscribing —
                    # safe against loss, but the same event can also arrive
                    # here. Send it once.
                    if _is_replay_duplicate(event, replayed):
                        continue
                    await websocket.send_json(absolutize(event, base))
            except WebSocketDisconnect:
                pass
            except (RuntimeError, OSError):
                # Socket died mid-send (connection reset) — normal for a
                # long-lived stream, not worth a traceback.
                logger.debug("ws: detection stream closed mid-send")
            finally:
                watcher.cancel()
                if getter is not None:
                    getter.cancel()

    def _require_style(style: str) -> None:
        """422 for a style the renderer doesn't know. Shared by /wall.png and
        /api/layout, which must agree on the set — a value one accepted and
        the other refused would be a plan nobody can draw."""
        if style not in WALL_STYLES:
            raise HTTPException(
                status_code=422,
                detail=f"style must be {', '.join(sorted(WALL_STYLES))}",
            )

    def _live_for_render() -> list[dict]:
        """The live set in the shape the planner and renderer take — the same
        list for /wall.png and /api/layout, so the picture and the plan are
        of the same birds."""
        return [
            {
                "file": p.file,
                "species_common": p.species_common,
                "born_at": p.born_at,
            }
            for p in store.live()[: config.wall_max_live]
        ]

    @app.get("/wall.png")
    def wall_png(layer: str = "all", style: str = "wall") -> Response:
        """The collage rendered server-side to a PNG — what the e-paper frame
        fetches, since it can't run the browser wall. The default `style=wall`
        shares the wall's layout maths and mirrors what the browser shows;
        `style=panel` deliberately does not — see below.

        `style=panel` renders it for the e-paper frame instead of the browser:
        the panel's own white as the ground (cream isn't one of its six
        colours and dithers into a speckle everywhere), a focal scatter
        instead of the spiral, birds fitted to their cells, and no title.
        `layer=picture|text` then splits that render in two, because dithering
        an 8px italic turns it into speckle — the frame dithers the picture and
        stamps the text through the mask afterwards in pure panel black.
        Defaults give the wall exactly as it always was."""
        from .render import render_wall_png

        # Validate before doing the work, not after: a bad param shouldn't
        # cost a walk of the live set first.
        if layer not in WALL_LAYERS:
            raise HTTPException(
                status_code=422,
                detail=f"layer must be {', '.join(sorted(WALL_LAYERS))}",
            )
        _require_style(style)
        png = render_wall_png(
            _live_for_render(),
            config.archive_dir,
            config.wall_png_width,
            config.wall_png_height,
            font=config.wall_font,
            italic_font=config.wall_font_italic,
            layer=layer,
            style=style,
        )
        return Response(content=png, media_type="image/png")

    @app.get("/api/layout")
    def layout(
        style: str = "panel",
        width: int | None = None,
        height: int | None = None,
        caption: float = 1.0,
    ) -> JSONResponse:
        """Where the birds go, as data — at `caption=1`, the same plan
        `/wall.png` draws; a scaled caption is the browser's own plan.

        The table model runs the browser wall on a panel read from across a
        room, and the owner wants it placed exactly as the e-paper frame is
        (#138). The frame's layout depends on things a browser cannot
        reproduce — each bird's ink measured with scipy, each caption with the
        house serif's own metrics — so instead of porting the layout, the
        browser asks for it. `style=panel` is the frame's focal scatter;
        `style=wall` the spiral, for completeness. Size defaults to the
        configured `/wall.png` size, so a bare call IS the frame's placement;
        the browser passes its own viewport. `caption` scales the panel's
        fixed-size type — and, because it goes through the plan, the room
        reserved for it — for a unit read from further away."""
        from .render import plan_wall

        _require_style(style)
        if not LAYOUT_CAPTION_MIN <= caption <= LAYOUT_CAPTION_MAX:
            raise HTTPException(
                status_code=422,
                detail=f"caption must be {LAYOUT_CAPTION_MIN}..{LAYOUT_CAPTION_MAX}",
            )
        width = config.wall_png_width if width is None else width
        height = config.wall_png_height if height is None else height
        for name, value in (("width", width), ("height", height)):
            if not LAYOUT_MIN_SIDE <= value <= LAYOUT_MAX_SIDE:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{name} must be {LAYOUT_MIN_SIDE}..{LAYOUT_MAX_SIDE}"
                    ),
                )
        plan = plan_wall(
            _live_for_render(),
            config.archive_dir,
            width,
            height,
            style=style,
            font=config.wall_font,
            italic_font=config.wall_font_italic,
            caption_scale=caption,
        )
        return JSONResponse(plan.as_json())

    @app.get("/api/archive")
    def archive(offset: int = 0, limit: int = 60) -> JSONResponse:
        """The browsable archive (browser wall only — the e-paper /wall.png
        render never shows it): everything retention has kept, newest first,
        paginated."""
        offset = max(0, offset)
        limit = max(1, min(limit, 200))
        everything = store.all_paintings()
        page = everything[offset : offset + limit]
        return JSONResponse(
            {
                "total": len(everything),
                "offset": offset,
                "paintings": [
                    {
                        "file": p.file,
                        "species_common": p.species_common,
                        "born_at": p.born_at,
                        "audio": store.audio_file_for(p.file),
                    }
                    for p in page
                ],
            }
        )

    @app.get("/audio/{filename}")
    def audio(filename: str, download: str | None = None) -> FileResponse:
        """The archived detection clip behind a painting (see /api/live's
        `audio` field). 404 for birds painted without one. `?download=1`
        serves it as an attachment — the same bytes, saved rather than
        streamed (the wall's click-to-replay uses the plain url).

        `download` is read leniently: it's a flag on a link people type and
        share, so anything but an explicit no counts as yes — a typo should
        still hand over the sound, not a 422."""
        path = store.audio_path(filename)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=filename if _flag_set(download) else None,
        )

    @app.get("/images/{filename}")
    def image(filename: str, bare: str | None = None) -> Response:
        """An archived painting. `?bare=1` serves it as the e-paper frame
        pastes it — cropped to the bird's own ink with the plate's ground
        keyed out to alpha — so the browser wall's panel mode shows the same
        pixels the frame does (#139). Falls back to the plain file when there
        is nothing to crop (an SVG placeholder, a blank plate)."""
        path = store.image_path(filename)
        if path is None:
            raise HTTPException(status_code=404)
        if _flag_set(bare):
            from .render import bare_bird_png

            png = bare_bird_png(path)
            if png is not None:
                return Response(content=png, media_type="image/png")
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @app.post("/dev/paint/{species}")
    def dev_paint(species: str, request: Request) -> JSONResponse:
        """Paint a named species onto the wall — the real brush when FAL_KEY
        is set, a placeholder plate otherwise. Dev helper alongside the
        detection-driven trigger gate.

        **Reachable only from the wall's own machine.** It bypasses the trigger
        gate's hourly cap, and with FAL_KEY set every call spends real money,
        so it must not be one curl away for anyone on the network (issue #66).

        Off-machine callers get 404 — `LocalOnly` turns away
        everything under /dev/ before routing, so the path's shape doesn't
        leak either. That isn't concealment: /api and /api/docs describe this
        endpoint and its 404 to anyone who asks.

        The check below is the second lock on the same door, deliberately:
        this is the one endpoint that spends money."""
        if not _is_loopback(request.client):
            logger.debug("dev/paint refused for %s (loopback only)", request.client)
            raise HTTPException(status_code=404)
        common = species.replace("-", " ").replace("_", " ").title()
        scientific = brush.UNKNOWN_SCIENTIFIC
        result = brush.paint(
            common,
            scientific,
            fal_key=config.fal_key,
            model=config.fal_model,
            hat=hat_for(datetime.date.today(), config.hat_days, config.hat_dates),
        )
        if isinstance(result, brush.Rejected):
            # The model painted something that isn't a bird on white, twice.
            # 502 rather than 201: nothing was stored, and the reason is the
            # actionable part.
            raise HTTPException(
                status_code=502, detail=f"no usable plate: {result.reason}"
            )
        if result is not None:
            image_bytes, extension = result
            image_bytes = trim_to_bird(image_bytes, extension)
            source = "dev"
        elif not config.fal_key:
            image_bytes, extension = placeholder_svg(common, scientific), "svg"
            source = "dev-placeholder"
        else:
            # Failure policy (PLAN.md): soft failure — no painting, no crash,
            # nothing marked painted; the caller may simply try again.
            raise HTTPException(status_code=502, detail="paint failed; see server log")
        painting = store.add(
            image_bytes=image_bytes,
            extension=extension,
            species_common=common,
            species_scientific=scientific,
            confidence=1.0,
            source=source,
        )
        # Dev paints ride the same stream as heard birds (no clip — nothing
        # was heard), so the socket can be exercised without waiting on a bird.
        announce_painted(events, store, painting)
        return JSONResponse(
            {"painted": painting.file, "source": source}, status_code=201
        )

    return app
