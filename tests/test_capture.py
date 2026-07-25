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


def test_returned_window_is_safe_for_the_caller_to_mutate():
    # Contract: a caller may mutate/hold a returned window without corrupting a
    # later (overlapping) window. With hop < window the two share samples, so
    # this would fail if push handed back a live view of internal state.
    acc = _WindowAccumulator(window=10, hop=5)
    first = None
    for b in _blocks(10, 5):
        first = acc.push(b)
    assert first is not None
    first[:] = -999  # caller stomps its window
    second = acc.push(np.arange(10, 15, dtype="float32"))  # samples 10..14
    assert second is not None
    # second holds 5..14; the shared 5..9 region must be the real values
    assert list(second[:5]) == [5, 6, 7, 8, 9]


class _FakeEars:
    pass


def test_persistent_stream_failure_backs_off_exponentially(monkeypatch):
    """A permanently absent mic must not retry once a second forever — that
    floods the recorder Pi's journal (thousands of tracebacks an hour on an SD
    card). Consecutive failures back off up to the ceiling."""
    from bird_painter import capture

    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", lambda s: sleeps.append(s))

    listener = capture.MicListener(_FakeEars(), window_seconds=15)
    calls = {"n": 0}

    def always_fails(_on_detections):
        calls["n"] += 1
        if calls["n"] > 8:
            raise KeyboardInterrupt  # stop the loop
        raise OSError("no such device")

    monkeypatch.setattr(listener, "_stream_once", always_fails)
    listener.listen(lambda d: None)

    assert sleeps[:4] == [1.0, 2.0, 4.0, 8.0]  # exponential, not flat
    assert max(sleeps) <= capture.MAX_ERROR_BACKOFF_SECONDS  # capped


def test_backoff_resets_after_the_stream_recovers(monkeypatch):
    """A transient fault must still recover fast: once a stream runs, the next
    failure starts from the short backoff again."""
    from bird_painter import capture

    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", lambda s: sleeps.append(s))

    listener = capture.MicListener(_FakeEars(), window_seconds=15)
    script = ["fail", "fail", "fail", "ok", "fail", "stop"]
    calls = {"n": 0}

    def scripted(_on_detections):
        step = script[calls["n"]]
        calls["n"] += 1
        if step == "stop":
            raise KeyboardInterrupt
        if step == "fail":
            raise OSError("no such device")
        return  # "ok": the stream ran and returned

    monkeypatch.setattr(listener, "_stream_once", scripted)
    listener.listen(lambda d: None)

    # 3 failures escalate, the good run resets, so the next failure is short.
    assert sleeps[:3] == [1.0, 2.0, 4.0]
    assert sleeps[-1] == 1.0
