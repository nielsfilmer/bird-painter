"""The documentation endpoints — and, more importantly, guards that keep the
documentation honest: a stale example is worse than no example."""

import pytest
from fastapi.testclient import TestClient

from bird_painter.api_docs import ENDPOINTS, WEBSOCKET, describe
from bird_painter.events import detected_event, painted_event
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
        path = endpoint["path"].replace("{file}", "{filename}")
        assert (endpoint["method"], path) in routes, f"undocumented drift: {path}"


def test_every_real_api_route_is_documented(client):
    """…and the reverse: an endpoint added without a line in the description
    fails here rather than quietly going missing from the docs."""
    documented = {e["path"].replace("{file}", "{filename}") for e in ENDPOINTS}
    # FastAPI's own generated routes + the wall's ES module: not API surface.
    documented |= {
        "/openapi.json",
        "/redoc",
        "/docs/oauth2-redirect",
        "/layout.js",
    }
    served = {
        route.path
        for route in client.app.routes
        if "GET" in getattr(route, "methods", set())
        or "POST" in getattr(route, "methods", set())
    }
    assert served - documented == set()


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
