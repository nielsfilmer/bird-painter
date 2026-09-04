"""The brush: species → fal FLUX `schnell` → painting bytes.

Failure policy (PLAN.md): a failed paint is logged and reported as None —
the caller must not consume an hourly-cap slot, must not mark the species
painted, and must never crash the loop. No aggressive retries; the species
simply retries naturally on its next detection.
"""

from __future__ import annotations

import logging

import httpx

from .plate_check import describe_problem
from .styles import style_for

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
    "A single {name} bird, {look}, the whole bird "
    # "perched in full side view" is a LOAD-BEARING ANCHOR: build_prompt splices
    # the occasion hat in right after it (a test pins this). If you reword it,
    # update build_prompt's replace + the test together.
    "perched in full side view, {palette}, cleanly isolated and centred on a "
    "pure flat bright white "
    "background, the bird is the only thing in the image. No text, no words, "
    "no letters, no caption, no label, no numbers, no signature, no watermark, "
    "no border, no frame, no paper texture, no vignette, no scenery, no "
    "background objects. Not a photograph of a painting: no sheet of paper, "
    "no desk or table, no pencils, brushes or art supplies, no hands, no "
    "sketchbook, no plain coloured blocks or panels — just the bird itself, "
    # NOT "filling the frame": plate_check rejects a subject that reaches the
    # edges, because that's what a photographed desk looks like. Asking for the
    # opposite of what we then throw away would be a slow, expensive way to
    # paint nothing — caught in review before it ever ran unattended.
    "centred with clear white space all around it."
)


class Rejected:
    """Every attempt came back as something other than a bird on white.

    Distinct from None (fal was unreachable, or there's no key) because the
    two deserve opposite treatment: an outage should retry freely on the next
    detection, while a species the model keeps painting wrongly is DETERMINISTIC
    — left free to retry, one persistent singer would spend 480 paid calls an
    hour against a cap of 20. The caller charges this to the hourly cap."""

    def __init__(self, reason: str):
        self.reason = reason


# How many times to ask for a plate before giving up. A small share of
# generations come back as something other than a bird on white (see
# plate_check); one retry catches most of those for a fraction of a cent,
# while a hard cap keeps a persistently confused model from spending in a
# loop.
MAX_ATTEMPTS = 2


def build_prompt(
    species_common: str,
    species_scientific: str,
    hat: str | None = None,
    style: str | None = None,
) -> str:
    name = species_common
    if species_scientific and species_scientific != UNKNOWN_SCIENTIFIC:
        name = f"{species_common} ({species_scientific})"
    chosen = style_for(style)
    prompt = PROMPT_TEMPLATE.format(name=name, look=chosen.look, palette=chosen.palette)
    if hat:
        # Occasion easter egg (see occasions.py): woven in right after the
        # bird so the hat reads as part of the subject, before the no-text
        # tail so that still binds last.
        prompt = prompt.replace(
            "perched in full side view",
            f"perched in full side view, {hat}",
        )
    return prompt


def paint(
    species_common: str,
    species_scientific: str,
    *,
    fal_key: str,
    model: str = DEFAULT_MODEL,
    hat: str | None = None,
    style: str | None = None,
    attempts: int = MAX_ATTEMPTS,
) -> tuple[bytes, str] | Rejected | None:
    """Paint one bird. Returns (image_bytes, extension) or None on failure.

    A plate that clearly isn't a bird on white — a photograph of a painting on
    a desk, a flat block of colour — is asked for again rather than hung on the
    wall; see plate_check. Giving up returns `Rejected`, which the caller
    charges to the hourly cap: unlike an outage, a model that keeps painting
    one species wrongly will keep doing so, and free retries on every detection
    would be a spend loop."""
    if not fal_key:
        logger.warning("brush: FAL_KEY not set; cannot paint %s", species_common)
        return None
    for attempt in range(1, max(1, attempts) + 1):
        painted = _paint_once(
            species_common,
            species_scientific,
            fal_key=fal_key,
            model=model,
            hat=hat,
            style=style,
        )
        if painted is None:
            return None
        problem = describe_problem(*painted)
        if problem is None:
            return painted
        logger.warning(
            "brush: discarding plate for %s (attempt %d/%d): %s",
            species_common,
            attempt,
            attempts,
            problem,
        )
    logger.error(
        "brush: no usable plate for %s after %d attempts; skipping",
        species_common,
        attempts,
    )
    return Rejected(problem)


def _paint_once(
    species_common: str,
    species_scientific: str,
    *,
    fal_key: str,
    model: str,
    hat: str | None,
    style: str | None = None,
) -> tuple[bytes, str] | None:
    """One generation: prompt in, image bytes out. No judgement about what came
    back — that's the caller's, so a retry doesn't re-enter the whole policy."""
    prompt = build_prompt(species_common, species_scientific, hat, style)
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
        image_response = httpx.get(images[0]["url"], timeout=REQUEST_TIMEOUT_SECONDS)
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
