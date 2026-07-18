import xml.dom.minidom

from bird_painter.placeholder import placeholder_svg


def parses(svg: bytes) -> bool:
    xml.dom.minidom.parseString(svg)
    return True


def test_plain_names_render():
    assert parses(placeholder_svg("European Robin", "Erithacus rubecula"))


def test_markup_in_species_names_is_escaped():
    svg = placeholder_svg("Rock <b>Dove</b>", "R&D <script>x</script>")
    assert parses(svg)
    assert b"<b>" not in svg
    assert b"<script>" not in svg


def test_ampersand_alone_is_escaped():
    assert parses(placeholder_svg("Parson & Clerk", "A & B"))
