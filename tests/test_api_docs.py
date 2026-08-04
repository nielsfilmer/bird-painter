"""The documentation endpoints — and, more importantly, guards that keep the
documentation honest: a stale example is worse than no example."""

import pytest
from fastapi.routing import APIWebSocketRoute
from fastapi.testclient import TestClient

from bird_painter.api_docs import ENDPOINTS, WEBSOCKET, describe
from bird_painter.events import EVENT_TYPES, PING_SECONDS, detected_event, painted_event
from bird_painter.store import Painting
from bird_painter.web import create_app


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app) as client:
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
        assert endpoint["method"] in {"GET", "POST"}
    for event in description["websocket"]["events"]:
        assert event["description"] and event["example"]["type"] == event["type"]


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
    """/dev/paint documents 201 and 502 — the 201 is exercised here; the 502
    path (a failing brush with a key set) is covered in test_web.py."""
    statuses = next(
        e["statuses"] for e in ENDPOINTS if e["path"] == "/dev/paint/{species}"
    )
    assert set(statuses) == {"201", "502"}
    assert client.post("/dev/paint/junco").status_code == 201


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
    from bird_painter.web import _NOT_DOWNLOAD

    note = next(
        p["note"]
        for e in ENDPOINTS
        if e["path"] == "/audio/{filename}"
        for p in e["params"]
        if p["name"] == "download"
    )
    for refused in _NOT_DOWNLOAD - {""}:
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
