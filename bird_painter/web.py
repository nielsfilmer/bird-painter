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

from . import brush
from .api_docs import describe, openapi_websocket_path
from .config import Config, load_config
from .events import PING_SECONDS, EventHub, absolutize, announce_painted
from .gate import TriggerGate
from .occasions import hat_for
from .placeholder import placeholder_svg
from .runner import PaintRunner
from .store import Store
from .trim import trim_to_bird

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


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


# The values that mean "no, stream it" — everything else hands the file over.
# A bare `?download` is indistinguishable from `?download=` (both parse to the
# empty string), so it streams too; the documented form is `?download=1`.
_NOT_DOWNLOAD = {"", "0", "false", "no", "off"}


def _asked_to_download(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in _NOT_DOWNLOAD


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
        )
        listener = MicListener(
            ears,
            window_seconds=config.analysis_window_seconds,
            device=config.input_device,
        )
        location = (
            f"; location filter {config.latitude}, {config.longitude}"
            if config.latitude is not None
            else ""
        )
        logger.info(
            "listener: painting birds heard on the mic (floor %.2f%s)",
            config.confidence_floor,
            location,
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # The mic thread publishes from off-loop; the hub needs the loop to
        # hop onto before any detection can reach a socket.
        events.bind(asyncio.get_running_loop())
        if config.enable_listener:
            threading.Thread(
                target=_start_listener, args=(config, runner), daemon=True
            ).start()
        else:
            logger.info("listener disabled (BP_ENABLE_LISTENER); wall-only")
        try:
            yield
        finally:
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

    @app.middleware("http")
    async def dev_routes_are_local_only(request: Request, call_next):
        """Refuse /dev/* at the door, before routing.

        Checking inside the handler still leaks the route's shape to the
        network: a GET answers 405 and a trailing slash answers 307, and only
        a real path answers either. Refusing here means an off-machine caller
        sees the same 404 for /dev/paint as for /dev/anything-else. The
        handler keeps its own check — this endpoint spends money, and one
        misordered middleware shouldn't be all that stands between the LAN and
        the brush."""
        if request.url.path.startswith("/dev/") and not _is_loopback(request.client):
            # Debug, not info: an unauthenticated remote caller would otherwise
            # choose how fast this wall's disk fills up.
            logger.debug("dev route refused for %s (loopback only)", request.client)
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    # Exposed for tests and debugging; not part of any API contract.
    app.state.config = config
    app.state.store = store
    app.state.events = events

    @app.get("/", response_class=HTMLResponse)
    def wall() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/layout.js")
    def layout_js() -> FileResponse:
        # The wall imports this ES module (its layout maths, unit-tested).
        return FileResponse(STATIC_DIR / "layout.js", media_type="text/javascript")

    @app.get("/api/docs", response_class=HTMLResponse)
    def api_docs_page() -> str:
        """The API, documented for a human: every endpoint, every WebSocket
        event, and a live console wired to this wall's own stream. It renders
        `/api` — so the page can't drift from the description."""
        return (STATIC_DIR / "api-docs.html").read_text(encoding="utf-8")

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

    @app.get("/wall.png")
    def wall_png() -> Response:
        """The collage rendered server-side to a PNG — what the e-paper frame
        (Phase 4) fetches, since it can't run the browser wall. Same live set,
        same layout maths, so it mirrors the on-screen wall."""
        from .render import render_wall_png

        paintings = [
            {
                "file": p.file,
                "species_common": p.species_common,
                "born_at": p.born_at,
            }
            for p in store.live()[: config.wall_max_live]
        ]
        png = render_wall_png(
            paintings,
            config.archive_dir,
            config.wall_png_width,
            config.wall_png_height,
            font=config.wall_font,
            italic_font=config.wall_font_italic,
        )
        return Response(content=png, media_type="image/png")

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
            filename=filename if _asked_to_download(download) else None,
        )

    @app.get("/images/{filename}")
    def image(filename: str) -> FileResponse:
        path = store.image_path(filename)
        if path is None:
            raise HTTPException(status_code=404)
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

        Off-machine callers get 404 — the middleware above turns away
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
