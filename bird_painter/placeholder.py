"""Placeholder 'paintings' for the slice-1 skeleton: a framed SVG plate with
the species name, in the spirit of the vintage-naturalist house style. The
real brush (fal FLUX, slice 2) replaces this as the source of images; the
store and wall don't care which produced a painting."""

from __future__ import annotations

import hashlib

_PALETTE = ["#8a6d3b", "#5b7065", "#7a5c61", "#54687e", "#6e6248", "#4f5d4a"]


def placeholder_svg(species_common: str, species_scientific: str) -> bytes:
    tint = _PALETTE[
        int(hashlib.sha256(species_common.encode()).hexdigest(), 16) % len(_PALETTE)
    ]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500">
  <rect width="400" height="500" fill="#f3ecd9"/>
  <rect x="14" y="14" width="372" height="472" fill="none" stroke="{tint}" stroke-width="3"/>
  <rect x="22" y="22" width="356" height="456" fill="none" stroke="{tint}" stroke-width="1"/>
  <ellipse cx="200" cy="215" rx="86" ry="62" fill="{tint}" opacity="0.85"/>
  <circle cx="272" cy="172" r="34" fill="{tint}"/>
  <polygon points="303,168 336,177 303,186" fill="#3d3324"/>
  <circle cx="283" cy="166" r="4.5" fill="#f3ecd9"/>
  <path d="M 155 205 Q 105 175 92 208 Q 130 232 165 224 Z" fill="#3d3324" opacity="0.35"/>
  <line x1="185" y1="274" x2="185" y2="330" stroke="#3d3324" stroke-width="4"/>
  <line x1="222" y1="274" x2="222" y2="330" stroke="#3d3324" stroke-width="4"/>
  <line x1="120" y1="332" x2="290" y2="332" stroke="#3d3324" stroke-width="5"/>
  <text x="200" y="408" text-anchor="middle" font-family="Georgia, serif"
        font-size="26" fill="#3d3324">{species_common}</text>
  <text x="200" y="438" text-anchor="middle" font-family="Georgia, serif"
        font-size="17" font-style="italic" fill="#6b5d43">{species_scientific}</text>
  <text x="200" y="470" text-anchor="middle" font-family="Georgia, serif"
        font-size="12" fill="#a2926f">placeholder plate</text>
</svg>"""
    return svg.encode()
