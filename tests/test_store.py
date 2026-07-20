import time

from bird_painter.store import SERVABLE_EXTENSIONS, Store

from .conftest import add_painting


def test_add_archives_file_and_metadata(store):
    painting = add_painting(store)
    assert (store.archive_dir / painting.file).read_bytes() == b"<svg/>"
    assert store.meta_path.exists()
    assert painting.species_common == "European Robin"


def test_same_second_same_species_never_overwrites(store):
    first = add_painting(store)
    second = add_painting(store)
    assert first.file != second.file
    assert (store.archive_dir / first.file).exists()
    assert (store.archive_dir / second.file).exists()


def test_live_hides_expired_but_keeps_archive_file(store):
    painting = add_painting(store)
    now = time.time()
    assert [p.file for p in store.live(now)] == [painting.file]
    after_ttl = now + store.ttl_seconds + 1
    assert store.live(after_ttl) == []
    # expiry hides, never deletes (PLAN.md: archive is permanent)
    assert (store.archive_dir / painting.file).exists()


def test_live_returns_newest_first(store):
    add_painting(store, "Wren")
    later = add_painting(store, "Robin")
    assert store.live()[0].file == later.file


def test_last_painted_at_sees_expired_paintings(store):
    """The trigger-gate cooldown keys on last_painted_at regardless of wall
    presence — an expired (hidden) painting must still count."""
    painting = add_painting(store)
    after_ttl = painting.born_at + store.ttl_seconds + 1
    assert store.live(after_ttl) == []
    assert store.last_painted_at("European Robin") == painting.born_at
    assert store.last_painted_at("Unheard Bird") is None


def test_reload_restores_live_set_and_cooldowns(store):
    painting = add_painting(store)
    reloaded = Store(store.archive_dir, store.ttl_seconds)
    assert [p.file for p in reloaded.live()] == [painting.file]
    assert reloaded.last_painted_at("European Robin") == painting.born_at


def test_image_path_serves_archived_images_only(store):
    painting = add_painting(store)
    assert store.image_path(painting.file) is not None
    assert store.image_path("nope.svg") is None
    # traversal
    assert store.image_path("../secrets.svg") is None
    assert store.image_path("sub/dir.svg") is None
    # non-image files in the archive dir are unreachable
    assert store.image_path("meta.jsonl") is None


def test_servable_extensions_include_the_types_the_app_writes():
    # brush writes png/jpg, placeholder writes svg
    assert {".svg", ".png", ".jpg"} <= SERVABLE_EXTENSIONS


def test_load_ignores_unknown_future_fields(archive_dir, store):
    # a newer version wrote an extra key; old code must still boot
    add_painting(store)
    line = store.meta_path.read_text().splitlines()[0]
    import json
    rec = json.loads(line)
    rec["mood"] = "serene"  # field this Painting doesn't know
    store.meta_path.write_text(json.dumps(rec) + "\n")
    reloaded = Store(archive_dir, ttl_seconds=100)
    assert len(reloaded.live()) == 1


def test_load_skips_records_missing_a_required_field(archive_dir, store):
    add_painting(store, "Robin")
    add_painting(store, "Wren")
    import json
    lines = store.meta_path.read_text().splitlines()
    broken = json.loads(lines[0])
    del broken["confidence"]  # drop a required field
    store.meta_path.write_text(json.dumps(broken) + "\n" + lines[1] + "\n")
    reloaded = Store(archive_dir, ttl_seconds=100)
    # the good record survives, the broken one is skipped (not a crash)
    assert [p.species_common for p in reloaded.live()] == ["Wren"]


def test_load_skips_unparseable_lines(archive_dir, store):
    add_painting(store, "Robin")
    good = store.meta_path.read_text().splitlines()[0]
    store.meta_path.write_text("{not json\n" + good + "\n")
    reloaded = Store(archive_dir, ttl_seconds=100)
    assert [p.species_common for p in reloaded.live()] == ["Robin"]


def test_concurrent_adds_do_not_interleave_meta_lines(store):
    import json
    import threading

    def worker(n):
        for _ in range(25):
            add_painting(store, f"Species {n}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = [x for x in store.meta_path.read_text().splitlines() if x.strip()]
    assert len(lines) == 100
    # every line is a complete, parseable JSON object — no torn writes
    for line in lines:
        json.loads(line)
    assert len(store.live()) == 100
