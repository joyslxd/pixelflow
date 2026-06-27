from __future__ import annotations

import io
import urllib.error

from pixelflow.skills.borgrise import run_generation


def test_send_request_marks_http_402_as_quota_insufficient(monkeypatch):
    body = '{"success":false,"message":"额度不足，剩余额度: 0，需要: 1"}'.encode()

    def raise_402(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url="https://x/api/picture/text_to_image",
            code=402,
            msg="Payment Required",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr(run_generation.urllib.request, "urlopen", raise_402)

    result = run_generation._send_request("https://x/api/picture/text_to_image", b"{}", {}, "POST")

    assert result["error"] is True
    assert result["quota_insufficient"] is True
    assert result["non_retryable"] is True
    assert "额度不足" in result["message"]


def test_make_request_does_not_retry_quota_insufficient(monkeypatch):
    calls = 0

    def fake_send(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "error": True,
            "quota_insufficient": True,
            "non_retryable": True,
            "status_code": 402,
            "message": "用户没有有效的额度",
        }

    monkeypatch.setattr(run_generation, "_send_request", fake_send)
    monkeypatch.setattr(run_generation, "_apply_auth_header", lambda headers: headers)

    result = run_generation.make_request("/picture/text_to_image", {"prompt": "x"})

    assert result["quota_insufficient"] is True
    assert calls == 1
