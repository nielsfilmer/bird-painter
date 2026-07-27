"""Occasion hats: on special days, birds are painted wearing a tiny hat.

Two sources of days, deliberately split:
- PUBLIC holidays live in code below — nothing personal about Christmas.
- PERSONAL days (family birthdays, a one-off party) come ONLY from the
  environment (`BP_HAT_DAYS`, `BP_HAT_DATES` — see `.env.example`), so they
  never appear in this public repository. They get party hats.

The hat is a phrase woven into the FLUX prompt — this is the one place where
putting content *in* the painting is right: it's the painting's subject, not a
label. `schnell` follows it loosely; `flux/dev` obeys well.
"""

from __future__ import annotations

import datetime

# Hat phrases, written to survive the brush's no-text/white-background prompt.
PARTY_HAT = "wearing a tiny colourful pointed party hat"
_PUBLIC_HATS: dict[tuple[int, int], str] = {
    # (day, month) — public holidays only; personal days come from the env.
    (1, 1): "wearing a tiny sparkly party hat",  # New Year's Day
    (27, 4): "wearing a tiny bright orange party hat",  # King's Day (NL)
    (31, 10): "wearing a tiny black pointed witch hat",  # Halloween
    (5, 12): "wearing a tiny red bishop's mitre",  # Sinterklaas (NL)
    (25, 12): "wearing a tiny red Santa hat with a white pom-pom",  # Christmas
}


def hat_for(
    today: datetime.date,
    personal_days: tuple[tuple[int, int], ...] = (),
    one_time_dates: tuple[datetime.date, ...] = (),
) -> str | None:
    """The hat phrase for `today`, or None on an ordinary day.

    Precedence: a personal day (or one-time date) beats a public holiday —
    a birthday on Christmas gets the party hat.
    """
    if today in one_time_dates:
        return PARTY_HAT
    if (today.day, today.month) in personal_days:
        return PARTY_HAT
    return _PUBLIC_HATS.get((today.day, today.month))
