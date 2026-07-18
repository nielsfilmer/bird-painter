"""FastAPI app: serves the wall page, the live-set API, archived images, and
a dev endpoint to drop a placeholder painting onto the wall (until the real
pipeline exists)."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import load_config
from .placeholder import placeholder_svg
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"

config = load_config()
store = Store(config.archive_dir, config.paint_ttl_seconds)
app = FastAPI(title="bird-painter")


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
    """Drop a placeholder painting on the wall. Dev-only helper for the
    skeleton; the real brush (slice 2) replaces the image source."""
    common = species.replace("-", " ").replace("_", " ").title()
    scientific = "Species incognita"
    painting = store.add(
        image_bytes=placeholder_svg(common, scientific),
        extension="svg",
        species_common=common,
        species_scientific=scientific,
        confidence=1.0,
        source="dev",
    )
    return JSONResponse({"painted": painting.file}, status_code=201)
