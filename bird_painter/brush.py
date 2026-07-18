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

FAL_ENDPOINT = "https://fal.run/fal-ai/flux/schnell"
REQUEST_TIMEOUT_SECONDS = 60.0

# The fixed vintage-naturalist house style (PLAN.md "House style").
PROMPT_TEMPLATE = (
    "A vintage naturalist illustration of a {name}, hand-painted "
    "19th-century field-guide plate in the style of John James Audubon, "
    "single bird in full view perched on a branch, fine watercolor and ink "
    "detail, muted earth tones, aged cream paper background with subtle "
    "foxing, no text, no border"
)


def build_prompt(species_common: str, species_scientific: str) -> str:
    name = species_common
    if species_scientific and species_scientific != "Species incognita":
        name = f"{species_common} ({species_scientific})"
    return PROMPT_TEMPLATE.format(name=name)


def paint(
    species_common: str, species_scientific: str, *, fal_key: str
) -> tuple[bytes, str] | None:
    """Paint one bird. Returns (image_bytes, extension) or None on failure."""
    if not fal_key:
        logger.warning("brush: FAL_KEY not set; cannot paint %s", species_common)
        return None
    prompt = build_prompt(species_common, species_scientific)
    try:
        response = httpx.post(
            FAL_ENDPOINT,
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
        if not images or not images[0].get("url"):
            logger.error("brush: fal returned no image for %s", species_common)
            return None
        image_response = httpx.get(
            images[0]["url"], timeout=REQUEST_TIMEOUT_SECONDS
        )
        image_response.raise_for_status()
        content_type = image_response.headers.get("content-type", "")
        extension = "png" if "png" in content_type else "jpg"
        return image_response.content, extension
    except httpx.HTTPError as exc:
        logger.error("brush: paint failed for %s: %s", species_common, exc)
        return None
