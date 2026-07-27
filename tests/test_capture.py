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


def _scripted_listener(monkeypatch, script):
    """A MicListener whose _stream_once follows `script` ("fail"/"ok"/"stop"),
    with time.sleep recorded instead of slept. Returns (listener, sleeps)."""
    from bird_painter import capture

    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", lambda s: sleeps.append(s))
    listener = capture.MicListener(_FakeEars(), window_seconds=15)
    calls = {"n": 0}

    def scripted(_on_detections):
        step = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if step == "stop":
            raise KeyboardInterrupt
        if step == "fail":
            raise OSError("no such device")
        return  # "ok": the stream ran and returned (went silent)

    monkeypatch.setattr(listener, "_stream_once", scripted)
    return listener, sleeps


def test_persistent_stream_failure_backs_off_exponentially(monkeypatch):
    """A permanently absent mic must not retry once a second forever — that
    floods the recorder Pi's journal (thousands of tracebacks an hour onto an
    SD card). Consecutive failures escalate, then hold at the ceiling."""
    from bird_painter import capture

    listener, sleeps = _scripted_listener(monkeypatch, ["fail"] * 9 + ["stop"])
    listener.listen(lambda d: None)

    assert sleeps[:6] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]  # exponential, not flat
    # Actually reaches the ceiling and holds there (not merely "never exceeds").
    assert sleeps[6:] == [capture.MAX_ERROR_BACKOFF_SECONDS] * (len(sleeps) - 6)


def test_backoff_resets_after_the_stream_recovers(monkeypatch):
    """A transient fault must still recover fast: once a stream runs, the next
    failure starts from the short backoff again. Also pins that a normal return
    sleeps exactly one short backoff (not the grown one, and not twice)."""
    listener, sleeps = _scripted_listener(
        monkeypatch, ["fail", "fail", "fail", "ok", "fail", "stop"]
    )
    listener.listen(lambda d: None)

    # 3 failures escalate; the good run sleeps 1.0 and resets; next failure 1.0.
    assert sleeps == [1.0, 2.0, 4.0, 1.0, 1.0]


def test_repeated_failures_stop_logging_full_tracebacks(monkeypatch, caplog):
    """The half of the fix that delivers the volume win: after the first few
    failures the traceback collapses to a single line. Without this the
    escalating backoff alone still writes a traceback per retry."""
    import logging

    from bird_painter import capture

    listener, _ = _scripted_listener(monkeypatch, ["fail"] * 6 + ["stop"])
    with caplog.at_level(logging.ERROR, logger="bird_painter.capture"):
        listener.listen(lambda d: None)

    with_tb = [r for r in caplog.records if r.exc_info]
    without_tb = [r for r in caplog.records if not r.exc_info]
    assert len(with_tb) == capture.TRACEBACK_ATTEMPTS  # first N carry a traceback
    assert without_tb, "later failures must log without a traceback"
    assert "still failing" in without_tb[0].getMessage()


def test_ctrl_c_during_the_backoff_sleep_stops_cleanly(monkeypatch):
    """With no mic the loop spends nearly all its time asleep, so Ctrl-C lands
    there — it must stop cleanly rather than escape as a traceback."""
    from bird_painter import capture

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(capture.time, "sleep", interrupt)
    listener = capture.MicListener(_FakeEars(), window_seconds=15)
    monkeypatch.setattr(
        listener, "_stream_once", lambda _o: (_ for _ in ()).throw(OSError("gone"))
    )
    listener.listen(lambda d: None)  # must return, not raise


def test_failure_count_resets_so_a_new_fault_logs_a_traceback(monkeypatch, caplog):
    """After the mic recovers, a later fault is a NEW problem worth diagnosing —
    so the failure count must reset and full tracebacks resume. Without the
    reset the loop stays collapsed to one-liners forever after the first
    persistent outage."""
    import logging

    listener, _ = _scripted_listener(
        monkeypatch, ["fail"] * 4 + ["ok", "fail", "stop"]
    )
    with caplog.at_level(logging.ERROR, logger="bird_painter.capture"):
        listener.listen(lambda d: None)

    # 4 failures: 3 tracebacks then 1 one-liner. Then a good run, then a fresh
    # failure — which must carry a traceback again, not a "still failing" line.
    kinds = [bool(r.exc_info) for r in caplog.records]
    assert kinds == [True, True, True, False, True]


def test_analyse_window_passes_window_and_rate_to_the_callback(monkeypatch):
    """The callback contract is (detections, window, samplerate) — pin it, and
    pin that listen_cli's printer accepts that arity (it silently printed
    nothing when this drifted)."""
    import numpy as np

    from bird_painter import capture, listen_cli
    from bird_painter.ears import Detection

    listener = capture.MicListener(_FakeEars(), window_seconds=15)
    det = Detection("Robin", "E. rubecula", 0.9, 0.0, 3.0)
    monkeypatch.setattr(
        listener, "ears",
        type("E", (), {"detect_samples": lambda self, w, r: [det]})(),
    )
    got = {}

    def callback(detections, window, samplerate):
        got.update(detections=detections, window=window, samplerate=samplerate)

    win = np.zeros(10, dtype="float32")
    listener._analyse_window(win, callback)
    assert got["detections"] == [det]
    assert got["samplerate"] == listener.samplerate
    assert got["window"] is win
    # listen_cli's callback must accept the same shape without raising.
    listen_cli._print_detections([det], win, listener.samplerate)
