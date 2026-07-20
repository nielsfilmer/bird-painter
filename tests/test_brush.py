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


def test_paint_extension_is_jpg_for_non_png(monkeypatch):
    def fake_post(url, **kw):
        return _resp(200, json={"images": [{"url": "http://cdn/x.jpg",
                                            "content_type": "image/jpeg"}]})

    def fake_get(url, **kw):
        return _resp(200, content=b"JPGBYTES", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    monkeypatch.setattr(brush.httpx, "get", fake_get)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") == (
        b"JPGBYTES",
        "jpg",
    )


def test_paint_falls_back_to_download_content_type_header(monkeypatch):
    # fal's image entry omits content_type → extension comes from the CDN
    # download's content-type header instead.
    def fake_post(url, **kw):
        return _resp(200, json={"images": [{"url": "http://cdn/x"}]})

    def fake_get(url, **kw):
        return _resp(200, content=b"PNGBYTES", headers={"content-type": "image/png"})

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    monkeypatch.setattr(brush.httpx, "get", fake_get)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") == (
        b"PNGBYTES",
        "png",
    )


def test_paint_returns_none_when_image_entry_has_no_url(monkeypatch):
    def fake_post(url, **kw):
        return _resp(200, json={"images": [{}]})  # entry present but no "url"

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") is None


def test_paint_image_download_failure_is_a_soft_failure(monkeypatch):
    def fake_post(url, **kw):
        return _resp(200, json={"images": [{"url": "http://cdn/x.png"}]})

    def fake_get(url, **kw):
        raise httpx.ReadTimeout("slow cdn")

    monkeypatch.setattr(brush.httpx, "post", fake_post)
    monkeypatch.setattr(brush.httpx, "get", fake_get)
    assert brush.paint("Robin", brush.UNKNOWN_SCIENTIFIC, fal_key="k") is None
