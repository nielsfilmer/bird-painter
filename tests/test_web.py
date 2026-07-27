import dataclasses
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


def test_live_caps_at_wall_max_live(config):
    app = create_app(dataclasses.replace(config, wall_max_live=2))
    with TestClient(app) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        assert len(client.get("/api/live").json()["paintings"]) == 2


def test_wall_png_renders_at_configured_size(config):
    small = dataclasses.replace(config, wall_png_width=320, wall_png_height=240)
    app = create_app(small)
    with TestClient(app) as client:
        client.post("/dev/paint/robin")  # a (placeholder-SVG) plate to render
        response = client.get("/wall.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        import io

        from PIL import Image

        assert Image.open(io.BytesIO(response.content)).size == (320, 240)


def test_wall_png_renders_when_empty(config):
    app = create_app(
        dataclasses.replace(config, wall_png_width=200, wall_png_height=150)
    )
    with TestClient(app) as client:
        response = client.get("/wall.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"


def test_importing_web_has_no_side_effects(tmp_path: Path):
    """Regression for PR #28: `import bird_painter.web` must not create the
    default data/ archive (Config.archive_dir is a relative path)."""
    subprocess.run(
        [sys.executable, "-c", "import bird_painter.web"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert list(tmp_path.iterdir()) == []


def test_dev_paint_returns_502_when_the_brush_fails_with_a_key(monkeypatch, config):
    # FAL_KEY set, but the brush returns None (a soft paint failure): the
    # endpoint 502s and nothing is stored.
    from bird_painter import brush

    keyed = dataclasses.replace(config, fal_key="present")
    monkeypatch.setattr(brush, "paint", lambda *a, **k: None)
    app = create_app(keyed)
    with TestClient(app) as client:
        response = client.post("/dev/paint/robin")
        assert response.status_code == 502
        assert client.get("/api/live").json()["paintings"] == []


def test_dev_paint_uses_the_real_brush_when_a_key_is_set(monkeypatch, config):
    from bird_painter import brush

    keyed = dataclasses.replace(config, fal_key="present")
    monkeypatch.setattr(brush, "paint", lambda *a, **k: (b"JPGBYTES", "jpg"))
    app = create_app(keyed)
    with TestClient(app) as client:
        response = client.post("/dev/paint/song-thrush")
        assert response.status_code == 201
        assert response.json()["source"] == "dev"
        live = client.get("/api/live").json()["paintings"]
        assert [p["species_common"] for p in live] == ["Song Thrush"]


def test_layout_js_is_served_as_a_module(client):
    response = client.get("/layout.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "computeCollage" in response.text


def test_audio_endpoint_serves_clip_and_api_live_links_it(config):
    app = create_app(config)
    with TestClient(app) as client:
        client.post("/dev/paint/robin")  # placeholder path: no audio
        live = client.get("/api/live").json()["paintings"]
        assert live[0]["audio"] is None  # dev birds have no clip

        # A painting stored WITH audio surfaces it in /api/live and /audio.
        store = app.state.store
        painting = store.add(
            image_bytes=b"<svg/>",
            extension="svg",
            species_common="Wren",
            species_scientific="T. troglodytes",
            confidence=0.9,
            source="detection",
            audio_bytes=b"RIFFfake",
        )
        live = client.get("/api/live").json()["paintings"]
        wren = next(p for p in live if p["species_common"] == "Wren")
        assert wren["audio"] == painting.file.rsplit(".", 1)[0] + ".wav"
        response = client.get(f"/audio/{wren['audio']}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert client.get("/audio/nope.wav").status_code == 404
        assert client.get("/audio/..%2Fmeta.jsonl").status_code == 404


def test_api_archive_paginates_everything_newest_first(config):
    app = create_app(config)
    with TestClient(app) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        body = client.get("/api/archive?limit=2").json()
        assert body["total"] == 3
        assert len(body["paintings"]) == 2
        assert body["paintings"][0]["species_common"] == "Junco"  # newest first
        page2 = client.get("/api/archive?offset=2&limit=2").json()
        assert [p["species_common"] for p in page2["paintings"]] == ["Robin"]
        # every entry carries file/species/born_at/audio
        for p in body["paintings"]:
            assert set(p) == {"file", "species_common", "born_at", "audio"}


def test_api_archive_clamps_junk_paging(config):
    app = create_app(config)
    with TestClient(app) as client:
        client.post("/dev/paint/robin")
        assert client.get("/api/archive?offset=-5&limit=9999").json()["total"] == 1
        assert client.get("/api/archive?offset=999").json()["paintings"] == []


def test_archive_button_and_overlay_only_in_the_browser_page(client):
    # The wall page ships the archive UI…
    page = client.get("/").text
    assert "archive-button" in page
    # …and the e-paper /wall.png is a server-side raster that renders paintings
    # only — no DOM, so no button can appear (see render.py). Sanity: PNG magic.
    assert client.get("/wall.png").content[:8] == b"\x89PNG\r\n\x1a\n"
