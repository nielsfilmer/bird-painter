import asyncio
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bird_painter.events import painted_event
from bird_painter.web import create_app
from tests.conftest import LOCAL, REMOTE


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        assert len(client.get("/api/live").json()["paintings"]) == 2


def test_wall_png_renders_at_configured_size(config):
    small = dataclasses.replace(config, wall_png_width=320, wall_png_height=240)
    app = create_app(small)
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
        response = client.post("/dev/paint/robin")
        assert response.status_code == 502
        assert client.get("/api/live").json()["paintings"] == []


def test_dev_paint_uses_the_real_brush_when_a_key_is_set(monkeypatch, config):
    from bird_painter import brush

    keyed = dataclasses.replace(config, fal_key="present")
    monkeypatch.setattr(brush, "paint", lambda *a, **k: (b"JPGBYTES", "jpg"))
    app = create_app(keyed)
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
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
    with TestClient(app, client=LOCAL) as client:
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


def test_ws_streams_a_painted_bird_with_name_time_image_and_sound(config):
    app = create_app(config)
    with (
        TestClient(app, client=LOCAL) as client,
        client.websocket_connect("/ws/detections") as ws,
    ):
        assert ws.receive_json()["type"] == "hello"
        # A detection-style painting (with an archived clip) reaches the socket.
        store = app.state.store
        painting = store.add(
            image_bytes=b"<svg/>",
            extension="svg",
            species_common="Wren",
            species_scientific="Troglodytes troglodytes",
            confidence=0.81,
            source="detection",
            audio_bytes=b"RIFF....WAVE",
        )
        app.state.events.publish(
            painted_event(painting, store.audio_file_for(painting.file))
        )

        event = ws.receive_json()
        assert event["type"] == "painted"
        assert event["species_common"] == "Wren"           # the name
        assert event["time"] and event["at"] == painting.born_at  # the time
        # urls are absolute for whoever connected, and both actually resolve
        assert event["image"]["url"].startswith("http://testserver/images/")
        assert client.get(event["image"]["url"]).status_code == 200
        sound = client.get(event["audio"]["download_url"])
        assert sound.status_code == 200
        assert sound.content == b"RIFF....WAVE"
        assert "attachment" in sound.headers["content-disposition"]


def test_ws_replays_recent_events_to_a_late_client(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        client.post("/dev/paint/robin")  # happened before anyone connected
        with client.websocket_connect("/ws/detections") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            assert [e["species_common"] for e in hello["recent"]] == ["Robin"]
            assert hello["recent"][0]["image"]["url"].startswith("http://testserver/")


def test_ws_broadcasts_to_every_connected_client(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        with (
            client.websocket_connect("/ws/detections") as first,
            client.websocket_connect("/ws/detections") as second,
        ):
            assert first.receive_json()["type"] == "hello"
            assert second.receive_json()["type"] == "hello"
            client.post("/dev/paint/junco")
            for ws in (first, second):
                event = ws.receive_json()
                assert (event["type"], event["species_common"]) == ("painted", "Junco")


def test_audio_streams_inline_unless_download_is_asked_for(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        store = app.state.store
        painting = store.add(
            image_bytes=b"<svg/>",
            extension="svg",
            species_common="Wren",
            species_scientific="Troglodytes troglodytes",
            confidence=0.8,
            source="detection",
            audio_bytes=b"RIFF",
        )
        clip = store.audio_file_for(painting.file)
        # The wall's click-to-replay must keep streaming, not download.
        assert "content-disposition" not in client.get(f"/audio/{clip}").headers


def test_ws_reaps_the_subscriber_when_a_client_drops_without_closing(config):
    """The failure mode of a 24/7 wall: a client that vanishes without a close
    frame (lid shut, wifi gone, process killed). The server sees that ONLY as a
    `websocket.disconnect` delivered to receive() — writes to the dead
    transport keep succeeding — so an endpoint that never receives leaks its
    subscriber, its queue, and its coroutine forever.

    Driven at the ASGI layer on purpose: TestClient always closes cleanly, so
    it cannot express this drop.
    """
    app = create_app(config)
    hub = app.state.events

    async def scenario():
        incoming: asyncio.Queue = asyncio.Queue()
        sent: list[dict] = []

        async def receive():
            return await incoming.get()

        async def send(message):
            sent.append(message)  # a lost transport swallows sends silently

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": "/ws/detections",
            "raw_path": b"/ws/detections",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"wall.local:8537")],
            "client": ("10.0.0.9", 51000),
            "server": ("10.0.0.2", 8537),
            "subprotocols": [],
            "state": {},
        }
        await incoming.put({"type": "websocket.connect"})
        served = asyncio.create_task(app(scope, receive, send))

        for _ in range(200):  # let the endpoint accept and greet
            await asyncio.sleep(0)
            if any(m["type"] == "websocket.send" for m in sent):
                break
        assert hub.subscriber_count == 1

        # The drop: no close frame from the client, just the transport dying.
        await incoming.put({"type": "websocket.disconnect", "code": 1006})
        await asyncio.wait_for(served, timeout=5)
        assert hub.subscriber_count == 0

    asyncio.run(scenario())


def test_ws_does_not_replay_an_event_twice_to_a_fresh_client(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        client.post("/dev/paint/robin")
        with client.websocket_connect("/ws/detections") as ws:
            hello = ws.receive_json()
            assert [e["species_common"] for e in hello["recent"]] == ["Robin"]
            # A second bird proves the stream is still live AND that the
            # replayed one didn't arrive again behind it.
            client.post("/dev/paint/wren")
            event = ws.receive_json()
            assert (event["type"], event["species_common"]) == ("painted", "Wren")


def test_ws_survives_a_clip_lookup_failure(config, monkeypatch):
    """The painting is already archived when we announce it — a filesystem
    hiccup reading the clip must not 500 the paint that succeeded."""
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        monkeypatch.setattr(
            app.state.store,
            "audio_file_for",
            lambda file: (_ for _ in ()).throw(OSError("disk gone")),
        )
        assert client.post("/dev/paint/robin").status_code == 201
        assert [p.species_common for p in app.state.store.live()] == ["Robin"]


def test_download_flag_is_read_leniently(config):
    """It's a flag on a shared link, so a typo hands over the sound rather
    than 422-ing; only an explicit no keeps it streaming."""
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        store = app.state.store
        painting = store.add(
            image_bytes=b"<svg/>",
            extension="svg",
            species_common="Wren",
            species_scientific="Troglodytes troglodytes",
            confidence=0.8,
            source="detection",
            audio_bytes=b"RIFF",
        )
        clip = store.audio_file_for(painting.file)
        for value in ("1", "true", "yes", "banana", ""):
            headers = client.get(f"/audio/{clip}?download={value}").headers
            expected = value != ""
            assert ("content-disposition" in headers) is expected, value
        for value in ("0", "false", "no", "off"):
            assert "content-disposition" not in client.get(
                f"/audio/{clip}?download={value}"
            ).headers, value


def test_replay_dedupe_matches_by_identity_and_ends_at_the_first_fresh_event():
    """The round-2 review caught the integration test never reaching this
    branch, so the rule is pinned directly: the same object is a duplicate, an
    equal-but-distinct event is not, and the overlap ends at the first fresh
    event."""
    from bird_painter.web import _is_replay_duplicate

    replayed_event = {"type": "painted", "species_common": "Robin"}
    replayed = [replayed_event]

    assert _is_replay_duplicate(replayed_event, replayed) is True
    assert replayed == [replayed_event]  # still guarding the rest of the prefix

    twin = {"type": "painted", "species_common": "Robin"}  # equal, not the same
    assert _is_replay_duplicate(twin, replayed) is False
    assert replayed == []  # a fresh event ends the overlap

    assert _is_replay_duplicate(replayed_event, replayed) is False  # nothing left


def test_dev_paint_is_refused_from_off_the_machine(config):
    """/dev/paint bypasses the hourly cap and, with a key set, spends real
    money per call — so it must not be one curl away for anyone on the LAN
    (issue #66). A 404, not a 403: a 403 would advertise that it's there."""
    app = create_app(config)
    with TestClient(app, client=REMOTE) as client:
        assert client.post("/dev/paint/robin").status_code == 404
        assert client.get("/api/live").json()["paintings"] == []  # nothing painted


def test_dev_paint_refuses_a_peer_it_cannot_place(config):
    """Fails closed: an unparseable or absent peer counts as remote."""
    app = create_app(config)
    with TestClient(app, client=("not-an-address", 1)) as client:
        assert client.post("/dev/paint/robin").status_code == 404


def test_dev_paint_ignores_a_forwarded_for_header(config):
    """The decision is made on the peer address alone — a header can be typed
    by anyone, and this wall has no authentication to fall back on."""
    app = create_app(config)
    with TestClient(app, client=REMOTE) as client:
        response = client.post(
            "/dev/paint/robin", headers={"X-Forwarded-For": "127.0.0.1"}
        )
        assert response.status_code == 404


def test_dev_paint_still_works_from_the_wall_itself(config):
    app = create_app(config)
    for peer in (("127.0.0.1", 5000), ("::1", 5000)):
        with TestClient(app, client=peer) as client:
            assert client.post("/dev/paint/robin").status_code == 201


def test_everything_else_stays_reachable_from_the_network(config):
    """Only /dev/paint is local — the wall, the stream and their assets are
    what the phone and the e-paper frame come for."""
    app = create_app(config)
    with TestClient(app, client=LOCAL) as local:
        painted = local.post("/dev/paint/wren").json()["painted"]
        clip = app.state.store.audio_file_for(
            app.state.store.add(
                image_bytes=b"<svg/>",
                extension="svg",
                species_common="Robin",
                species_scientific="Erithacus rubecula",
                confidence=0.9,
                source="detection",
                audio_bytes=b"RIFF",
            ).file
        )
    with TestClient(app, client=REMOTE) as remote:
        for path in ("/", "/api", "/api/docs", "/api/live", "/api/archive",
                     "/wall.png", "/docs", "/openapi.json", "/layout.js",
                     f"/images/{painted}", f"/audio/{clip}",
                     f"/audio/{clip}?download=1"):
            assert remote.get(path).status_code == 200, path
        with remote.websocket_connect("/ws/detections") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_the_server_does_not_trust_proxy_headers(monkeypatch):
    """The loopback check is only as good as the peer address, and uvicorn
    rewrites that from X-Forwarded-For unless proxy headers are off —
    FORWARDED_ALLOW_IPS=* in the environment would otherwise hand /dev/paint
    to any LAN caller willing to type a header (found in review of #92)."""
    from bird_painter import __main__

    captured = {}
    monkeypatch.setattr(
        __main__.uvicorn, "run", lambda *args, **kwargs: captured.update(kwargs)
    )
    monkeypatch.setattr(sys, "argv", ["bird_painter", "--no-prompt"])
    __main__.main()
    assert captured["proxy_headers"] is False


def test_loopback_check_understands_the_addresses_a_listener_reports():
    """A dual-stack listener reports a v4 client as ::ffff:127.0.0.1, which
    Python only calls loopback from 3.13 — this project targets 3.11."""
    from bird_painter.web import _is_loopback

    for local in ("127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"):
        assert _is_loopback((local, 5000)) is True, local
    for remote in ("192.168.1.50", "10.0.0.2", "::ffff:192.168.1.50", "8.8.8.8"):
        assert _is_loopback((remote, 5000)) is False, remote
    assert _is_loopback(None) is False
    assert _is_loopback(("testclient", 5000)) is False
