import httpx

from bird_painter import brush
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


def _resp(status, *, json=None, content=None, headers=None, url=brush.FAL_ENDPOINT):
    return httpx.Response(
        status,
        json=json,
        content=content,
        headers=headers,
        request=httpx.Request("GET", url),
    )


def test_paint_success_returns_image_and_extension(monkeypatch):
    def fake_post(url, **kw):
        return _resp(200, json={"images": [{"url": "http://cdn/x.png",
                                            "content_type": "image/png"}]})

    def fake_get(url, **kw):
        return _resp(200, content=b"PNGBYTES", headers={"content-type": "image/png"})

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    monkeypatch.setattr(brush.httpx, "get", fake_get)
    result = brush.paint("Robin", "Erithacus rubecula", fal_key="k")
    assert result == (b"PNGBYTES", "png")


def test_paint_http_error_is_a_soft_failure(monkeypatch):
    def fake_post(url, **kw):
        return _resp(403, json={"detail": "Exhausted balance"})

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") is None


def test_paint_transport_error_is_a_soft_failure(monkeypatch):
    def fake_post(url, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") is None


def test_paint_returns_none_when_fal_sends_no_image(monkeypatch):
    def fake_post(url, **kw):
        return _resp(200, json={"images": []})

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") is None
