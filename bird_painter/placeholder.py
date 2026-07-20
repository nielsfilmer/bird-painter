"""Placeholder 'paintings' for when FAL_KEY is unset (dev / tests / QA): a
textless bird cutout on plain white, in the spirit of the vintage-naturalist
house style — same shape the real brush (fal FLUX) produces, so the wall's
multiply-blend drops the white and leaves just the bird. No text, no frame:
the wall carries no per-bird label, and the images must carry none either."""

from __future__ import annotations

import hashlib

_PALETTE = ["#8a6d3b", "#5b7065", "#7a5c61", "#54687e", "#6e6248", "#4f5d4a"]


def placeholder_svg(species_common: str, species_scientific: str) -> bytes:
    # species names only pick a stable tint — nothing is drawn as text.
    tint = _PALETTE[
        int(hashlib.sha256(species_common.encode()).hexdigest(), 16) % len(_PALETTE)
    ]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500">
  <rect width="400" height="500" fill="#ffffff"/>
  <ellipse cx="196" cy="262" rx="94" ry="66" fill="{tint}" opacity="0.9"/>
  <circle cx="272" cy="212" r="37" fill="{tint}"/>
  <polygon points="305,206 342,216 305,226" fill="#3d3324"/>
  <circle cx="284" cy="205" r="5" fill="#ffffff"/>
  <path d="M 150 250 Q 96 216 82 254 Q 124 282 162 272 Z" fill="#3d3324" opacity="0.3"/>
  <line x1="182" y1="324" x2="182" y2="386" stroke="#3d3324" stroke-width="5"/>
  <line x1="222" y1="324" x2="222" y2="386" stroke="#3d3324" stroke-width="5"/>
  <line x1="120" y1="388" x2="292" y2="388" stroke="#3d3324" stroke-width="5"/>
</svg>"""
    return svg.encode()
