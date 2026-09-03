"""The documentation endpoints — and, more importantly, guards that keep the
documentation honest: a stale example is worse than no example."""

import pytest
from fastapi.routing import APIWebSocketRoute
from fastapi.testclient import TestClient

from bird_painter.api_docs import ENDPOINTS, WEBSOCKET, describe
from bird_painter.events import EVENT_TYPES, PING_SECONDS, detected_event, painted_event
from bird_painter.store import Painting
from bird_painter.web import STATIC_DIR, create_app
from tests.conftest import LOCAL, REMOTE


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app, client=LOCAL) as client:
        yield client


def example_for(event_type: str) -> dict:
    return next(e for e in WEBSOCKET["events"] if e["type"] == event_type)["example"]


def test_api_serves_the_description_with_this_instance_settings(client, config):
    body = client.get("/api").json()
    assert body["service"] == "bird-painter"
    assert body["settings"]["paint_ttl_seconds"] == config.paint_ttl_seconds
    assert body["settings"]["wall_max_live"] == config.wall_max_live
    assert body["websocket"]["path"] == "/ws/detections"


def test_api_docs_page_renders_and_reads_the_description(client):
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert "<title>bird-painter — API</title>" in page
    # The page is a renderer, not a second copy of the docs: it fetches /api
    # and connects to the stream it documents.
    assert 'fetch("/api")' in page
    assert "new WebSocket(" in page


def test_every_documented_endpoint_actually_exists(client):
    """Documentation that lies is the failure mode worth a test: every path in
    the description must be a real route on the app."""
    routes = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    for endpoint in ENDPOINTS:
        path = endpoint["path"]
        assert (endpoint["method"], path) in routes, f"undocumented drift: {path}"


def test_every_real_api_route_is_documented(client):
    """…and the reverse: an endpoint added without a line in the description
    fails here rather than quietly going missing from the docs.

    Matched on (method, path), not path alone — round-1 review demonstrated
    that a new POST on an already-documented GET path slipped straight
    through — and WebSocket routes count, since the stream is the half most
    worth documenting."""
    documented = {(e["method"], e["path"]) for e in ENDPOINTS}
    documented.add(("WEBSOCKET", WEBSOCKET["path"]))
    # FastAPI's own generated routes + the wall's ES module: not API surface.
    documented |= {
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/layout.js"),
        ("GET", "/unit-screen.js"),
    }
    served = set()
    for route in client.app.routes:
        if isinstance(route, APIWebSocketRoute):
            served.add(("WEBSOCKET", route.path))
            continue
        for method in getattr(route, "methods", set()):
            if method in {"HEAD", "OPTIONS"}:  # FastAPI adds these itself
                continue
            served.add((method, route.path))
    assert served - documented == set()


def test_every_event_the_stream_can_emit_is_documented():
    """The same guard for events: the roster lives beside the producers in
    events.py, so a new event type fails here instead of going unmentioned."""
    documented = {event["type"] for event in WEBSOCKET["events"]}
    assert documented == set(EVENT_TYPES)


def test_websocket_path_is_the_one_the_app_serves(client):
    with client.websocket_connect(WEBSOCKET["path"]) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_documented_hello_matches_the_real_hello(client):
    with client.websocket_connect("/ws/detections") as ws:
        assert set(ws.receive_json()) == set(example_for("hello"))


def test_documented_painted_example_matches_a_real_painted_event():
    real = painted_event(
        Painting(
            file="x.jpg",
            species_common="Eurasian Wren",
            species_scientific="Troglodytes troglodytes",
            confidence=0.8123,
            born_at=1785866477.948883,
            source="detection",
        ),
        "x.wav",
    )
    example = example_for("painted")
    assert set(real) == set(example)
    assert set(real["image"]) == set(example["image"])
    assert set(real["audio"]) == set(example["audio"])


def test_documented_detected_example_matches_a_real_detected_event():
    real = detected_event(
        species_common="Eurasian Wren",
        species_scientific="Troglodytes troglodytes",
        confidence=0.8123,
        at=1785866477.512,
        will_paint=True,
    )
    assert set(real) == set(example_for("detected"))


def test_description_carries_no_placeholder_gaps(config):
    """Every documented thing has a summary and a description — an empty one
    ships an obviously-unfinished page."""
    description = describe(config)
    for endpoint in description["endpoints"]:
        assert endpoint["summary"] and endpoint["description"]
        assert endpoint["method"] in {"GET", "POST", "PUT"}
    for event in description["websocket"]["events"]:
        assert event["description"] and event["example"]["type"] == event["type"]


def test_endpoint_prose_carries_no_markdown_the_page_cannot_render(config):
    """QA on #92: a `**bold**` in an endpoint description printed its own
    asterisks on /api/docs, whose renderer does code spans and nothing else.
    Descriptions bound for the page stay plain prose; the OpenAPI-only text
    (rendered by Swagger) may use markdown."""
    description = describe(config)
    prose = []
    for endpoint in description["endpoints"]:
        # Everything the page prints, not just descriptions: status meanings
        # and param notes reach the reader the same way (round-2 review of
        # #92 — the first version of this guard walked descriptions only).
        prose.append(endpoint["description"])
        prose += endpoint.get("statuses", {}).values()
        prose += [p.get("note", "") for p in endpoint.get("params", [])]
    prose += [description["websocket"]["description"]]
    prose += description["websocket"]["notes"]
    prose += [e["description"] for e in description["websocket"]["events"]]
    for text in prose:
        assert "**" not in text, text
        assert "\n" not in text, text  # the page renders one paragraph


def test_swagger_still_serves_and_points_at_the_human_page(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/docs" in schema["info"]["description"]


def test_documented_endpoint_examples_match_the_live_responses(client):
    """Event examples were guarded from the start; endpoint examples were not
    (round-1 review). Same rule for both: an example that no longer matches
    the response is a lie the page tells confidently."""
    client.post("/dev/paint/robin")

    def example_for(method: str, path: str) -> dict:
        return next(
            e["example"]
            for e in ENDPOINTS
            if e["method"] == method and e["path"] == path
        )

    live = client.get("/api/live").json()
    live_example = example_for("GET", "/api/live")
    assert set(live) == set(live_example)
    assert set(live["paintings"][0]) == set(live_example["paintings"][0])

    archive = client.get("/api/archive").json()
    archive_example = example_for("GET", "/api/archive")
    assert set(archive) == set(archive_example)
    assert set(archive["paintings"][0]) == set(archive_example["paintings"][0])

    painted = client.post("/dev/paint/wren")
    assert set(painted.json()) == set(example_for("POST", "/dev/paint/{species}"))


def test_documented_statuses_are_the_ones_the_endpoint_returns(client):
    """/dev/paint documents 201, 404 and 502 — the 201 and the 404 are
    exercised here; the 502 (a failing brush with a key set) is covered in
    test_web.py."""
    statuses = next(
        e["statuses"] for e in ENDPOINTS if e["path"] == "/dev/paint/{species}"
    )
    assert set(statuses) == {"201", "404", "502"}
    assert client.post("/dev/paint/junco").status_code == 201  # client is LOCAL
    with TestClient(client.app, client=REMOTE) as remote:
        assert remote.post("/dev/paint/junco").status_code == 404


def test_documented_ping_interval_is_the_one_the_stream_uses(client):
    """A retyped '30s' in prose is exactly how docs rot; both the example and
    the note are tied to the constant."""
    hello_example = next(
        e["example"] for e in WEBSOCKET["events"] if e["type"] == "hello"
    )
    assert hello_example["ping_seconds"] == PING_SECONDS
    assert any(f"{PING_SECONDS}s" in note for note in WEBSOCKET["notes"])
    with client.websocket_connect("/ws/detections") as ws:
        assert ws.receive_json()["ping_seconds"] == PING_SECONDS


def test_api_publishes_only_the_allowlisted_settings(client):
    """The wall is unauthenticated on the LAN, so /api's settings block is an
    allowlist, not a dump of Config: one careless addition would publish the
    house's coordinates or the fal key."""
    assert set(client.get("/api").json()["settings"]) == {
        "paint_ttl_seconds",
        "wall_max_live",
        "max_paints_per_hour",
        "confidence_floor",
        "retention_days",
        "listener_enabled",
    }


def test_openapi_lists_the_websocket_so_swagger_readers_find_it(client):
    """OpenAPI has no WebSocket operation, so the stream is folded in as the
    upgrade handshake it is — otherwise a reader of /docs never learns it
    exists (the complaint that prompted this)."""
    schema = client.get("/openapi.json").json()
    stream = schema["paths"]["/ws/detections"]["get"]
    assert "[WebSocket]" in stream["summary"]
    assert "101" in stream["responses"]
    # every event documented on the page is described there too
    for event in WEBSOCKET["events"]:
        assert f"`{event['type']}`" in stream["description"]


def test_the_pages_curl_hint_points_at_the_wall_not_at_the_reader(client):
    """QA on #92: the empty state said "from the wall's own machine" and then
    printed the reader's own LAN origin, a command that 404s for them."""
    page = client.get("/api/docs").text
    assert "`http://127.0.0.1${location.port" in page
    assert "location.origin" not in page.split("curl-base")[1].split("\n")[0]


def test_the_page_can_still_stream_when_the_description_fails(client):
    """Round-1 blocking finding: the page died silently if /api failed. The
    console must not depend on the fetch — the stream path is fixed in the
    page, and the fetch is guarded."""
    page = client.get("/api/docs").text
    assert 'const WS_PATH = "/ws/detections"' in page
    assert "describeFailure" in page
    assert "response.ok" in page


def test_documented_download_values_are_the_ones_the_code_refuses():
    """The `?download` prose was the one documented constant not pinned — and
    it is exactly the sentence that was wrong in round 1."""
    from bird_painter.web import _FLAG_OFF

    note = next(
        p["note"]
        for e in ENDPOINTS
        if e["path"] == "/audio/{filename}"
        for p in e["params"]
        if p["name"] == "download"
    )
    for refused in _FLAG_OFF - {""}:
        assert refused in note
    assert "empty" in note  # the "" case, which reads as a word not a value


def test_openapi_documents_the_status_a_plain_get_actually_returns(client):
    """Round-2 review: the schema claimed 400, but Starlette never matches a
    WebSocket route for an http request — it is a 404."""
    schema = client.get("/openapi.json").json()
    assert set(schema["paths"]["/ws/detections"]["get"]["responses"]) == {"101", "404"}
    assert client.get("/ws/detections").status_code == 404


def test_openapi_keeps_what_fastapi_generates(client):
    """The WebSocket is folded into FastAPI's own schema, not a rebuilt one —
    a rebuild silently dropped fields it fills in."""
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "bird-painter"
    assert schema["openapi"].startswith("3.")
    assert "/api/live" in schema["paths"]  # the generated half survives
    assert "/ws/detections" in schema["paths"]  # and the added half is there


def test_documented_wall_png_values_are_the_ones_the_endpoint_accepts():
    """The `/wall.png` params went undocumented through a whole review round
    while the PR body claimed otherwise (review, 2026-08-20). The existing
    drift guards compare paths, so a route growing a param slipped past them.
    Pin the values themselves against the sets the endpoint validates."""
    from bird_painter.web import WALL_LAYERS, WALL_STYLES

    entry = next(e for e in ENDPOINTS if e["path"] == "/wall.png")
    documented = {p["name"]: p for p in entry.get("params", [])}
    assert set(documented) == {"style", "layer"}
    for name, allowed in (("style", WALL_STYLES), ("layer", WALL_LAYERS)):
        note = documented[name]["note"]
        for value in allowed:
            assert value in note, f"{name}={value} undocumented"
        assert documented[name]["default"] in allowed
    assert "422" in entry["statuses"]


def test_the_http_layer_and_the_renderer_accept_the_same_values():
    """Three copies of these sets existed: the endpoint's validation, the
    renderer's, and the documentation (review round 2). The drift guard above
    covers docs-vs-endpoint; this one covers endpoint-vs-renderer, so a value
    can't be added to one and missed by the other."""
    from bird_painter.render import LAYERS, STYLES
    from bird_painter.web import WALL_LAYERS, WALL_STYLES

    assert set(WALL_LAYERS) == set(LAYERS)
    assert set(WALL_STYLES) == set(STYLES)


def js_constant(name: str) -> float:
    """A numeric constant as written in static/layout.js. The bounds the docs
    quote live only there, in another language; this is the one reader."""
    import re

    source = (STATIC_DIR / "layout.js").read_text()
    match = re.search(rf"\b{name}\s*=\s*([0-9.]+)", source)
    assert match, f"{name} not found in layout.js — did it get renamed?"
    return float(match.group(1))


def test_documented_wall_params_match_layout_js_clamps():
    """Same failure mode as the /wall.png guard above, one endpoint along: `/`
    grew `spread` and `caption` in PR #132 while the PR body claimed "no
    server surface, no API-docs drift".

    The ranges are enforced in JavaScript (`normalizePanelOpts` in
    static/layout.js), so there is no Python constant to compare against —
    parse them out of the module and pin the documented bounds to the real
    ones. Ugly, but the alternative is a doc that drifts from the only place
    the clamp actually lives.
    """
    constant = js_constant
    entry = next(e for e in ENDPOINTS if e["path"] == "/")
    documented = {p["name"]: p for p in entry.get("params", [])}
    assert set(documented) == {"spread", "caption", "ui"}

    # Round-2 review, N10: substring-matching each bound against prose passed
    # even when a bound changed (4 -> 2 still "matched" a note containing a
    # "2" elsewhere). Assert the exact "lo to hi" phrase the notes are written
    # with, so a moved bound fails loudly.
    def rendered(x: float) -> str:
        return f"{x:g}"

    caption_range = (
        f"({rendered(constant('CAPTION_SCALE_MIN'))} to "
        f"{rendered(constant('CAPTION_SCALE_MAX'))})"
    )
    expected = {
        "spread": f"({rendered(0)} to {rendered(constant('CLUSTER_W_FRAC'))})",
        "caption": caption_range,
        # The archive knob shares the caption knob's bounds, by design.
        "ui": caption_range,
    }
    for name, phrase in expected.items():
        assert phrase in documented[name]["note"], (
            f"{name}'s documented range is stale: layout.js says {phrase}, "
            f"the note says {documented[name]['note']!r}"
        )
    assert float(documented["spread"]["default"]) == constant("DEFAULT_SPREAD")
    assert float(documented["caption"]["default"]) == constant(
        "DEFAULT_CAPTION_SCALE"
    )
    assert float(documented["ui"]["default"]) == constant("DEFAULT_UI_SCALE")


def test_documented_layout_example_matches_the_live_response(config):
    """Same rule as the other endpoint examples: the shape shown on /api/docs
    is the shape /api/layout actually returns, down to a placement's keys."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    from bird_painter.store import Store

    # A RASTER plate, so the live `ink` carries a real box: under /dev/paint
    # alone every plate is an SVG placeholder and every box is null, which
    # left the documented 4-float shape unpinned (round-2 review of #139).
    plate = Image.new("RGB", (200, 250), (255, 255, 255))
    ImageDraw.Draw(plate).rectangle((40, 50, 119, 199), fill=(60, 40, 20))
    buf = BytesIO()
    plate.save(buf, "PNG")
    painting = Store(config.archive_dir, ttl_seconds=100).add(
        image_bytes=buf.getvalue(), extension="png", species_common="Test Bird",
        species_scientific="Testus", confidence=0.9, source="detection",
    )
    # Built AFTER the plate exists: the app's store reads the archive once, at
    # startup, and a plate added behind its back is invisible to it.
    with TestClient(create_app(config), client=LOCAL) as client:
        client.post("/dev/paint/robin")
        live = client.get("/api/layout").json()
    example = next(
        e["example"] for e in ENDPOINTS
        if e["method"] == "GET" and e["path"] == "/api/layout"
    )
    assert set(live) == set(example)
    assert set(live["placements"][0]) == set(example["placements"][0])
    assert set(live["ink"]) == {p["file"] for p in live["placements"]}
    box = live["ink"][painting.file]
    doc_box = next(iter(example["ink"].values()))
    assert len(box) == len(doc_box) == 4
    assert all(isinstance(v, float) and 0.0 <= v <= 1.0 for v in box)
    # …and the placeholder's is the documented null.
    svg = next(f for f in live["ink"] if f.endswith(".svg"))
    assert live["ink"][svg] is None


def test_documented_layout_bounds_are_the_ones_the_endpoint_enforces():
    """Third time this repo writes this guard, each time after a review found
    the prose had drifted from the code (#139, B2). The range is typed into
    the param notes and the 422 text; pin all of them to the constants."""
    from bird_painter.web import LAYOUT_MAX_SIDE, LAYOUT_MIN_SIDE

    entry = next(e for e in ENDPOINTS if e["path"] == "/api/layout")
    phrase = f"{LAYOUT_MIN_SIDE}..{LAYOUT_MAX_SIDE}"
    documented = {p["name"]: p for p in entry["params"]}
    assert phrase in documented["width"]["note"]
    assert phrase in documented["height"]["note"]
    assert phrase in entry["statuses"]["422"]
    assert documented["style"]["default"] == "panel"


def test_documented_layout_caption_bounds_match_the_endpoint_and_the_spiral_knob():
    """One number in a kiosk URL means one thing: the panel's caption scale on
    /api/layout has the same bounds as the spiral's ?caption= in layout.js.
    Pinned both ways — docs to constants, and web constants to JS constants."""
    from bird_painter.web import LAYOUT_CAPTION_MAX, LAYOUT_CAPTION_MIN

    entry = next(e for e in ENDPOINTS if e["path"] == "/api/layout")
    # The param set itself is guarded, like /wall.png's and /'s — a route
    # growing a param has slipped past the path-only guards before.
    assert {p["name"] for p in entry["params"]} == {
        "style", "width", "height", "caption",
    }
    caption = next(p for p in entry["params"] if p["name"] == "caption")
    phrase = f"{LAYOUT_CAPTION_MIN:g}..{LAYOUT_CAPTION_MAX:g}"
    assert phrase in caption["note"]
    assert phrase in entry["statuses"]["422"]
    assert (js_constant("CAPTION_SCALE_MIN"), js_constant("CAPTION_SCALE_MAX")) == (
        LAYOUT_CAPTION_MIN, LAYOUT_CAPTION_MAX
    )
