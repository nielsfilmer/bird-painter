from bird_painter.brush import UNKNOWN_SCIENTIFIC, build_prompt, paint


def test_prompt_includes_both_names_when_known():
    prompt = build_prompt("European Robin", "Erithacus rubecula")
    assert "European Robin (Erithacus rubecula)" in prompt


def test_prompt_omits_unknown_scientific_name():
    prompt = build_prompt("Great Tit", UNKNOWN_SCIENTIFIC)
    assert "Great Tit" in prompt
    assert UNKNOWN_SCIENTIFIC not in prompt


def test_missing_key_is_a_soft_failure():
    assert paint("Robin", UNKNOWN_SCIENTIFIC, fal_key="") is None
