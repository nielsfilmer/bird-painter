"""Painting store: permanent disk archive + ephemeral live view.

Every painting is archived forever (image file + a metadata line in
meta.jsonl). The wall only shows paintings younger than the TTL; expiry hides,
never deletes. The per-species last_painted_at map — the repaint-cooldown key,
independent of wall presence (PLAN.md trigger rule) — is derived from the same
metadata, so it survives restarts.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Painting:
    file: str  # filename within the archive dir
    species_common: str
    species_scientific: str
    confidence: float
    born_at: float  # unix seconds
    source: str  # "detection" | "dev"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "bird"


class Store:
    def __init__(self, archive_dir: Path, ttl_seconds: int):
        self.archive_dir = archive_dir
        self.ttl_seconds = ttl_seconds
        self.meta_path = archive_dir / "meta.jsonl"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._paintings: list[Painting] = self._load()

    def _load(self) -> list[Painting]:
        if not self.meta_path.exists():
            return []
        paintings = []
        for line in self.meta_path.read_text().splitlines():
            if line.strip():
                paintings.append(Painting(**json.loads(line)))
        return paintings

    def add(
        self,
        *,
        image_bytes: bytes,
        extension: str,
        species_common: str,
        species_scientific: str,
        confidence: float,
        source: str,
    ) -> Painting:
        born_at = time.time()
        filename = f"{int(born_at)}_{slugify(species_common)}.{extension}"
        (self.archive_dir / filename).write_bytes(image_bytes)
        painting = Painting(
            file=filename,
            species_common=species_common,
            species_scientific=species_scientific,
            confidence=confidence,
            born_at=born_at,
            source=source,
        )
        with self.meta_path.open("a") as f:
            f.write(json.dumps(asdict(painting)) + "\n")
        self._paintings.append(painting)
        return painting

    def live(self, now: float | None = None) -> list[Painting]:
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

    def image_path(self, filename: str) -> Path | None:
        """Resolve an archived image safely (no path traversal)."""
        if filename != Path(filename).name:
            return None
        path = self.archive_dir / filename
        return path if path.is_file() else None
