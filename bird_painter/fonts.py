"""Where the house serif lives.

Its own module because two very different things need it: `render`, which draws
the wall on the recorder, and `frame_client`, which draws the frame's own
notices on the frame Pi. That Pi installs the package with `--no-deps` and has
no scipy, so the client must not import `render` merely to find a font file —
this module is stdlib plus Pillow, and nothing else.
"""

from __future__ import annotations

from pathlib import Path

# Serif faces to try, in order, when no font is configured. Raspberry Pi OS /
# Debian first (the deploy target), then macOS (dev). Callers fall back to
# Pillow's bundled bitmap font if none exist — text still renders, just plainer.
SERIF = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
]
SERIF_ITALIC = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
]


def first_existing(paths: list[str]) -> str | None:
    return next((p for p in paths if Path(p).exists()), None)
