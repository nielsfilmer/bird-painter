"""Make the archived detection clip actually listenable.

A raw 48 kHz window from a mic pointed out of a window is mostly *not* bird:
traffic rumble, wind, the fridge, a plane. The bird is a few hundred
milliseconds of energy in a narrow band, often well below the noise in
loudness. Played back on the wall it can be nearly inaudible.

So the clip is cleaned before archiving, in the order that matters:

1. **Spectral subtraction** against a noise profile the clip supplies itself —
   the quietest fifth of frames per frequency bin. Steady noise (rumble, hum,
   hiss) sits in every frame; a bird doesn't.
2. **Band-limiting to the bird's own band**, found from what's left rather
   than assumed: a wren lives around 5 kHz, a wood pigeon near 400 Hz, so a
   fixed high-pass would either keep the traffic or delete the pigeon.
3. **Normalising** to a consistent, loud-but-unclipped level, with a soft
   limiter so one wing-flap transient doesn't leave the rest quiet.

Everything is fail-soft by contract: `enhance` returns the original samples
if anything about them defeats it (silence, a clip too short to analyse, a
numerical surprise). The archive would rather hold a raw clip than none.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# STFT resolution: ~21 ms frames at 48 kHz — short enough to keep a chirp's
# shape, long enough to separate bands.
FRAME = 1024
HOP = 256

# The noise profile is this percentile of each bin's magnitude over time.
# Low enough that a bird singing through most of the clip doesn't become
# "noise"; high enough to catch noise that fluctuates.
NOISE_PERCENTILE = 20.0
# How hard to pull the estimated noise down. Above 1.0 over-subtracts, which
# is what makes a hiss actually disappear rather than merely dip.
OVER_SUBTRACTION = 1.8
# Never attenuate a bin below this fraction of its original magnitude:
# subtracting all the way to zero is what makes denoisers warble ("musical
# noise"), and a little residue sounds far more natural than a gate.
SPECTRAL_FLOOR = 0.06

# The widest band a bird here could occupy. Below this is traffic, wind and
# things knocked against the mic stand — all of them transient enough to fool
# the band finder, and none of them a bird: the lowest voices around here
# (wood pigeon, tawny owl) sit at 250–500 Hz. Above the top, essentially
# nothing but hiss at 48 kHz.
BAND_MIN_HZ = 200.0
BAND_MAX_HZ = 14000.0
# A bin counts as "the bird" if it holds at least this fraction of the peak
# bin's energy; the band is then padded by half an octave each way so the
# song keeps its harmonics and its air.
BAND_THRESHOLD = 0.08
BAND_PAD_OCTAVES = 0.5

# Target level. Peak-normalising to 1.0 invites inter-sample clipping on
# playback; -1 dBFS is the usual compromise.
TARGET_PEAK = 0.89
# Absolute ceiling the limiter approaches but never reaches, so nothing lands
# at full scale where a player's resampling could push it over.
CEILING = 0.97
# The level is set from this percentile rather than the true peak, so a single
# click doesn't decide how loud the bird is; the limiter then catches whatever
# that pushes over.
LEVEL_PERCENTILE = 99.5
FADE_SECONDS = 0.01


def _stft(samples: np.ndarray) -> tuple[np.ndarray, object]:
    from scipy.signal import ShortTimeFFT
    from scipy.signal.windows import hann

    transform = ShortTimeFFT(hann(FRAME, sym=False), hop=HOP, fs=1.0, mfft=FRAME)
    return transform.stft(samples), transform


def _denoise(spectrum: np.ndarray) -> np.ndarray:
    """Spectral subtraction with a floor, phase untouched."""
    magnitude = np.abs(spectrum)
    noise = np.percentile(magnitude, NOISE_PERCENTILE, axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        gain = 1.0 - OVER_SUBTRACTION * noise / np.maximum(magnitude, 1e-12)
    return spectrum * np.clip(gain, SPECTRAL_FLOOR, 1.0)


def _band_mask(spectrum: np.ndarray, samplerate: int) -> np.ndarray:
    """Which frequency bins the bird actually occupies.

    Derived from the denoised clip itself: the loudest bin decides the
    neighbourhood, everything within BAND_THRESHOLD of it extends the band,
    and half an octave of padding keeps the harmonics that make a song sound
    like a bird instead of a whistle.
    """
    freqs = np.fft.rfftfreq(FRAME, d=1.0 / samplerate)
    # What matters is not which bin is loudest — that's the traffic — but which
    # bin CHANGES. A lorry holds the same 90 Hz for the whole clip; a bird is
    # an event. Measuring each bin against its own median over time finds the
    # song even when the rumble is twenty times louder.
    magnitude = np.abs(spectrum)
    steady = np.median(magnitude, axis=1, keepdims=True)
    energy = (np.maximum(magnitude - steady, 0.0) ** 2).sum(axis=1)
    audible = (freqs >= BAND_MIN_HZ) & (freqs <= BAND_MAX_HZ)
    if not audible.any() or energy[audible].max() <= 0:
        return audible
    # Grow outwards from the loudest bin while its neighbours are still part
    # of the same sound. Taking the min and max of every strong bin instead
    # would let the skirt of a passing lorry — loud, and only just inside the
    # audible range — drag the band's floor down over the whole clip.
    loud = np.where(audible, energy, 0.0)
    peak = int(np.argmax(loud))
    threshold = BAND_THRESHOLD * loud[peak]
    low_bin = peak
    while low_bin > 0 and audible[low_bin - 1] and energy[low_bin - 1] >= threshold:
        low_bin -= 1
    high_bin = peak
    last = len(freqs) - 1
    while (
        high_bin < last and audible[high_bin + 1] and energy[high_bin + 1] >= threshold
    ):
        high_bin += 1
    low = max(freqs[low_bin] / (2**BAND_PAD_OCTAVES), BAND_MIN_HZ)
    high = min(freqs[high_bin] * (2**BAND_PAD_OCTAVES), BAND_MAX_HZ)
    return (freqs >= low) & (freqs <= high)


def _normalise(samples: np.ndarray, samplerate: int) -> np.ndarray:
    """Bring the clip up to a consistent level, softly."""
    reference = np.percentile(np.abs(samples), LEVEL_PERCENTILE)
    if reference <= 1e-6:  # silence: leave it alone rather than amplify hiss
        return samples
    limited = samples * (TARGET_PEAK / reference)
    # Only the peaks the percentile underestimated get bent, and they get bent
    # softly. Running everything through a tanh would be simpler, but it's a
    # nonlinearity: it sprays harmonics of the bird's own note across the
    # spectrum, undoing the band-limiting two steps above.
    over = np.abs(limited) > TARGET_PEAK
    if over.any():
        excess = np.abs(limited[over]) - TARGET_PEAK
        headroom = CEILING - TARGET_PEAK
        limited[over] = np.sign(limited[over]) * (
            TARGET_PEAK + headroom * np.tanh(excess / headroom)
        )
    fade = max(1, int(FADE_SECONDS * samplerate))
    if 2 * fade < len(limited):
        ramp = np.linspace(0.0, 1.0, fade, dtype=limited.dtype)
        limited[:fade] *= ramp
        limited[-fade:] *= ramp[::-1]
    return limited


def enhance(samples: np.ndarray, samplerate: int) -> np.ndarray:
    """Clean and level a detection clip. Returns the input unchanged if it
    can't be improved — a clip in the archive beats a traceback in the mic
    thread."""
    samples = np.asarray(samples, dtype=np.float64)
    if samples.ndim != 1 or len(samples) < FRAME * 2:
        return samples
    if not np.any(np.abs(samples) > 1e-6):  # nothing but digital silence
        return samples
    try:
        spectrum, transform = _stft(samples)
        spectrum = _denoise(spectrum)
        spectrum[~_band_mask(spectrum, samplerate)] = 0.0
        cleaned = transform.istft(spectrum, k1=len(samples))
        cleaned = np.nan_to_num(cleaned[: len(samples)], nan=0.0)
        if len(cleaned) < len(samples):  # istft can end a hair short
            cleaned = np.pad(cleaned, (0, len(samples) - len(cleaned)))
        if not np.any(np.abs(cleaned) > 1e-9):
            # The clip was all noise floor and nothing survived the band. The
            # raw sound is more honest than silence.
            return samples
        return _normalise(cleaned, samplerate)
    except Exception:  # noqa: BLE001 — never cost the painting its sound
        logger.exception("clip cleanup failed; archiving the raw sound")
        return samples
