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
) -> tuple[np.ndarray, np.ndarray]:
    """A quiet chirp buried in the kind of noise a window mic actually picks
    up: broadband hiss plus heavy low-frequency traffic rumble. Returns
    (mixture, the chirp alone) so tests can measure what survived."""
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
    return bird + hiss + traffic, bird


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
    assert snr(cleaned, freq=420.0) > snr(mixture, freq=420.0) * 20
    assert band_energy(cleaned, 300, 550) > 0


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
    assert band_energy(cleaned, BAND_MAX_HZ + 1000, 24000) / total < 1e-4
