import os
from pathlib import Path

import pytest

from bird_painter.config import Config
from bird_painter.store import Store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Config's field defaults read the environment (and load_dotenv runs at
    import), so the dev's real .env — including a live FAL_KEY — would leak
    into any test that doesn't pin every knob. Strip them all, every test."""
    for name in list(os.environ):
        if name.startswith("BP_") or name == "FAL_KEY":
            monkeypatch.delenv(name, raising=False)


# The wall's own machine. /dev/paint is loopback-only, and TestClient's
# default peer ("testclient") is not an address at all — so every client in
# the suite has to say where it's calling from.
LOCAL = ("127.0.0.1", 51000)
REMOTE = ("192.168.1.50", 51000)


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
