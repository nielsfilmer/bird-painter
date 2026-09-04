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
        "Art Nouveau illustration with flowing elegant outlines, flat "
        "stylised colour and ornamental curves in the manner of 1900",
        "muted jade, old gold, cream and terracotta with bold clean contour lines",
    ),
    Style(
        "sketch",
        "field sketch",
        "a naturalist's pencil study with a light watercolour wash, quick and "
        "precise, the lines fading out towards the edges of the bird",
        "graphite lines with a few translucent washes of the bird's true colours",
    ),
    Style(
        "linocut",
        "folk linocut",
        "a bold folk-art linocut with carved, slightly uneven marks and "
        "flat areas of ink",
        "two or three flat inks — black, rust red and olive — with the white "
        "showing through",
    ),
    # Watch item: the canonical cubist corpus is papier collé — pasted
    # lettering, painted borders, a faceted ground — the very things the
    # prompt's tail suppresses. Two verification plates came back clean on
    # white; if a cubist plate ever carries text or a full-bleed ground, the
    # plate check discards it and the retry pays a fraction of a cent.
    # ("the whole bird" is repeated by the template right after the look —
    # left as is: rewording would invalidate the two paid verifications.)
    Style(
        "cubist",
        "cubist",
        "a cubist painting in the manner of Picasso and Braque, the whole bird "
        "fractured into overlapping angular geometric planes and facets seen from "
        "several viewpoints at once, nothing realistic",
        "flat angular planes of muted ochre, slate grey and dusty blue with hard "
        "edges, in textured oil paint, cubism throughout",
    ),
)

DEFAULT_STYLE = STYLES[0].key
_BY_KEY = {s.key: s for s in STYLES}


def style_for(key: str | None) -> Style:
    """The style with this key (case- and space-insensitive), or the DEFAULT
    — a unit.conf line from an older install paints the house look rather
    than nothing. Lenient on purpose; `is_style` is the strict twin that
    Config uses to refuse a misspelt BP_STYLE at startup, where a person
    can still read the message. Note the fallback is the default, not
    BP_STYLE: a stale unit.conf value is treated as "no choice made"."""
    return _BY_KEY.get((key or "").strip().lower(), _BY_KEY[DEFAULT_STYLE])


def is_style(key: str) -> bool:
    return key in _BY_KEY


def style_choices() -> list[dict[str, str]]:
    """What the settings screen steps through: key + name, in order."""
    return [{"key": s.key, "name": s.name} for s in STYLES]
