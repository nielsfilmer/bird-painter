import datetime
import importlib.util
import os
from pathlib import Path

import pytest

from bird_painter.ears import NON_BIRD_SCIENTIFIC, Ears, _silence_load, is_bird


def _ears_without_model(latitude=None, longitude=None, seasonal=False):
    """An Ears with its location fields set but WITHOUT loading BirdNET (the
    real __init__ constructs a heavy TF-Lite Analyzer). Exercises the pure
    location-kwargs logic in isolation."""
    ears = Ears.__new__(Ears)
    ears.confidence_floor = 0.6
    ears.latitude = latitude
    ears.longitude = longitude
    ears.seasonal = seasonal
    return ears


def test_location_kwargs_empty_when_no_location():
    assert _ears_without_model()._location_kwargs() == {}


def test_location_kwargs_carries_lat_lon_but_no_date_by_default():
    """Place, not calendar. birdnetlib leaves week_48 at -1 without a date,
    which BirdNET reads as "any week" — so a bird singing outside its expected
    weeks is still heard. (2026-08-05: with the season on, a nightingale
    identified at 0.87 confidence was dropped silently, which from the console
    is indistinguishable from a dead microphone.)"""
    ears = _ears_without_model(latitude=52.37, longitude=4.90)
    kwargs = ears._location_kwargs()
    assert kwargs["lat"] == 52.37
    assert kwargs["lon"] == 4.90
    assert "date" not in kwargs


def test_location_kwargs_adds_the_date_when_the_season_filter_is_on():
    ears = _ears_without_model(latitude=52.37, longitude=4.90, seasonal=True)
    assert ears._location_kwargs()["date"] == datetime.date.today()


def test_birdnets_calendar_is_48_weeks_not_52():
    """A characterisation test of the installed library, pinning the fact that
    tripped up #99: BirdNET counts 48 weeks, so the obvious `day_of_year // 7`
    is wrong for most of the year. This documents the library; the guard on
    OUR code is the seasonal-week test below."""
    from birdnetlib.utils import return_week_48_from_datetime

    for day in (
        datetime.date(2026, 1, 1),
        datetime.date(2026, 4, 15),
        datetime.date(2026, 8, 5),
        datetime.date(2026, 12, 31),
    ):
        assert 1 <= return_week_48_from_datetime(day) <= 48
    # The specific day the shipped bug got wrong: `// 7 + 1` said 32.
    assert return_week_48_from_datetime(datetime.date(2026, 8, 5)) == 29


def test_counting_the_allowed_species_is_only_a_diagnostic():
    """It feeds a startup log line; it must never be what breaks the wall."""
    assert _ears_without_model().allowed_species_count() is None

    ears = _ears_without_model(latitude=52.37, longitude=4.90)

    class Exploding:
        def return_predicted_species_list(self, **kwargs):
            raise RuntimeError("meta model unavailable")

    ears._analyzer = Exploding()
    assert ears.allowed_species_count() is None


def test_counting_species_asks_without_changing_the_recognizer():
    """Answering a question must not install a species list on the shared
    analyzer — `return_predicted_species_list` returns one,
    `set_predicted_species_list_from_position` installs one, and the listener
    asks BEFORE its first analysis."""
    ears = _ears_without_model(latitude=52.37, longitude=4.90)

    class Recording:
        def __init__(self):
            self.asked = []
            self.custom_species_list = ["untouched"]

        def return_predicted_species_list(self, **kwargs):
            self.asked.append(kwargs)
            return ["a", "b", "c"]

    analyzer = Recording()
    ears._analyzer = analyzer
    assert ears.allowed_species_count() == 3
    assert analyzer.custom_species_list == ["untouched"]
    assert analyzer.asked[0]["week_48"] == -1  # place only: any week


def test_counting_species_passes_the_week_when_the_season_filter_is_on(monkeypatch):
    """Pinned to a fixed date, not to today: the bug this guards against
    (`day_of_year // 7 + 1`) happens to agree with the library on about an
    eighth of the year, so a test run on the wrong day would pass with the bug
    reinstated. 5 August is one of the days where they differ — 29 vs 32."""
    import bird_painter.ears as ears_module
    from birdnetlib.utils import return_week_48_from_datetime

    class FixedDate(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 5)

    monkeypatch.setattr(ears_module.datetime, "date", FixedDate)
    ears = _ears_without_model(latitude=52.37, longitude=4.90, seasonal=True)

    class Analyzer:
        def __init__(self):
            self.asked = []

        def return_predicted_species_list(self, **kwargs):
            self.asked.append(kwargs)
            return []

    analyzer = Analyzer()
    ears._analyzer = analyzer
    ears.allowed_species_count()
    expected = return_week_48_from_datetime(datetime.date(2026, 8, 5))
    assert expected == 29  # the library's answer, not ours
    assert analyzer.asked[0]["week_48"] == expected


def test_location_kwargs_nudges_bare_zero_so_filter_still_engages():
    # birdnetlib gates the filter on `lon and lat` truthiness, so an exact 0.0
    # (equator / prime meridian) would silently disable it. The nudge keeps it
    # non-zero and sub-metre-close.
    ears = _ears_without_model(latitude=0.0, longitude=0.0)
    kwargs = ears._location_kwargs()
    assert kwargs["lat"] != 0
    assert kwargs["lon"] != 0
    assert abs(kwargs["lat"]) < 1e-4
    assert abs(kwargs["lon"]) < 1e-4


def test_real_birds_pass():
    for sci in (
        "Erithacus rubecula",       # European Robin
        "Parus major",              # Great Tit
        "Turdus philomelos",        # Song Thrush
        "Cardinalis cardinalis",    # Northern Cardinal
        # common-name traps that ARE birds
        "Piaya cayana",             # Squirrel Cuckoo
        "Spiloptila clamans",       # Cricket Longtail
        "Podargus strigoides",      # Tawny Frogmouth
        "Edolisoma tenuirostre",    # Common Cicadabird
    ):
        assert is_bird(sci)


def test_pseudo_and_animal_classes_are_dropped():
    for sci in (
        "Gun", "Engine", "Siren", "Human non-vocal", "Noise",  # machine/human
        "Canis latrans",              # Coyote
        "Lithobates catesbeianus",    # American Bullfrog
        "Pseudacris crucifer",        # Spring Peeper
        "Gryllus assimilis",          # a field cricket
        "Sciurus carolinensis",       # Eastern Gray Squirrel
    ):
        assert not is_bird(sci)


def test_match_is_whitespace_insensitive():
    assert not is_bird("  Gun ")
    assert not is_bird(" Canis latrans ")


def _label_file() -> Path:
    """The BirdNET label file shipped inside birdnetlib (found without
    importing the package, so no tensorflow load)."""
    spec = importlib.util.find_spec("birdnetlib")
    assert spec and spec.origin
    return (
        Path(spec.origin).parent
        / "models/analyzer/BirdNET_GLOBAL_6K_V2.4_Labels.txt"
    )


# Genera BirdNET_GLOBAL_6K_V2.4 carries that are NOT birds (frogs/toads,
# orthopterans, mammals). Kept here in the test — the drift guard's job is to
# prove NON_BIRD_SCIENTIFIC still equals what this derivation finds.
_NON_BIRD_GENERA = {
    "Acris", "Anaxyrus", "Dryophytes", "Hyliola", "Lithobates", "Pseudacris",
    "Scaphiopus", "Gastrophryne", "Incilius", "Eleutherodactylus", "Spea",
    "Oecanthus", "Gryllus", "Miogryllus", "Scudderia", "Neoconocephalus",
    "Conocephalus", "Orocharis", "Anaxipha", "Eunemobius", "Allonemobius",
    "Amblycorypha", "Microcentrum", "Pterophylla", "Atlanticus", "Neonemobius",
    "Cyrtoxipha", "Phyllopalpus", "Orchelimum", "Canis", "Odocoileus",
    "Tamiasciurus", "Sciurus", "Tamias",
}


@pytest.mark.skipif(
    not _label_file().exists(), reason="birdnetlib label file not installed"
)
def test_denylist_matches_the_shipped_label_file():
    """Drift guard: if a birdnetlib bump adds/removes a non-bird label, this
    fails so the denylist gets updated instead of silently painting frogs."""
    rows = [
        tuple(line.split("_", 1))
        for line in _label_file().read_text(encoding="utf-8").splitlines()
        if "_" in line
    ]
    derived = {
        sci
        for sci, common in rows
        if sci.split()[0] in _NON_BIRD_GENERA or sci.strip() == common.strip()
    }
    assert derived == set(NON_BIRD_SCIENTIFIC)


@pytest.mark.skipif(
    not _label_file().exists(), reason="birdnetlib label file not installed"
)
def test_no_non_bird_animal_slips_past_the_denylist():
    """Completeness audit: every label whose common name reads like a non-bird
    animal is either denylisted or a known bird whose name merely borrows the
    word (frogmouth, mousebird, squirrel cuckoo…)."""
    keywords = (
        "frog", "toad", "cricket", "katydid", "cicada", "squirrel", "deer",
        "wolf", "coyote", "chipmunk", "spadefoot", "peeper", "bullfrog",
        "conehead", "treefrog",
    )
    trap_bird_substrings = (
        "frogmouth", "cicadabird", "squirrel cuckoo", "cricket longtail",
        "killdeer",  # Charadrius vociferus — a bird, not a deer
    )
    rows = [
        tuple(line.split("_", 1))
        for line in _label_file().read_text(encoding="utf-8").splitlines()
        if "_" in line
    ]
    leaked = [
        (sci, common)
        for sci, common in rows
        if any(k in common.lower() for k in keywords)
        and sci not in NON_BIRD_SCIENTIFIC
        and not any(t in common.lower() for t in trap_bird_substrings)
    ]
    assert leaked == []



def test_silence_load_swallows_raw_fd_writes_then_restores(capfd):
    # os.write goes straight to the fd (like TF Lite's C++ XNNPACK line),
    # bypassing Python buffering — the exact thing _silence_load must catch.
    with _silence_load():
        os.write(1, b"SWALLOWED-STDOUT\n")
        os.write(2, b"SWALLOWED-STDERR\n")
    os.write(1, b"VISIBLE-AFTER\n")
    out, err = capfd.readouterr()
    assert "SWALLOWED" not in out
    assert "SWALLOWED" not in err
    assert "VISIBLE-AFTER" in out


def test_silence_load_restores_fds_even_on_exception(capfd):
    with pytest.raises(RuntimeError):
        with _silence_load():
            raise RuntimeError("boom")
    os.write(1, b"RESTORED\n")
    out, _ = capfd.readouterr()
    assert "RESTORED" in out
