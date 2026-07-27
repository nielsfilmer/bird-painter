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


def test_load_skips_valid_json_that_isnt_an_object(archive_dir, store):
    # a line that parses but isn't a dict (null / number / array / string)
    add_painting(store, "Robin")
    good = store.meta_path.read_text().splitlines()[0]
    store.meta_path.write_text("null\n42\n[1, 2]\n" + good + "\n")
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
    # Every add is recorded exactly once as a complete parseable line and lands
    # in the live set. NOTE: on CPython+POSIX, O_APPEND + the GIL already make
    # these small buffered appends atomic, so this passes with or without the
    # lock — it guards against LOST/DUPLICATED adds and documents the
    # concurrent contract; the lock is defence-in-depth for non-CPython / larger
    # writes, not something this test can fail on.
    assert len(lines) == 100
    for line in lines:
        json.loads(line)
    assert len(store.live()) == 100


def _add(store, species, **kw):
    defaults = dict(
        image_bytes=b"img", extension="svg", species_common=species,
        species_scientific="x", confidence=0.9, source="detection",
    )
    defaults.update(kw)
    return store.add(**defaults)


def test_purge_removes_old_artifacts_and_keeps_young(archive_dir, monkeypatch):
    from bird_painter import store as store_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(store_mod.time, "time", lambda: clock["t"])
    store = store_mod.Store(archive_dir, ttl_seconds=100, retention_seconds=1000)
    old = _add(store, "Old Bird", audio_bytes=b"wav")
    clock["t"] += 600
    young = _add(store, "Young Bird")
    clock["t"] = old.born_at + 1500  # old past retention (1000), young not

    assert store.purge_expired() == 1
    assert [p.species_common for p in store._paintings] == ["Young Bird"]
    # Purged files are gone — image AND clip.
    assert not (archive_dir / old.file).exists()
    assert store.audio_file_for(old.file) is None
    assert (archive_dir / young.file).exists()


def test_purge_compacts_meta_so_reload_cannot_resurrect(archive_dir, monkeypatch):
    from bird_painter import store as store_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(store_mod.time, "time", lambda: clock["t"])
    store = store_mod.Store(archive_dir, ttl_seconds=100, retention_seconds=1000)
    _add(store, "Old Bird")
    clock["t"] += 1500
    _add(store, "Young Bird")
    store.purge_expired()
    # A fresh store (reboot) sees only the survivor.
    reloaded = store_mod.Store(archive_dir, ttl_seconds=100, retention_seconds=1000)
    assert [p.species_common for p in reloaded._paintings] == ["Young Bird"]


def test_boot_purge_runs_on_construction(archive_dir, monkeypatch):
    from bird_painter import store as store_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(store_mod.time, "time", lambda: clock["t"])
    store = store_mod.Store(archive_dir, ttl_seconds=100, retention_seconds=1000)
    old = _add(store, "Old Bird")
    clock["t"] += 5000  # wall was off for a long time
    rebooted = store_mod.Store(archive_dir, ttl_seconds=100, retention_seconds=1000)
    assert rebooted._paintings == []
    assert not (archive_dir / old.file).exists()


def test_live_triggers_throttled_purge(archive_dir, monkeypatch):
    from bird_painter import store as store_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(store_mod.time, "time", lambda: clock["t"])
    store = store_mod.Store(archive_dir, ttl_seconds=10**9, retention_seconds=1000)
    _add(store, "Old Bird")
    # Advance beyond retention AND the purge throttle; live() should purge.
    clock["t"] += 5000
    assert store.live() == []
    assert store._paintings == []


def test_retention_none_disables_purging(archive_dir, monkeypatch):
    from bird_painter import store as store_mod

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(store_mod.time, "time", lambda: clock["t"])
    store = store_mod.Store(archive_dir, ttl_seconds=10**9, retention_seconds=None)
    _add(store, "Forever Bird")
    clock["t"] += 10**8
    assert store.purge_expired() == 0
    assert len(store._paintings) == 1


def test_purge_never_deletes_outside_the_archive(tmp_path, monkeypatch):
    """A corrupt/crafted meta record with a traversal filename must not reach
    outside the archive dir (PoC'd during PR #75 review: it did)."""
    import json
    import time as _time

    from bird_painter import store as store_mod

    archive = tmp_path / "archive"
    archive.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    record = {
        "file": "../victim.txt", "species_common": "Evil",
        "species_scientific": "x", "confidence": 0.9,
        "born_at": _time.time() - 10**8, "source": "detection",
    }
    (archive / "meta.jsonl").write_text(json.dumps(record) + "\n")
    store_mod.Store(archive, ttl_seconds=100, retention_seconds=1000)  # boot purge
    assert victim.exists(), "purge escaped the archive dir"
