"""The painting styles a unit can choose (owner, 2026-09-04: "different
painting styles you can select in the settings … Japanese Watercolor …
five or six … also a weird one, like abstract or cubistic").

A style is two phrases spliced into the brush's prompt: the LOOK (what
kind of picture this is) and the PALETTE (its colours and handling). The
rest of the prompt — one bird, full side view, cleanly isolated on flat
white, no text, no paper — is the house rule and stays the same for every
style, because the wall's multiply-blend and the plate check depend on it.
The vintage naturalist look the wall has always had is the default and
first in the list; the order here is the order the settings screen steps
through.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    key: str
    name: str  # as the settings screen shows it
    look: str  # "A single {name} bird, <look>, the whole bird perched …"
    palette: str  # "…, <palette>, cleanly isolated …"


STYLES: tuple[Style, ...] = (
    Style(
        "naturalist",
        "vintage naturalist",
        "hand-painted naturalist watercolor",
        "soft muted natural colors, fine feather detail",
    ),
    Style(
        "sumi",
        "Japanese watercolour",
        "traditional Japanese sumi-e ink and watercolour painting with loose, "
        "confident brushstrokes and soft wet washes",
        "a restrained palette of ink black and warm grey with a touch of "
        "vermilion and indigo, visible brush texture",
    ),
    Style(
        "dutch",
        "Dutch Golden Age",
        "seventeenth-century Dutch Golden Age oil painting, glazed and "
        "luminous, with soft chiaroscuro",
        "deep warm earth tones of umber, ochre and ivory, fine glazed detail",
    ),
    Style(
        "nouveau",
        "Art Nouveau",
        "Art Nouveau illustration in the manner of a 1900s decorative poster, "
        "with flowing elegant outlines and flat stylised colour",
        "muted jade, old gold, cream and terracotta with bold clean contour lines",
    ),
    Style(
        "sketch",
        "field sketch",
        "a naturalist's field-notebook study in pencil with a light watercolour "
        "wash, quick and precise, unfinished at the edges",
        "graphite lines with a few translucent washes of the bird's true colours",
    ),
    Style(
        "linocut",
        "folk linocut",
        "a hand-pulled folk-art linocut print with bold carved marks and "
        "slightly uneven ink",
        "two or three flat inks — black, rust red and olive — with the white "
        "showing through",
    ),
    Style(
        "cubist",
        "cubist",
        "an early-twentieth-century cubist painting, the bird broken into "
        "overlapping geometric facets and planes seen from several angles at once",
        "muted ochre, slate grey, dusty blue and off-white, angular, in "
        "textured oil paint",
    ),
)

DEFAULT_STYLE = STYLES[0].key
_BY_KEY = {s.key: s for s in STYLES}


def style_for(key: str | None) -> Style:
    """The style with this key, or the default — a unit.conf line from an
    older install, or a typo in .env, paints the house look rather than
    nothing."""
    return _BY_KEY.get((key or "").strip().lower(), _BY_KEY[DEFAULT_STYLE])


def is_style(key: str) -> bool:
    return key in _BY_KEY


def style_choices() -> list[dict[str, str]]:
    """What the settings screen steps through: key + name, in order."""
    return [{"key": s.key, "name": s.name} for s in STYLES]
