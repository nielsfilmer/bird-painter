import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bird_painter.web import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as client:
        yield client


def test_wall_page_serves(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>bird-painter</title>" in response.text


def test_live_starts_empty_with_ttl(client, config):
    body = client.get("/api/live").json()
    assert body == {
        "ttl_seconds": config.paint_ttl_seconds,
        "paintings": [],
    }


def test_dev_paint_falls_back_to_placeholder_without_key(client):
    response = client.post("/dev/paint/song-thrush")
    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "dev-placeholder"

    live = client.get("/api/live").json()["paintings"]
    assert [p["species_common"] for p in live] == ["Song Thrush"]

    image = client.get(f"/images/{body['painted']}")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/svg")


def test_dev_paint_rejects_wrong_method(client):
    assert client.get("/dev/paint/robin").status_code == 405


def test_images_refuses_missing_traversal_and_non_images(client):
    client.post("/dev/paint/robin")  # ensure meta.jsonl exists
    assert client.get("/images/nope.svg").status_code == 404
    assert client.get("/images/..%2Fsecrets.svg").status_code == 404
    assert client.get("/images/meta.jsonl").status_code == 404


def test_live_caps_at_wall_max_live(archive_dir):
    from bird_painter.config import Config

    config = Config(
        archive_dir=archive_dir, enable_listener=False, fal_key="", wall_max_live=2
    )
    app = create_app(config)
    with TestClient(app) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        assert len(client.get("/api/live").json()["paintings"]) == 2


def test_importing_web_has_no_side_effects(tmp_path: Path):
    """Regression for PR #28: `import bird_painter.web` must not create the
    default data/ archive (Config.archive_dir is a relative path)."""
    result = subprocess.run(
        [sys.executable, "-c", "import bird_painter.web"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert list(tmp_path.iterdir()) == []
