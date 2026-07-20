"""The brush: species → fal FLUX `schnell` → painting bytes.

Failure policy (PLAN.md): a failed paint is logged and reported as None —
the caller must not consume an hourly-cap slot, must not mark the species
painted, and must never crash the loop. No aggressive retries; the species
simply retries naturally on its next detection.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Sync route is right for schnell (~1-2 s renders). If PLAN.md's upgrade to
# FLUX dev/pro happens, revisit: slower models belong on queue.fal.run.
FAL_BASE = "https://fal.run"
DEFAULT_MODEL = "fal-ai/flux/schnell"
FAL_ENDPOINT = f"{FAL_BASE}/{DEFAULT_MODEL}"  # default; paint() builds from model
REQUEST_TIMEOUT_SECONDS = 60.0

# Sentinel for "BirdNET/dev gave us no scientific name" — shared with web.py.
UNKNOWN_SCIENTIFIC = "Species incognita"

# House style (PLAN.md). Deliberately does NOT say "field-guide plate" /
# "Audubon" / "engraving" — those style words make FLUX bake in engraved
# captions AND an aged-paper ground (which then can't cutout cleanly). Instead:
# a single bird, isolated and centred on FLAT PURE WHITE, with a hard no-text /
# no-paper tail. On white, the wall's multiply-blend drops the ground to a
# clean cutout. schnell follows this loosely; flux/dev (BP_FAL_MODEL) obeys it
# far better — recommended if text/paper still leak through.
PROMPT_TEMPLATE = (
    "A single {name} bird, hand-painted naturalist watercolor, the whole bird "
    "perched in full side view, soft muted natural colors, fine feather "
    "detail, cleanly isolated and centred on a pure flat bright white "
    "background, the bird is the only thing in the image. No text, no words, "
    "no letters, no caption, no label, no numbers, no signature, no watermark, "
    "no border, no frame, no paper texture, no vignette, no scenery, no "
    "background objects."
)


def build_prompt(species_common: str, species_scientific: str) -> str:
    name = species_common
    if species_scientific and species_scientific != UNKNOWN_SCIENTIFIC:
        name = f"{species_common} ({species_scientific})"
    return PROMPT_TEMPLATE.format(name=name)


def paint(
    species_common: str,
    species_scientific: str,
    *,
    fal_key: str,
    model: str = DEFAULT_MODEL,
) -> tuple[bytes, str] | None:
    """Paint one bird. Returns (image_bytes, extension) or None on failure."""
    if not fal_key:
        logger.warning("brush: FAL_KEY not set; cannot paint %s", species_common)
        return None
    prompt = build_prompt(species_common, species_scientific)
    try:
        response = httpx.post(
            f"{FAL_BASE}/{model}",
            headers={"Authorization": f"Key {fal_key}"},
            json={
                "prompt": prompt,
                "image_size": "portrait_4_3",
                "num_images": 1,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        images = response.json().get("images") or []
        if not images or not isinstance(images[0], dict) or not images[0].get("url"):
            logger.error("brush: fal returned no image for %s", species_common)
            return None
        image_response = httpx.get(
            images[0]["url"], timeout=REQUEST_TIMEOUT_SECONDS
        )
        image_response.raise_for_status()
        content_type = images[0].get("content_type") or image_response.headers.get(
            "content-type", ""
        )
        extension = "png" if "png" in content_type else "jpg"
        return image_response.content, extension
    except Exception as exc:  # noqa: BLE001 — soft-failure contract: the loop
        # must survive ANY brush failure (HTTP, JSON decode, shape surprises).
        # On HTTP errors, include the response body: fal puts the actionable
        # reason there (e.g. "Exhausted balance"), not in the status line.
        # Never log request headers — that's where the key lives.
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f" — response: {exc.response.text[:500]}"
        logger.error("brush: paint failed for %s: %s%s", species_common, exc, detail)
        return None
