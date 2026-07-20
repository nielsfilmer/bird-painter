import xml.dom.minidom

from bird_painter.placeholder import placeholder_svg


def parses(svg: bytes) -> bool:
    xml.dom.minidom.parseString(svg)
    return True


def test_renders_valid_svg():
    assert parses(placeholder_svg("European Robin", "Erithacus rubecula"))


def test_no_text_is_rendered():
    # The wall carries no per-bird label and the images must carry none either.
    svg = placeholder_svg("European Robin", "Erithacus rubecula")
    assert b"<text" not in svg


def test_odd_species_names_never_reach_the_svg():
    # Names only pick a tint; markup/entities in a name can't break the SVG
    # because the name is never drawn.
    svg = placeholder_svg("Rock <b>Dove</b> & co", "R&D <script>x</script>")
    assert parses(svg)
    assert b"<b>" not in svg
    assert b"<script>" not in svg
    assert b"Dove" not in svg
