"""The painting styles (`bird_painter/styles.py`) and how they reach the
brush, the config, the unit's settings and the running service."""

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bird_painter import brush, unit
from bird_painter.config import Config, ConfigError, load_config
from bird_painter.ears import Detection
from bird_painter.styles import (
    DEFAULT_STYLE,
    STYLES,
    is_style,
    style_choices,
    style_for,
)
from bird_painter.web import create_app
from tests.conftest import LOCAL


def test_the_table_has_the_asked_for_looks_and_a_weird_one():
    keys = [s.key for s in STYLES]
    assert keys[0] == DEFAULT_STYLE == "naturalist"
    assert len(keys) == len(set(keys)) and 6 <= len(keys) <= 8
    assert "sumi" in keys, "Japanese watercolour was asked for by name"
    assert "cubist" in keys, "and a weird one"
    for s in STYLES:
        assert s.look and s.palette and s.name
        assert "{" not in s.look and "{" not in s.palette  # spliced, not formatted
    assert style_choices()[1] == {"key": "sumi", "name": "Japanese watercolour"}


def test_an_unknown_style_falls_back_to_the_house_look():
    assert style_for(None).key == DEFAULT_STYLE
    assert style_for("").key == DEFAULT_STYLE
    assert style_for("  Sumi ").key == "sumi"
    assert style_for("banksy").key == DEFAULT_STYLE
    assert is_style("cubist") and not is_style("Cubist")


@pytest.mark.parametrize("style", [s.key for s in STYLES])
def test_the_prompt_takes_the_styles_look_and_keeps_the_house_rule(style):
    prompt = brush.build_prompt("Eurasian Wren", "Troglodytes troglodytes", style=style)
    chosen = style_for(style)
    assert chosen.look in prompt and chosen.palette in prompt
    assert prompt.startswith("A single Eurasian Wren (Troglodytes troglodytes) bird, ")
    # What the wall's blend and the plate check depend on, every style.
    for rule in (
        "pure flat bright white",
        "No text",
        "no paper texture",
        "just the bird itself",
    ):
        assert rule in prompt, rule
    # The occasion hat still splices in after the pose, before the tail.
    hatted = brush.build_prompt(
        "Robin", "Erithacus rubecula", hat="wearing a tiny party hat", style=style
    )
    assert "perched in full side view, wearing a tiny party hat" in hatted


# The prompt the wall has always painted with, word for word (main before
# #162). Pinned so a style edit can't drift the default.
ORIGINAL_PROMPT = (
    "A single Robin (Erithacus rubecula) bird, hand-painted naturalist watercolor, "
    "the whole bird perched in full side view, soft muted natural colors, fine "
    "feather detail, cleanly isolated and centred on a pure flat bright white "
    "background, the bird is the only thing in the image. No text, no words, no "
    "letters, no caption, no label, no numbers, no signature, no watermark, no "
    "border, no frame, no paper texture, no vignette, no scenery, no background "
    "objects. Not a photograph of a painting: no sheet of paper, no desk or "
    "table, no pencils, brushes or art supplies, no hands, no sketchbook, no "
    "plain coloured blocks or panels — just the bird itself, centred with clear "
    "white space all around it."
)


def test_the_default_prompt_is_the_one_the_wall_always_had():
    assert brush.build_prompt("Robin", "Erithacus rubecula") == ORIGINAL_PROMPT
    assert (
        brush.build_prompt("Robin", "Erithacus rubecula", style="naturalist")
        == ORIGINAL_PROMPT
    )


def test_no_style_names_the_artefacts_the_prompt_bans():
    """Words FLUX takes literally: it paints the object, and the plate check
    lists a baked-in caption and a photographed print among its misses."""
    for s in STYLES:
        text = f"{s.look} {s.palette}".lower()
        for word in (
            "poster",
            "print",
            "notebook",
            "paper",
            "engraving",
            "field-guide",
            "off-white",
        ):
            assert word not in text, (s.key, word)


def test_config_validates_bp_style(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("BP_STYLE", "dutch")
    assert load_config().style == "dutch"
    monkeypatch.setenv("BP_STYLE", "graffiti")
    with pytest.raises(ConfigError, match="BP_STYLE must be one of"):
        load_config()
    monkeypatch.delenv("BP_STYLE")
    assert load_config().style == DEFAULT_STYLE


def test_the_unit_keeps_its_own_style_in_unit_conf(tmp_path: Path):
    conf = tmp_path / "unit.conf"
    conf.write_text("CAPTION=1.5\nSTYLE=linocut\n")
    config = Config(
        archive_dir=tmp_path / "a", enable_listener=False, night_enabled=False
    )
    live = unit.LiveSettings.from_config(config, conf)
    assert live.style == "linocut" and live.as_json()["STYLE"] == "linocut"
    # A stale or misspelt line paints the house look, not nothing.
    conf.write_text("STYLE=vaporwave\n")
    assert unit.LiveSettings.from_config(config, conf).style == DEFAULT_STYLE
    # BP_STYLE is the fallback when unit.conf says nothing.
    conf.write_text("CAPTION=1.5\n")
    cfg2 = dataclasses.replace(config, style="sumi")
    assert unit.LiveSettings.from_config(cfg2, conf).style == "sumi"
    assert unit.clean_updates({"STYLE": "cubist", "CAPTION": 1.2}) == {
        "STYLE": "cubist",
        "CAPTION": 1.2,
    }
    assert unit.clean_updates({"STYLE": "Cubist"}) == {}
    assert unit.clean_updates({"STYLE": 7}) == {}
    env = tmp_path / ".env"
    env.write_text("FAL_KEY=secret\n")
    unit.apply({"STYLE": "cubist"}, live, conf_path=conf, env_path=env)
    assert unit.read_conf(conf)["STYLE"] == "cubist" and live.style == "cubist"
    assert unit.read_conf(env) == {
        "FAL_KEY": "secret"
    }  # the style is the unit's, not .env's


def test_a_style_set_on_the_screen_reaches_the_next_painting(
    config, tmp_path, monkeypatch
):
    conf, env = tmp_path / "unit.conf", tmp_path / ".env"
    conf.write_text("CAPTION=1\n")
    env.write_text("FAL_KEY=k\n")
    monkeypatch.setattr(unit, "UNIT_CONF", conf)
    monkeypatch.setattr(unit, "ENV_FILE", env)
    monkeypatch.setattr(unit, "connectivity", lambda rescan=False: unit.Connectivity())
    prompts = []

    def fake_paint_once(
        species_common, species_scientific, *, fal_key, model, hat, style=None
    ):
        prompts.append(
            brush.build_prompt(species_common, species_scientific, hat, style)
        )
        return (b"\x89PNG", "png")

    monkeypatch.setattr(brush, "_paint_once", fake_paint_once)
    monkeypatch.setattr(brush, "describe_problem", lambda *a, **k: None)
    keyed = dataclasses.replace(config, fal_key="k")
    app = create_app(keyed)
    with TestClient(app, client=LOCAL) as local:
        state = local.get("/unit").json()
        assert state["settings"]["STYLE"] == DEFAULT_STYLE
        assert [s["key"] for s in state["styles"]] == [s.key for s in STYLES]
        local.post("/dev/paint/Robin")
        assert (
            local.put("/unit", json={"STYLE": "sumi"}).json()["settings"]["STYLE"]
            == "sumi"
        )
        local.post("/dev/paint/Wren")
        # The runner (the mic's path) asks the live settings at paint time.
        app.state.live_settings.style = "cubist"
        app.state.runner.on_detections(
            [Detection("Dunnock", "Prunella modularis", 0.91, 0.0, 3.0)]
        )
    assert prompts[0].count("hand-painted naturalist watercolor") == 1
    assert style_for("sumi").look in prompts[1]
    assert style_for("cubist").look in prompts[2]
