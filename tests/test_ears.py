from bird_painter.ears import NON_BIRD_COMMON_NAMES, is_bird


def test_real_birds_pass():
    for name in ("European Robin", "Great Tit", "Song Thrush", "Northern Cardinal"):
        assert is_bird(name)


def test_noise_and_human_and_machine_classes_are_dropped():
    for name in (
        "Gun",
        "Engine",
        "Siren",
        "Fireworks",
        "Dog",
        "Noise",
        "Power tools",
        "Human non-vocal",
        "Human vocal",
        "Human whistle",
        "Environmental",
    ):
        assert not is_bird(name)


def test_insect_classes_are_dropped():
    assert not is_bird("Gryllus assimilis")
    assert not is_bird("Miogryllus saussurei")


def test_match_is_case_and_whitespace_insensitive():
    assert not is_bird("  gun ")
    assert not is_bird("HUMAN NON-VOCAL")


def test_denylist_names_are_normalized_lowercase():
    # is_bird lowercases its input, so the set must hold lowercase to match.
    assert all(name == name.lower() for name in NON_BIRD_COMMON_NAMES)
