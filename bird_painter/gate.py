"""The trigger gate: decides whether a detected species gets painted.

PLAN.md's precise rule — a detection paints the species iff
  (a) it's been at least TTL since that species was last painted
      (cooldown keys on the store's per-species last_painted_at, NOT on wall
      presence — so wall overflow eviction can't shorten the cooldown), and
  (b) the rolling per-hour paint count is under the cap.
TTL doubles as the cooldown (one knob).

Only a SUCCESSFUL paint consumes a cap slot / marks the species painted, so
`allows()` is the check and `record()` is called by the caller after the paint
lands — a failed paint (fal outage) leaves both untouched and the species free
to retry (PLAN.md failure policy).
"""

from __future__ import annotations

import time
from collections import deque

HOUR_SECONDS = 3600


class TriggerGate:
    def __init__(self, store, ttl_seconds: int, max_paints_per_hour: int):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.max_paints_per_hour = max_paints_per_hour
        self._paint_times: deque[float] = deque()
        # Species the brush couldn't paint acceptably, and when we gave up.
        # Kept here rather than in the store because nothing was archived —
        # there is no painting to carry a timestamp.
        self._gave_up_at: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        cutoff = now - HOUR_SECONDS
        while self._paint_times and self._paint_times[0] < cutoff:
            self._paint_times.popleft()

    def allows(self, species_common: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self._prune(now)
        if len(self._paint_times) >= self.max_paints_per_hour:
            return False
        last = self.store.last_painted_at(species_common)
        if last is not None and now - last < self.ttl_seconds:
            return False
        # A species the model kept painting wrongly waits out the same
        # cooldown before costing another generation. Without this, giving up
        # freed the species to try again on its very next detection — which,
        # for a persistent singer, is every 15 seconds.
        gave_up = self._gave_up_at.get(species_common)
        if gave_up is not None and now - gave_up < self.ttl_seconds:
            return False
        return True

    def record_failure(self, species_common: str, now: float | None = None) -> None:
        """Mark that this species couldn't be painted acceptably.

        Deliberately NOT a cap slot. Charging the hourly cap bounds the spend
        but spends the wrong budget: measured over an hour, one bad species
        exhausted the cap in ~5 minutes and a good bird singing every 5 minutes
        got 1 painting instead of 12. The per-species cooldown bounds the same
        spend without letting one species crowd out the others."""
        self._gave_up_at[species_common] = time.time() if now is None else now

    def record(self, now: float | None = None) -> None:
        """Mark that a paint succeeded — consumes one hourly-cap slot. The
        per-species cooldown is carried by the store (the archived painting's
        born_at), so only the cap is tracked here."""
        now = time.time() if now is None else now
        self._paint_times.append(now)
