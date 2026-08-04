"""The clip cleanup. The point of this feature is audibility, so the tests
measure it — signal-to-noise and level — rather than asserting the code ran."""

import numpy as np
import pytest

from bird_painter.clip_clean import BAND_MAX_HZ, CEILING, enhance

SAMPLERATE = 48000


def a_bird(
    seconds: float = 2.0,
    freq: float = 4200.0,
    amplitude: float = 0.02,
    noise: float = 0.05,
    rumble: float = 0.25,
    thump_hz: float = 0.0,
    thump: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """A quiet chirp buried in the kind of noise a window mic actually picks
    up: broadband hiss plus heavy traffic rumble. Returns (mixture, the chirp
    alone) so tests can measure what survived.

    `thump_hz`/`thump` add a loud IN-BAND transient — a door, a gust, a hand on
    the mic stand. Round-1 review of #96 caught that rumble at 55/90 Hz sits
    below the band search's own floor, so nothing in this fixture could ever
    compete with the bird; the real archive then showed the picker losing to
    exactly this."""
    rng = np.random.default_rng(1)
    t = np.linspace(0, seconds, int(seconds * SAMPLERATE), endpoint=False)
    # The bird sings in the middle third only.
    envelope = np.zeros_like(t)
    third = len(t) // 3
    envelope[third : 2 * third] = np.hanning(third)
    bird = amplitude * envelope * np.sin(2 * np.pi * freq * t)
    hiss = noise * rng.standard_normal(len(t))
    traffic = rumble * np.sin(2 * np.pi * 90 * t) + rumble * 0.5 * np.sin(
        2 * np.pi * 55 * t
    )
    knock = np.zeros_like(t)
    if thump:
        # A short, loud whack a third of the way in — transient, like a bird,
        # but not a bird.
        at = len(t) // 4
        width = int(0.05 * SAMPLERATE)
        knock[at : at + width] = (
            thump * np.hanning(width) * np.sin(2 * np.pi * thump_hz * t[:width])
        )
    return bird + hiss + traffic + knock, bird


def band_energy(samples: np.ndarray, low: float, high: float) -> float:
    spectrum = np.abs(np.fft.rfft(samples)) ** 2
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / SAMPLERATE)
    return float(spectrum[(freqs >= low) & (freqs <= high)].sum())


def snr(samples: np.ndarray, freq: float = 4200.0) -> float:
    """Energy in the bird's band over energy everywhere else."""
    signal = band_energy(samples, freq - 200, freq + 200)
    total = band_energy(samples, 0, SAMPLERATE / 2)
    return signal / max(total - signal, 1e-12)


def test_the_bird_comes_out_far_clearer_than_it_went_in():
    mixture, _ = a_bird()
    cleaned = enhance(mixture, SAMPLERATE)
    assert snr(cleaned) > snr(mixture) * 50


def test_traffic_rumble_is_gone():
    """The loudest thing in the raw clip is 55–90 Hz traffic; it should not be
    the loudest thing in the archived one."""
    mixture, _ = a_bird()
    cleaned = enhance(mixture, SAMPLERATE)
    raw_rumble = band_energy(mixture, 0, 200) / band_energy(mixture, 0, 24000)
    left_rumble = band_energy(cleaned, 0, 200) / band_energy(cleaned, 0, 24000)
    assert raw_rumble > 0.9  # it really did dominate
    assert left_rumble < 0.01


def test_a_quiet_bird_is_brought_up_to_a_consistent_level():
    mixture, _ = a_bird(amplitude=0.005)
    cleaned = enhance(mixture, SAMPLERATE)
    assert 0.5 < np.abs(cleaned).max() <= CEILING  # loud, never full scale


def test_a_loud_bird_is_not_pushed_into_clipping():
    mixture, _ = a_bird(amplitude=0.9, noise=0.01, rumble=0.01)
    cleaned = enhance(mixture, SAMPLERATE)
    assert np.abs(cleaned).max() <= CEILING


def test_a_low_voiced_bird_keeps_its_own_band():
    """A wood pigeon lives near 400 Hz — a fixed high-pass tuned for wrens
    would delete it. The band is found per clip, not assumed."""
    mixture, _ = a_bird(freq=420.0, rumble=0.05)
    cleaned = enhance(mixture, SAMPLERATE)
    assert snr(cleaned, freq=420.0) > snr(mixture, freq=420.0) * 5
    # placement matters more than the ratio here: the pigeon's own band must
    # be what survived, not some brighter thing higher up
    assert band_energy(cleaned, 300, 550) > band_energy(cleaned, 1000, 24000)


def test_length_samplerate_and_shape_are_preserved():
    mixture, _ = a_bird()
    cleaned = enhance(mixture, SAMPLERATE)
    assert cleaned.shape == mixture.shape
    assert np.isfinite(cleaned).all()


def test_it_fades_in_and_out_so_playback_does_not_click():
    mixture, _ = a_bird()
    cleaned = enhance(mixture, SAMPLERATE)
    assert abs(cleaned[0]) < 1e-3
    assert abs(cleaned[-1]) < 1e-3


@pytest.mark.parametrize(
    "samples",
    [
        np.zeros(SAMPLERATE),  # digital silence
        np.zeros(10),  # far too short to analyse
        np.array([]),  # nothing at all
    ],
)
def test_degenerate_clips_come_back_untouched(samples):
    """Silence must not be amplified into a wall of hiss, and a clip too short
    to transform is returned rather than refused."""
    assert np.array_equal(enhance(samples, SAMPLERATE), samples)


def test_a_failure_inside_the_cleanup_returns_the_raw_sound(monkeypatch):
    """Fail-soft by contract: the archive would rather hold a raw clip than
    lose the sound behind a painting."""
    import bird_painter.clip_clean as clip_clean

    mixture, _ = a_bird()
    monkeypatch.setattr(
        clip_clean, "_denoise", lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    )
    assert np.array_equal(enhance(mixture, SAMPLERATE), mixture)


def test_nothing_above_the_top_of_the_band_survives():
    """Ultrasonic hiss is pure noise at 48 kHz — it should not ride along."""
    mixture, _ = a_bird()
    cleaned = enhance(mixture, SAMPLERATE)
    total = band_energy(cleaned, 0, 24000)
    assert band_energy(cleaned, BAND_MAX_HZ + 1000, 24000) / total < 1e-3


def test_a_loud_in_band_thump_does_not_win_the_band():
    """Round-1 review of #96: absolute transient energy picked whatever was
    loudest-and-changing, which on all eight real clips was the rumble, not
    the bird. Here a 300 Hz knock is 25x the chirp's amplitude and just as
    transient — the band must still land on the bird."""
    mixture, _ = a_bird(freq=5000.0, amplitude=0.02, thump_hz=300.0, thump=0.5)
    cleaned = enhance(mixture, SAMPLERATE)
    assert snr(cleaned, freq=5000.0) > snr(mixture, freq=5000.0) * 10
    # and the knock's own band is not what survived
    assert band_energy(cleaned, 200, 500) < band_energy(cleaned, 4500, 5500)


def test_a_faint_bird_outranks_a_far_louder_steady_hum():
    """The decision is contrast against a bin's own background, not level: a
    40 dB louder hum that never stops is background, however loud."""
    mixture, _ = a_bird(freq=6000.0, amplitude=0.01)
    t = np.linspace(0, 2.0, len(mixture), endpoint=False)
    mixture = mixture + 0.9 * np.sin(2 * np.pi * 800 * t)  # a very loud mains-ish hum
    cleaned = enhance(mixture, SAMPLERATE)
    assert band_energy(cleaned, 5500, 6500) > band_energy(cleaned, 700, 900)


def a_detection(duty: float, seed: int = 0, freq: float = 4200.0):
    """A clip shaped like the ones this app actually archives: 1.5 s of
    padding, a 3 s BirdNET detection, 1.5 s of padding. `duty` is how much of
    the DETECTION the bird fills — 1.0 means it sings right through it, which
    is the case round-2 review found erased the bird entirely."""
    pad, detection = 1.5, 3.0
    seconds = detection + 2 * pad
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(seconds * SAMPLERATE), endpoint=False)
    first, last = int(pad * SAMPLERATE), int((pad + detection) * SAMPLERATE)
    envelope = np.zeros_like(t)
    length = int(duty * (last - first))
    start = first + ((last - first) - length) // 2
    envelope[start : start + length] = 1.0
    mixture = (
        0.02 * envelope * np.sin(2 * np.pi * freq * t)
        + 0.05 * rng.standard_normal(len(t))
        + 0.25 * np.sin(2 * np.pi * 90 * t)
    )
    return mixture, (first, last)


@pytest.mark.parametrize("duty", [0.2, 0.5, 0.8, 1.0])
def test_the_bird_survives_however_much_of_its_detection_it_fills(duty):
    """Round-2 review of #96: a bird singing through its own detection became
    its own noise profile and was subtracted away, leaving amplified hiss at
    full level — silently, in the only copy. The padding either side is the
    same room without it, so that's what the noise profile is built from."""
    mixture, span = a_detection(duty)
    cleaned = enhance(mixture, SAMPLERATE, bird_span=span)
    bird = band_energy(cleaned, 4000, 4400)
    assert bird / band_energy(cleaned, 0, 24000) > 0.5, f"bird lost at {duty:.0%}"


def test_a_clip_with_no_quiet_moment_at_all_is_archived_raw():
    """The one case nothing can learn from: the bird never stops, not even in
    the padding. Better a raw clip than a confident wall of hiss."""
    t = np.linspace(0, 6.0, 6 * SAMPLERATE, endpoint=False)
    rng = np.random.default_rng(0)
    mixture = (
        0.02 * np.sin(2 * np.pi * 4200 * t)
        + 0.05 * rng.standard_normal(len(t))
        + 0.25 * np.sin(2 * np.pi * 90 * t)
    )
    span = (int(1.5 * SAMPLERATE), int(4.5 * SAMPLERATE))
    assert np.array_equal(enhance(mixture, SAMPLERATE, bird_span=span), mixture)


def test_the_span_is_ignored_when_the_padding_is_too_short_to_learn_from():
    """A degenerate span must not crash or produce a worse clip — it falls
    back to estimating the noise from the mixture."""
    mixture, _ = a_detection(0.5)
    for span in [(0, len(mixture)), (0, 0), (len(mixture), len(mixture))]:
        cleaned = enhance(mixture, SAMPLERATE, bird_span=span)
        assert np.isfinite(cleaned).all()
        assert cleaned.shape == mixture.shape


def test_frames_are_placed_where_the_transform_actually_puts_them():
    """Round-3 review of #96: frame centres were computed as `p*HOP + FRAME/2`,
    but scipy's ShortTimeFFT starts at p_min = -1, so the true centre is
    `p*HOP - HOP`. Besides a 16 ms skew, the old maths invented frames past
    the end of the clip — which a span covering the whole clip then accepted
    as "padding", making the noise profile the transform's own zero-padding
    and band-limiting to the one place the bird isn't."""
    from bird_painter.clip_clean import _frame_roles

    samples = 6 * SAMPLERATE
    frames = samples // 256 + 8  # a few more than exist, as scipy reports
    bird, noise = _frame_roles(frames, samples, (0, samples))
    # A span covering everything leaves no padding to learn from, so the
    # statistical path must take over rather than inventing quiet frames.
    assert bird is None and noise is None

    bird, noise = _frame_roles(frames, samples, (2 * SAMPLERATE, 4 * SAMPLERATE))
    assert bird is not None and noise is not None
    # No frame is claimed beyond the clip's own samples.
    centres = np.arange(frames) * 256 - 256
    assert not (bird | noise)[(centres < 0) | (centres >= samples)].any()


def test_a_bird_filling_a_short_clip_is_not_band_limited_to_silence():
    """The same bug, end to end: a short analysis window (BP_ANALYSIS_WINDOW_
    SECONDS is a documented knob) leaves little padding, and the phantom
    frames made the cleanup pick a band with no bird in it."""
    mixture, _ = a_detection(1.0)
    cleaned = enhance(mixture, SAMPLERATE, bird_span=(0, len(mixture)))
    assert band_energy(cleaned, 4000, 4400) / band_energy(cleaned, 0, 24000) > 0.5
