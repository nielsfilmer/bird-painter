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


def test_servable_extensions_cover_everything_the_app_writes():
    assert {".svg", ".png", ".jpg"} <= SERVABLE_EXTENSIONS
