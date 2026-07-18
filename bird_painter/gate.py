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
        return True

    def record(self, now: float | None = None) -> None:
        """Mark that a paint succeeded — consumes one hourly-cap slot. The
        per-species cooldown is carried by the store (the archived painting's
        born_at), so only the cap is tracked here."""
        now = time.time() if now is None else now
        self._paint_times.append(now)
