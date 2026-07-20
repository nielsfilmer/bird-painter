import numpy as np

from bird_painter.capture import _WindowAccumulator


def _blocks(total, size):
    """Split a 0..total-1 ramp into `size`-sample blocks."""
    data = np.arange(total, dtype="float32")
    return [data[i : i + size] for i in range(0, total, size)]


def test_no_window_until_a_full_window_is_buffered():
    acc = _WindowAccumulator(window=100, hop=100)
    emitted = [acc.push(b) for b in _blocks(90, 30)]  # 90 < 100 samples
    assert all(w is None for w in emitted)


def test_emits_window_of_exact_length_and_latest_samples():
    acc = _WindowAccumulator(window=100, hop=100)
    windows = [w for b in _blocks(100, 25) for w in [acc.push(b)] if w is not None]
    assert len(windows) == 1
    assert len(windows[0]) == 100
    # the window holds the most recent `window` samples (here, 0..99)
    assert windows[0][0] == 0 and windows[0][-1] == 99


def test_ring_buffer_keeps_only_the_latest_window():
    acc = _WindowAccumulator(window=100, hop=100)
    windows = [w for b in _blocks(300, 50) for w in [acc.push(b)] if w is not None]
    # 300 samples, hop 100 → 3 windows; the last holds 200..299
    assert len(windows) == 3
    assert windows[-1][0] == 200 and windows[-1][-1] == 299


def test_hop_smaller_than_window_overlaps():
    acc = _WindowAccumulator(window=100, hop=50)
    # feed 200 samples in 50-sample blocks; a full window forms at 100, then
    # every 50 more → windows after 100, 150, 200 samples = 3 windows
    windows = [w for b in _blocks(200, 50) for w in [acc.push(b)] if w is not None]
    assert len(windows) == 3
    assert [int(w[-1]) for w in windows] == [99, 149, 199]


def test_returned_window_is_a_copy_safe_to_mutate():
    acc = _WindowAccumulator(window=10, hop=10)
    win = None
    for b in _blocks(10, 10):
        win = acc.push(b)
    win[:] = -1  # caller mutating its copy
    nxt = acc.push(np.zeros(10, dtype="float32"))
    assert nxt is not None and nxt.max() >= 0  # internal buffer untouched
