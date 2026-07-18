from bird_painter.gate import TriggerGate


class StubStore:
    def __init__(self):
        self.last = {}

    def last_painted_at(self, species):
        return self.last.get(species)


def test_unpainted_species_is_allowed():
    gate = TriggerGate(StubStore(), ttl_seconds=100, max_paints_per_hour=20)
    assert gate.allows("Robin", now=1000.0)


def test_cooldown_blocks_within_ttl_and_frees_after():
    store = StubStore()
    gate = TriggerGate(store, ttl_seconds=100, max_paints_per_hour=20)
    store.last["Robin"] = 1000.0
    gate.record(1000.0)
    assert not gate.allows("Robin", now=1050.0)
    assert gate.allows("Robin", now=1101.0)


def test_cooldown_boundary_exactly_ttl_is_allowed():
    """PLAN.md: paints iff now − last_painted_at ≥ TTL — the boundary is
    inclusive."""
    store = StubStore()
    gate = TriggerGate(store, ttl_seconds=100, max_paints_per_hour=20)
    store.last["Robin"] = 1000.0
    assert not gate.allows("Robin", now=1099.999)
    assert gate.allows("Robin", now=1100.0)


def test_cooldown_is_per_species():
    store = StubStore()
    gate = TriggerGate(store, ttl_seconds=100, max_paints_per_hour=20)
    store.last["Robin"] = 1000.0
    assert not gate.allows("Robin", now=1050.0)
    assert gate.allows("Wren", now=1050.0)


def test_hourly_cap_blocks_at_max_and_frees_after_an_hour():
    gate = TriggerGate(StubStore(), ttl_seconds=1, max_paints_per_hour=3)
    for i in range(3):
        gate.record(2000.0 + i)
    assert not gate.allows("New Bird", now=2003.0)
    assert gate.allows("New Bird", now=2000.0 + 3601)


def test_failed_paint_consumes_nothing():
    """PLAN.md failure policy: allows() then no record() (paint failed) must
    leave the species free and the cap untouched."""
    gate = TriggerGate(StubStore(), ttl_seconds=100, max_paints_per_hour=1)
    assert gate.allows("Finch", now=5000.0)
    # no record() — the paint failed
    assert gate.allows("Finch", now=5001.0)
