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
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # scipy is heavy; the import stays inside _stft at runtime
    from scipy.signal import ShortTimeFFT

logger = logging.getLogger(__name__)

# STFT resolution: ~21 ms frames at 48 kHz — short enough to keep a chirp's
# shape, long enough to separate bands.
FRAME = 1024
HOP = 256

# The noise profile is this percentile of each bin's magnitude over time.
# Low enough that a bird singing through most of the clip doesn't become its
# own noise profile — measured: 85% duty costs it 0.3 dB — while still
# tracking noise that drifts.
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
# A bin is still "the bird" while it stands this far from the background
# towards the peak (0 = background, 1 = the peak itself); the band is then
# padded half an octave each way so the song keeps its harmonics and its air.
BAND_THRESHOLD = 0.35
BAND_PAD_OCTAVES = 0.5
# How much of the clip a bin has to stand out for to count as the bird: the
# 90th percentile of its contrast, so a sustained note beats a knock.
CONTRAST_PERCENTILE = 90.0
# Frames ignored at each end of the clip — the analysis window's zero-padding
# invents an onset and an offset there.
EDGE_FRAMES = 4

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
# Below this, a signal is silence rather than a quiet bird: amplifying it
# would just make a wall of hiss. One number, used at every stage that asks
# the question.
SILENT = 1e-6


def _stft(samples: np.ndarray) -> tuple[np.ndarray, ShortTimeFFT]:
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

    Found per clip, from the bin with the strongest CONTRAST — how far it
    rises above its own steady level — then grown outwards while its
    neighbours are still part of the same sound, and padded half an octave
    each way so a song keeps the harmonics that stop it sounding like a
    whistle.

    Contrast, not loudness, and not raw transient energy either. Loudness
    finds the traffic. Absolute transient energy also finds the traffic —
    rumble fluctuates, and 40 dB of fluctuating rumble beats a faint bird.
    What actually marks a bird is standing far above its OWN background,
    which is scale-free: a whisper at 6 kHz can outrank a lorry at 300 Hz.
    (Measured on the archive: absolute transient energy pinned all 8 real
    clips to the 200 Hz floor; contrast spreads them across 234 Hz–13 kHz.)
    """
    freqs = np.fft.rfftfreq(FRAME, d=1.0 / samplerate)
    magnitude = np.abs(spectrum)
    # Drop the frames at each end: the analysis window pads with zeros there,
    # so even a perfectly steady hum shows a fake onset and offset — enough to
    # win the band on contrast alone.
    if magnitude.shape[1] > 2 * EDGE_FRAMES + 8:
        magnitude = magnitude[:, EDGE_FRAMES:-EDGE_FRAMES]
    steady = np.median(magnitude, axis=1, keepdims=True)
    contrast = np.maximum(magnitude - steady, 0.0) / np.maximum(steady, 1e-12)
    # A high percentile rather than a sum: summing rewards one enormous spike,
    # which is how a door slam or a knock on the mic stand wins. Asking "how
    # far above its own background does this bin sit for a decent share of the
    # clip" needs the sound to LAST, which a bird's note does and a knock
    # doesn't.
    energy = np.percentile(contrast, CONTRAST_PERCENTILE, axis=1)
    audible = (freqs >= BAND_MIN_HZ) & (freqs <= BAND_MAX_HZ)
    if not audible.any() or energy[audible].max() <= 0:
        return audible
    # Grow contiguously from the peak. Taking the min and max of every strong
    # bin instead would let the skirt of a passing lorry — only just inside
    # the audible range — drag the band's floor down over the whole clip.
    peak = int(np.argmax(np.where(audible, energy, 0.0)))
    # Measured against the background level of the metric itself, not against
    # zero: hiss has contrast in every bin, so a plain fraction-of-peak
    # threshold is met almost everywhere and the "band" becomes the whole
    # spectrum. Growth stops where the bin stops standing out from its peers.
    background = float(np.median(energy[audible]))
    threshold = background + BAND_THRESHOLD * (energy[peak] - background)

    def edge(step: int) -> int:
        """Walk from the peak in one direction while the sound continues."""
        bin_index = peak
        while 0 <= bin_index + step < len(freqs):
            if not audible[bin_index + step] or energy[bin_index + step] < threshold:
                break
            bin_index += step
        return bin_index

    low = max(freqs[edge(-1)] / (2**BAND_PAD_OCTAVES), BAND_MIN_HZ)
    high = min(freqs[edge(+1)] * (2**BAND_PAD_OCTAVES), BAND_MAX_HZ)
    return (freqs >= low) & (freqs <= high)


def _level(samples: np.ndarray, samplerate: int) -> np.ndarray:
    """Bring the clip up to a consistent level, limit what that pushes over,
    and fade the ends so playback doesn't click. (Normalising is only the
    first of the three, hence the plainer name.)"""
    reference = np.percentile(np.abs(samples), LEVEL_PERCENTILE)
    if reference <= SILENT:  # leave silence alone rather than amplify hiss
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
    if not np.any(np.abs(samples) > SILENT):  # nothing but digital silence
        return samples
    try:
        spectrum, transform = _stft(samples)
        spectrum = _denoise(spectrum)
        spectrum[~_band_mask(spectrum, samplerate)] = 0.0
        cleaned = transform.istft(spectrum, k1=len(samples))
        # istft returns exactly k1 samples for this window/hop pair (checked:
        # round-trip error 1.7e-16, no lag), so this is a slice, not a fix.
        cleaned = np.nan_to_num(cleaned[: len(samples)], nan=0.0)
        if not np.any(np.abs(cleaned) > SILENT):
            # The clip was all noise floor and nothing survived the band. The
            # raw sound is more honest than silence.
            return samples
        return _level(cleaned, samplerate)
    except Exception:  # noqa: BLE001 — never cost the painting its sound
        logger.exception("clip cleanup failed; archiving the raw sound")
        return samples
