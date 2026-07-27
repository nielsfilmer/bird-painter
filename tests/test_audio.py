"""The detection clip encoder (`bird_painter/audio.py`) and the audio side of
the store: clip bounds, WAV validity, storage beside the painting, and the
serving guards."""

import io
import wave

import numpy as np

from bird_painter.audio import detection_clip_wav
from tests.conftest import add_painting


def _decode(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        return w.getframerate(), w.getnframes()


def test_clip_covers_detection_plus_padding():
    rate = 48000
    window = np.zeros(15 * rate, dtype="float32")
    wav = detection_clip_wav(window, rate, start_seconds=5.0, end_seconds=8.0)
    got_rate, frames = _decode(wav)
    assert got_rate == rate
    # 3s detection + 1.5s pad each side = 6s.
    assert abs(frames / rate - 6.0) < 0.01


def test_clip_clamps_to_window_edges():
    rate = 48000
    window = np.zeros(15 * rate, dtype="float32")
    # Detection at the very start: the left pad has nowhere to go.
    wav = detection_clip_wav(window, rate, start_seconds=0.0, end_seconds=3.0)
    _, frames = _decode(wav)
    assert abs(frames / rate - 4.5) < 0.01  # 3s + right pad only


def test_degenerate_bounds_fall_back_to_whole_window():
    rate = 48000
    window = np.zeros(2 * rate, dtype="float32")
    wav = detection_clip_wav(window, rate, start_seconds=9.0, end_seconds=9.0)
    _, frames = _decode(wav)
    assert frames == len(window)


def test_clip_survives_out_of_range_samples():
    rate = 48000
    window = np.full(rate, 1.7, dtype="float32")  # beyond [-1, 1]
    wav = detection_clip_wav(window, rate, 0.0, 1.0)
    _decode(wav)  # decodes without overflow errors


def test_store_writes_and_serves_the_clip(store):
    painting = store.add(
        image_bytes=b"<svg/>",
        extension="svg",
        species_common="European Robin",
        species_scientific="Erithacus rubecula",
        confidence=0.9,
        source="detection",
        audio_bytes=b"RIFFfake",
    )
    clip = store.audio_file_for(painting.file)
    assert clip is not None and clip.endswith(".wav")
    assert store.audio_path(clip) is not None
    assert store.audio_path(clip).read_bytes() == b"RIFFfake"


def test_paintings_without_audio_have_none(store):
    painting = add_painting(store)
    assert store.audio_file_for(painting.file) is None


def test_audio_path_refuses_traversal_and_non_wav(store):
    painting = store.add(
        image_bytes=b"x",
        extension="svg",
        species_common="Wren",
        species_scientific="T. troglodytes",
        confidence=0.9,
        source="detection",
        audio_bytes=b"wav",
    )
    clip = store.audio_file_for(painting.file)
    assert store.audio_path(f"../{clip}") is None
    assert store.audio_path("meta.jsonl") is None
    assert store.audio_path(painting.file) is None  # the image isn't audio
