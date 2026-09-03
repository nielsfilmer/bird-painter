import asyncio
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


def test_wall_page_and_its_module_revalidate(client):
    """#151: the kiosk's Chromium served a cached layout.js against a fresh
    index.html after a deploy and the import died silently. Both — and the
    docs page — say revalidate; the API's JSON was never cached."""
    for path in ("/", "/layout.js", "/api/docs"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    assert client.get("/layout.js").headers.get("etag")


def test_live_reports_night_from_the_watch(config):
    """#122: the page dims itself on `night`; the flag is the watch's state,
    which the schedule sets on its own thread. Off (the fixture) it is False
    whatever the clock says; flipped, /api/live says so at once."""
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        assert client.get("/api/live").json()["night"] is False
        app.state.night.is_night = True
        assert client.get("/api/live").json()["night"] is True


def test_live_starts_empty_with_ttl(client, config):
    body = client.get("/api/live").json()
    assert body == {
        "ttl_seconds": config.paint_ttl_seconds,
        "night": False,
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


def test_wall_png_serves_every_style_and_layer(config):
    """The e-paper frame asks for `style=panel` and fetches the picture and the
    lettering separately. The text layer must come back as an 8-bit grayscale
    MASK — the frame tells a real mask from an older recorder's ordinary wall
    by exactly that, so if this ever returned RGB the panel would go black."""
    import io

    from PIL import Image

    small = dataclasses.replace(config, wall_png_width=320, wall_png_height=240)
    with TestClient(create_app(small), client=LOCAL) as client:
        client.post("/dev/paint/robin")
        for style in ("wall", "panel"):
            for layer in ("all", "picture", "text"):
                response = client.get(f"/wall.png?style={style}&layer={layer}")
                assert response.status_code == 200, (style, layer)
                image = Image.open(io.BytesIO(response.content))
                assert image.size == (320, 240)
                expected = "L" if layer == "text" else "RGB"
                assert image.mode == expected, (style, layer, image.mode)


def test_wall_png_refuses_an_unknown_style_or_layer(config):
    with TestClient(create_app(config), client=LOCAL) as client:
        for query in ("style=bogus", "layer=bogus", "layer=TEXT", "style="):
            response = client.get(f"/wall.png?{query}")
            assert response.status_code == 422, query


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
    (issue #66)."""
    app = create_app(config)
    with TestClient(app, client=REMOTE) as client:
        assert client.post("/dev/paint/robin").status_code == 404
        assert client.get("/api/live").json()["paintings"] == []  # nothing painted


def test_dev_routes_do_not_leak_their_shape_to_the_network(config):
    """QA on #92: checking inside the handler still answered 405 to a GET and
    307 to a trailing slash — answers only a real route gives, so the endpoint
    remained an existence oracle. Refused before routing, every shape looks
    like every other missing path."""
    app = create_app(config)
    with TestClient(app, client=REMOTE) as remote:
        probes = [
            remote.get("/dev/paint/robin"),  # was 405
            remote.post("/dev/paint/robin/", follow_redirects=False),  # was 307
            remote.request("PUT", "/dev/paint/robin"),
            remote.get("/dev/nope/robin"),  # a path that never existed
        ]
        assert [p.status_code for p in probes] == [404, 404, 404, 404]

    with TestClient(app, client=LOCAL) as local:
        # …while the machine itself still gets the real answers.
        assert local.get("/dev/paint/robin").status_code == 405
        assert local.post("/dev/paint/robin").status_code == 201


def test_local_only_guard_sees_through_a_root_path_mount(config):
    """#95: the guard matched the RAW path, so a wall served under
    `--root-path /wall` let `/wall/dev/paint/x` past it — the handler still
    refused, but with the 405/307 answers the guard exists to hide."""
    app = create_app(config)
    with TestClient(app, client=REMOTE, root_path="/wall") as remote:
        assert remote.get("/wall/dev/paint/robin").status_code == 404  # not 405
        slash = remote.post("/wall/dev/paint/robin/", follow_redirects=False)
        assert slash.status_code == 404  # not 307
    with TestClient(app, client=LOCAL, root_path="/wall") as local:
        assert local.get("/wall/dev/paint/robin").status_code == 405
        assert local.post("/wall/dev/paint/robin").status_code == 201


def test_local_only_guard_strips_root_path_only_on_a_boundary(config):
    """Round-1 review of #148: Starlette strips `root_path` only where a `/`
    follows it — a mount at `/d` does not own `/dev/...`. A guard that
    stripped any string prefix saw `ev/paint/robin`, missed, and the 405
    was back."""
    app = create_app(config)
    with TestClient(app, client=REMOTE, root_path="/d") as remote:
        assert remote.get("/dev/paint/robin").status_code == 404  # not 405


def test_local_only_guard_reserves_unit_on_a_boundary(config):
    """`/unit` is the table model's settings API (#123), refused off-machine
    before it exists — but only `/unit` and `/unit/...`, not `/unittest`."""
    app = create_app(config)

    @app.get("/unit")
    def unit_root():
        return {"ok": True}

    @app.get("/unit/state")
    def unit_state():
        return {"ok": True}

    @app.get("/unittest")
    def unittest_():
        return {"ok": True}

    with TestClient(app, client=REMOTE) as remote:
        assert remote.get("/unit/state").status_code == 404
        assert remote.get("/unit").status_code == 404
        assert remote.get("/unittest").status_code == 200
    with TestClient(app, client=LOCAL) as local:
        assert local.get("/unit").status_code == 200
        assert local.get("/unit/state").status_code == 200
        assert local.get("/unittest").status_code == 200


def test_local_only_guard_takes_its_prefixes_as_an_argument():
    from fastapi import FastAPI

    from bird_painter.web import LocalOnly

    app = FastAPI()
    app.add_middleware(LocalOnly, prefixes=("/secret",))

    @app.get("/secret/x")
    def secret():
        return {"ok": True}

    @app.get("/dev/open")
    def open_():
        return {"ok": True}

    with TestClient(app, client=REMOTE) as remote:
        assert remote.get("/secret/x").status_code == 404
        assert remote.get("/dev/open").status_code == 200  # not in THIS guard's list


def test_local_only_guard_covers_websockets_too(config):
    """#95: an `@app.middleware("http")` never sees a websocket scope. A
    throwaway /dev socket stands in for any future one."""
    app = create_app(config)

    @app.websocket("/dev/echo")
    async def dev_echo(ws: WebSocket):
        await ws.accept()
        await ws.send_text("hello")
        await ws.close()

    with TestClient(app, client=LOCAL) as local:
        with local.websocket_connect("/dev/echo") as ws:
            assert ws.receive_text() == "hello"
    with TestClient(app, client=REMOTE) as remote:
        with pytest.raises(WebSocketDisconnect) as refused:
            with remote.websocket_connect("/dev/echo"):
                pass
        # In-process only: uvicorn turns a close-before-accept into a 403 on
        # the handshake and drops the code. 1008 is what distinguishes
        # "refused" from Starlette's 1000 for a socket path that doesn't exist.
        assert refused.value.code == 1008


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


def test_api_layout_is_the_plan_the_png_draws(config):
    """/api/layout exists so the table model's browser wall can place its
    plates exactly where the e-paper frame does (#138). That is only true if
    the endpoint serves the very numbers render_wall_png draws — same
    function, same inputs — so pin it against plan_wall directly."""
    import json

    from bird_painter.render import plan_wall

    small = dataclasses.replace(config, wall_png_width=320, wall_png_height=240)
    with TestClient(create_app(small), client=LOCAL) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        body = client.get("/api/layout").json()
        # Defaults: the frame's own style and size — a bare call IS the panel.
        assert (body["style"], body["width"], body["height"]) == ("panel", 320, 240)
        live = client.get("/api/live").json()["paintings"]
        paintings = [
            {"file": p["file"], "species_common": p["species_common"],
             "born_at": p["born_at"]}
            for p in live
        ]
        plan = plan_wall(paintings, small.archive_dir, 320, 240, style="panel")
        assert body == json.loads(json.dumps(plan.as_json()))
        # Newest-first, one placement per live bird, every bird has an ink entry
        # (None for the SVG placeholders /dev/paint writes without a key).
        assert [p["file"] for p in body["placements"]] == [p["file"] for p in live]
        assert set(body["ink"]) == {p["file"] for p in live}
        for p in body["placements"]:
            assert p["height_vmin"] > 0 and p["size_vmin"] > 0

        # The browser passes its own viewport; the spiral is available too and
        # draws whole plates, so it carries no ink boxes.
        other = client.get("/api/layout?style=wall&width=720&height=1280").json()
        assert (other["style"], other["width"], other["height"]) == ("wall", 720, 1280)
        assert other["ink"] == {}
        assert other["placements"], "a live set laid out to nothing"


def test_api_layout_refuses_bad_style_and_sizes(config):
    with TestClient(create_app(config), client=LOCAL) as client:
        for query in (
            "style=bogus", "style=", "width=10", "height=99999", "width=abc",
            "width=0&height=0",
        ):
            assert client.get(f"/api/layout?{query}").status_code == 422, query


def test_wall_png_and_api_layout_share_one_plan(config, monkeypatch):
    """The PR's central invariant — the picture and the plan come from ONE
    function — was first tested by calling that function on both sides,
    which proved nothing (review of #139, B1). This spies on plan_wall: the
    PNG endpoint must go through it, exactly once, and /api/layout must serve
    the plan the PNG was drawn from."""
    import json

    from bird_painter import render

    drawn = []
    real = render.plan_wall

    def spy(*args, **kwargs):
        plan = real(*args, **kwargs)
        drawn.append(plan)
        return plan

    monkeypatch.setattr(render, "plan_wall", spy)
    small = dataclasses.replace(config, wall_png_width=320, wall_png_height=240)
    with TestClient(create_app(small), client=LOCAL) as client:
        for species in ("robin", "wren", "junco"):
            client.post(f"/dev/paint/{species}")
        assert client.get("/wall.png?style=panel").status_code == 200
        assert len(drawn) == 1, "the PNG did not go through plan_wall once"
        served = client.get("/api/layout").json()
        assert served == json.loads(json.dumps(drawn[0].as_json()))
        assert drawn[0].style == "panel"
        assert (drawn[0].width, drawn[0].height) == (320, 240)


def test_api_layout_accepts_its_own_boundaries(config):
    """The documented range is 64..8192; both ends are inside it."""
    with TestClient(create_app(config), client=LOCAL) as client:
        for w, h in ((64, 64), (8192, 8192), (64, 8192)):
            response = client.get(f"/api/layout?width={w}&height={h}")
            assert response.status_code == 200, (w, h)
            assert (response.json()["width"], response.json()["height"]) == (w, h)


def test_images_bare_serves_the_birds_ink_with_the_ground_keyed_out(config):
    """Panel mode shows the bird as the frame pastes it — the same crop and
    the same key-out, done by the server, because a browser cropping a padded
    plate leaves the plate's ground magnified under the bird (QA on #139)."""
    import io

    from PIL import Image, ImageDraw

    from bird_painter.store import Store

    plate = Image.new("RGB", (200, 250), (255, 255, 255))
    ImageDraw.Draw(plate).rectangle((40, 50, 119, 199), fill=(60, 40, 20))
    buf = io.BytesIO()
    plate.save(buf, "PNG")
    store = Store(config.archive_dir, ttl_seconds=100)
    painting = store.add(
        image_bytes=buf.getvalue(), extension="png", species_common="Test Bird",
        species_scientific="Testus birdus", confidence=0.9, source="detection",
    )
    with TestClient(create_app(config), client=LOCAL) as client:
        plain = client.get(f"/images/{painting.file}")
        assert plain.status_code == 200 and plain.content == buf.getvalue()
        # An explicit no is the plain file too, like /audio?download.
        assert client.get(f"/images/{painting.file}?bare=0").content == buf.getvalue()

        bare = client.get(f"/images/{painting.file}?bare=1")
        assert bare.status_code == 200
        assert bare.headers["content-type"] == "image/png"
        image = Image.open(io.BytesIO(bare.content))
        assert image.mode == "RGBA"
        assert image.size == (80, 150), "cropped to the ink's own bounds"
        # The bird is opaque; the crop has no white left to key, so the
        # corners — bird pixels here — are solid too. Key-out is exercised on
        # a plate with a ground margin below.
        assert image.getpixel((40, 75))[3] == 255

        # A plate whose crop still contains ground (a pale margin inside the
        # bounding box) has that ground keyed to alpha.
        soft = Image.new("RGB", (200, 250), (255, 255, 255))
        d = ImageDraw.Draw(soft)
        d.rectangle((40, 50, 119, 60), fill=(60, 40, 20))   # a bar at the top
        d.rectangle((40, 190, 119, 199), fill=(60, 40, 20))  # and the bottom
        buf2 = io.BytesIO()
        soft.save(buf2, "PNG")
        p2 = store.add(
            image_bytes=buf2.getvalue(), extension="png", species_common="Bar Bird",
            species_scientific="Barrus", confidence=0.9, source="detection",
        )
        keyed = Image.open(io.BytesIO(client.get(f"/images/{p2.file}?bare=1").content))
        assert keyed.size == (80, 150)
        assert keyed.getpixel((40, 75))[3] == 0, "white between the bars is keyed out"
        assert keyed.getpixel((40, 5))[3] == 255, "the bar is not"

        # Nothing to crop (an SVG placeholder): the plain file, plain type.
        client.post("/dev/paint/robin")
        svg = next(p for p in client.get("/api/live").json()["paintings"]
                   if p["file"].endswith(".svg"))
        fallback = client.get(f"/images/{svg['file']}?bare=1")
        assert fallback.status_code == 200
        assert fallback.headers["content-type"].startswith("image/svg")
        assert client.get("/images/nope.png?bare=1").status_code == 404


def test_api_layout_caption_scales_the_panels_type(config):
    with TestClient(create_app(config), client=LOCAL) as client:
        client.post("/dev/paint/robin")
        one = client.get("/api/layout?width=1280&height=720").json()
        big = client.get("/api/layout?width=1280&height=720&caption=1.5").json()
        # The literal pins the endpoint's own number, not a restatement of the
        # formula (round-2 QA of #146): 1280x720 sits at the panel's floor,
        # 9/11 px, so 1.5 is 13.5/16.5 — half rounds UP, not to even.
        assert (one["species_size"], one["heard_size"]) == (9, 11)
        assert (big["species_size"], big["heard_size"]) == (14, 17)
        for query in ("caption=0.1", "caption=3", "caption=abc", "caption=-1"):
            assert client.get(f"/api/layout?{query}").status_code == 422, query
        # Both rails accepted.
        for rail in ("0.5", "2"):
            assert client.get(f"/api/layout?caption={rail}").status_code == 200
