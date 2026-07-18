"""FastAPI app: serves the wall page, the live-set API, archived images, and
a dev endpoint that paints a named species (real brush with FAL_KEY, else a
placeholder) until the trigger gate drives painting from detections."""

from __future__ import annotations

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import brush
from .config import load_config
from .gate import TriggerGate
from .placeholder import placeholder_svg
from .runner import PaintRunner
from .store import Store

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

config = load_config()
store = Store(config.archive_dir, config.paint_ttl_seconds)
gate = TriggerGate(store, config.paint_ttl_seconds, config.max_paints_per_hour)
runner = PaintRunner(config, store, gate)


def _start_listener() -> None:
    """Load BirdNET and run the mic loop, painting heard birds onto the wall.
    Runs in a daemon thread so the wall serves immediately; a mic/model
    failure is logged and leaves the wall running (still usable via
    /dev/paint)."""
    try:
        from .capture import MicListener
        from .ears import Ears

        ears = Ears(confidence_floor=config.confidence_floor)
        listener = MicListener(ears, window_seconds=config.analysis_window_seconds)
        logger.info("listener: painting birds heard on the mic")
        listener.listen(runner.on_detections)
    except Exception:  # noqa: BLE001 — the wall must survive a broken listener
        logger.exception("listener failed to start; wall runs without it")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.enable_listener:
        threading.Thread(target=_start_listener, daemon=True).start()
    else:
        logger.info("listener disabled (BP_ENABLE_LISTENER); wall-only")
    yield


app = FastAPI(title="bird-painter", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def wall() -> str:
    return (STATIC_DIR / "index.html").read_text()


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
                }
                for p in paintings
            ],
        }
    )


@app.get("/images/{filename}")
def image(filename: str) -> FileResponse:
    path = store.image_path(filename)
    if path is None:
        raise HTTPException(status_code=404)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@app.post("/dev/paint/{species}")
def dev_paint(species: str) -> JSONResponse:
    """Paint a named species onto the wall — the real brush when FAL_KEY is
    set, a placeholder plate otherwise. Dev helper until the trigger gate
    (slice 5) drives painting from detections."""
    common = species.replace("-", " ").replace("_", " ").title()
    scientific = brush.UNKNOWN_SCIENTIFIC
    result = brush.paint(common, scientific, fal_key=config.fal_key)
    if result is not None:
        image_bytes, extension = result
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
    return JSONResponse({"painted": painting.file, "source": source}, status_code=201)
