"""Painting store: rolling disk archive + ephemeral live view.

Every painting is archived (image file + optional detection clip + a metadata
line in meta.jsonl) and kept for a rolling month (BP_RETENTION_DAYS; the purge
below). The wall only shows paintings younger than the TTL; TTL expiry hides
without deleting — deletion is the retention purge's job. The per-species
last_painted_at map — the repaint-cooldown key, independent of wall presence
(PLAN.md trigger rule) — is derived from the same metadata, so it survives
restarts.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)

# Image types the /images endpoint may serve; keeps meta.jsonl (and anything
# else that lands in the archive dir) unreachable from the web.
SERVABLE_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class Painting:
    file: str  # filename within the archive dir
    species_common: str
    species_scientific: str
    confidence: float
    born_at: float  # unix seconds
    source: str  # "detection" | "dev" | "dev-placeholder"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "bird"


_PAINTING_FIELDS = {f.name for f in fields(Painting)}

# Artifacts are kept one month, then purged (owner decision 2026-07-27) —
# the archive is a rolling month, not a forever collection.
DEFAULT_RETENTION_SECONDS = 31 * 24 * 60 * 60
# How often the opportunistic purge (from live()) may actually run.
PURGE_INTERVAL_SECONDS = 3600


def _painting_from_record(record: object) -> Painting | None:
    """Build a Painting from a meta.jsonl record, tolerating schema drift:
    keys added by a newer version are ignored; a record missing a field the
    current Painting needs (or not a JSON object at all) is skipped with a
    warning rather than crashing boot on a TypeError/AttributeError."""
    if not isinstance(record, dict):
        logger.warning("store: skipping meta record that isn't a JSON object")
        return None
    known = {k: v for k, v in record.items() if k in _PAINTING_FIELDS}
    missing = _PAINTING_FIELDS - known.keys()
    if missing:
        logger.warning("store: skipping meta record missing %s", sorted(missing))
        return None
    return Painting(**known)


class Store:
    """Single-process, single-worker only. The in-memory painting list is the
    source of the live view, so running uvicorn with --workers N would give
    each worker its own diverging store (and a per-worker meta.jsonl race) —
    correctness depends on `--workers 1`, which is the app's default.

    Within the one process there ARE two writer threads (the mic listener and
    any /dev/paint request), so `add` is guarded by a lock: the file append
    and the list append happen atomically, so concurrent adds can't interleave
    partial lines in meta.jsonl. Reads (live/last_painted_at) iterate the list
    under the GIL, which is safe for a list (no 'changed size during
    iteration')."""

    def __init__(
        self,
        archive_dir: Path,
        ttl_seconds: int,
        retention_seconds: int | None = DEFAULT_RETENTION_SECONDS,
    ):
        self.archive_dir = archive_dir
        self.ttl_seconds = ttl_seconds
        # Artifacts (painting + clip + meta record) older than this are purged;
        # None disables retention (tests / archaeology).
        self.retention_seconds = retention_seconds
        self.meta_path = archive_dir / "meta.jsonl"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._paintings: list[Painting] = self._load()
        self._last_purge = 0.0
        self.purge_expired()  # boot purge — a wall off for months catches up

    def _load(self) -> list[Painting]:
        if not self.meta_path.exists():
            return []
        paintings = []
        for line in self.meta_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("store: skipping unparseable meta line")
                continue
            painting = _painting_from_record(record)
            if painting is not None:
                paintings.append(painting)
        return paintings

    def purge_expired(self, now: float | None = None) -> int:
        """Delete paintings (file + clip + meta record) older than the
        retention window. Returns how many were purged. Compacts meta.jsonl
        atomically so a reboot can't resurrect purged records."""
        if self.retention_seconds is None:
            return 0
        now = time.time() if now is None else now
        cutoff = now - self.retention_seconds
        with self._lock:
            self._last_purge = now
            expired = [p for p in self._paintings if p.born_at < cutoff]
            if not expired:
                return 0
            for p in expired:
                for name in (p.file, f"{Path(p.file).stem}.wav"):
                    # Same guard as the serving paths: a corrupt/crafted meta
                    # record must never delete outside the archive dir.
                    if name != Path(name).name:
                        logger.warning("purge: refusing suspicious name %r", name)
                        continue
                    try:
                        (self.archive_dir / name).unlink(missing_ok=True)
                    except OSError:
                        logger.warning("purge: could not delete %s", name)
            self._paintings = [p for p in self._paintings if p.born_at >= cutoff]
            tmp = self.meta_path.with_suffix(".jsonl.tmp")
            with tmp.open("w") as f:
                for p in self._paintings:
                    f.write(json.dumps(asdict(p)) + "\n")
            tmp.replace(self.meta_path)
        logger.info("purge: removed %d painting(s) past retention", len(expired))
        return len(expired)

    def maybe_purge(self, now: float | None = None) -> None:
        """Opportunistic purge, throttled — called from the read path so even a
        wall that never paints (quiet winter) still ages out old artifacts."""
        now = time.time() if now is None else now
        if now - self._last_purge >= PURGE_INTERVAL_SECONDS:
            self.purge_expired(now)

    def add(
        self,
        *,
        image_bytes: bytes,
        extension: str,
        species_common: str,
        species_scientific: str,
        confidence: float,
        source: str,
        audio_bytes: bytes | None = None,
    ) -> Painting:
        born_at = time.time()
        # uuid suffix: same-species-same-second paints must never overwrite an
        # archived file (the archive is append-only until the retention purge).
        filename = (
            f"{int(born_at)}_{slugify(species_common)}_{uuid.uuid4().hex[:8]}.{extension}"
        )
        (self.archive_dir / filename).write_bytes(image_bytes)
        if audio_bytes is not None:
            # The detection clip lives beside its painting, same stem: a click
            # on the wall replays the sound that produced the bird.
            (self.archive_dir / f"{Path(filename).stem}.wav").write_bytes(audio_bytes)
        painting = Painting(
            file=filename,
            species_common=species_common,
            species_scientific=species_scientific,
            confidence=confidence,
            born_at=born_at,
            source=source,
        )
        # Serialize the two writers (mic thread + /dev/paint) so their meta
        # lines and list appends never interleave.
        with self._lock:
            with self.meta_path.open("a") as f:
                f.write(json.dumps(asdict(painting)) + "\n")
            self._paintings.append(painting)
        return painting

    def all_paintings(self) -> list[Painting]:
        """Everything retention has kept, newest first — the archive view."""
        self.maybe_purge()
        return sorted(self._paintings, key=lambda p: -p.born_at)

    def live(self, now: float | None = None) -> list[Painting]:
        self.maybe_purge(now)
        """Non-expired paintings, newest first."""
        now = time.time() if now is None else now
        cutoff = now - self.ttl_seconds
        fresh = [p for p in self._paintings if p.born_at >= cutoff]
        return sorted(fresh, key=lambda p: p.born_at, reverse=True)

    def last_painted_at(self, species_common: str) -> float | None:
        """Cooldown key for the trigger gate: when this species was last
        painted, regardless of whether that painting is still on the wall."""
        times = [
            p.born_at
            for p in self._paintings
            if p.species_common == species_common
        ]
        return max(times) if times else None

    def audio_file_for(self, painting_file: str) -> str | None:
        """The painting's clip filename (same stem, .wav) if one exists —
        old and dev-painted birds have none."""
        name = f"{Path(painting_file).stem}.wav"
        return name if (self.archive_dir / name).is_file() else None

    def audio_path(self, filename: str) -> Path | None:
        """Resolve an archived clip safely (no traversal, .wav only)."""
        if filename != Path(filename).name or Path(filename).suffix.lower() != ".wav":
            return None
        path = self.archive_dir / filename
        return path if path.is_file() else None

    def image_path(self, filename: str) -> Path | None:
        """Resolve an archived image safely (no traversal, images only)."""
        if filename != Path(filename).name:
            return None
        if Path(filename).suffix.lower() not in SERVABLE_EXTENSIONS:
            return None
        path = self.archive_dir / filename
        return path if path.is_file() else None
