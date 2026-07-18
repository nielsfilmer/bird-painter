from pathlib import Path

import pytest

from bird_painter.config import Config
from bird_painter.store import Store


@pytest.fixture
def archive_dir(tmp_path: Path) -> Path:
    # Always an absolute throwaway dir — Config's default archive_dir is
    # relative (cwd-dependent), so tests must never construct a default Config.
    return tmp_path / "archive"


@pytest.fixture
def store(archive_dir: Path) -> Store:
    return Store(archive_dir, ttl_seconds=100)


@pytest.fixture
def config(archive_dir: Path) -> Config:
    return Config(
        archive_dir=archive_dir,
        enable_listener=False,
        fal_key="",
    )


def add_painting(store: Store, species: str = "European Robin", **kwargs):
    defaults = dict(
        image_bytes=b"<svg/>",
        extension="svg",
        species_common=species,
        species_scientific="Erithacus rubecula",
        confidence=0.9,
        source="detection",
    )
    defaults.update(kwargs)
    return store.add(**defaults)
