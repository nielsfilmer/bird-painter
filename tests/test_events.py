import asyncio
import datetime

from bird_painter.events import (
    EventHub,
    absolutize,
    detected_event,
    painted_event,
)
from bird_painter.store import Painting


def a_painting(file: str = "1_robin_ab.jpg") -> Painting:
    return Painting(
        file=file,
        species_common="European Robin",
        species_scientific="Erithacus rubecula",
        confidence=0.876543,
        born_at=1_700_000_000.0,
        source="detection",
    )


def test_painted_event_carries_name_time_image_and_clip():
    event = painted_event(a_painting(), "1_robin_ab.wav")
    assert event["type"] == "painted"
    assert event["species_common"] == "European Robin"
    assert event["at"] == 1_700_000_000.0
    # local ISO 8601 with offset — round-trips back to the same instant
    assert datetime.datetime.fromisoformat(event["time"]).timestamp() == event["at"]
    assert event["confidence"] == 0.8765  # rounded, not a float smear
    assert event["image"]["url"] == "/images/1_robin_ab.jpg"
    assert event["audio"]["url"] == "/audio/1_robin_ab.wav"
    assert event["audio"]["download_url"] == "/audio/1_robin_ab.wav?download=1"


def test_painted_event_without_a_clip_has_null_audio():
    assert painted_event(a_painting(), None)["audio"] is None


def test_detected_event_reports_the_gate_verdict():
    event = detected_event(
        species_common="Wren",
        species_scientific="Troglodytes troglodytes",
        confidence=0.7,
        at=1_700_000_000.0,
        will_paint=False,
    )
    assert event["type"] == "detected"
    assert event["will_paint"] is False


def test_absolutize_rewrites_only_url_fields():
    event = absolutize(painted_event(a_painting(), "1_robin_ab.wav"), "http://pi:8537")
    assert event["image"]["url"] == "http://pi:8537/images/1_robin_ab.jpg"
    assert event["audio"]["download_url"] == (
        "http://pi:8537/audio/1_robin_ab.wav?download=1"
    )
    assert event["image"]["file"] == "1_robin_ab.jpg"  # untouched
    assert event["species_common"] == "European Robin"


def test_absolutize_tolerates_an_event_without_assets():
    event = detected_event(
        species_common="Wren",
        species_scientific="Troglodytes troglodytes",
        confidence=0.7,
        at=1.0,
        will_paint=True,
    )
    assert absolutize(event, "http://pi:8537") == event


def test_publish_without_a_bound_loop_only_fills_the_backlog():
    hub = EventHub()
    hub.publish({"type": "painted", "n": 1})
    assert hub.backlog() == [{"type": "painted", "n": 1}]


def test_backlog_keeps_only_the_most_recent():
    hub = EventHub(backlog_size=2)
    for n in range(5):
        hub.publish({"n": n})
    assert [e["n"] for e in hub.backlog()] == [3, 4]


def test_subscribers_receive_published_events():
    async def scenario():
        hub = EventHub()
        hub.bind(asyncio.get_running_loop())
        with hub.subscribe() as queue:
            assert hub.subscriber_count == 1
            hub.publish({"n": 1})
            # publish hops onto the loop; yield so the fan-out callback runs.
            await asyncio.sleep(0)
            assert await asyncio.wait_for(queue.get(), timeout=1) == {"n": 1}
        assert hub.subscriber_count == 0

    asyncio.run(scenario())


def test_every_subscriber_gets_every_event():
    async def scenario():
        hub = EventHub()
        hub.bind(asyncio.get_running_loop())
        with hub.subscribe() as first, hub.subscribe() as second:
            hub.publish({"n": 1})
            await asyncio.sleep(0)
            assert first.get_nowait() == {"n": 1}
            assert second.get_nowait() == {"n": 1}

    asyncio.run(scenario())


def test_a_slow_subscriber_loses_its_oldest_events_not_the_newest():
    async def scenario():
        hub = EventHub(queue_size=2)
        hub.bind(asyncio.get_running_loop())
        with hub.subscribe() as queue:
            for n in range(4):
                hub.publish({"n": n})
            await asyncio.sleep(0)
            assert [queue.get_nowait()["n"] for _ in range(2)] == [2, 3]

    asyncio.run(scenario())


def test_unbind_stops_fan_out_but_publishing_still_survives():
    async def scenario():
        hub = EventHub()
        hub.bind(asyncio.get_running_loop())
        with hub.subscribe() as queue:
            hub.unbind()
            hub.publish({"n": 1})  # must not raise
            await asyncio.sleep(0)
            assert queue.empty()

    asyncio.run(scenario())
