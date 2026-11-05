from __future__ import annotations

from email.message import Message
from urllib import error

import pytest

from tests.evals import graders


def _reply(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _http_error(code: int) -> error.HTTPError:
    return error.HTTPError(graders.DEEPSEEK_URL, code, "err", Message(), None)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    return []


def test_deepseek_success_does_not_touch_gemini(monkeypatch, calls) -> None:
    def fake_post(url, key, payload, timeout):
        calls.append((url, payload))
        return _reply("ok")

    monkeypatch.setattr(graders, "_post", fake_post)
    assert graders.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert [url for url, _ in calls] == [graders.DEEPSEEK_URL]


def test_payment_required_fails_over_to_gemini(monkeypatch, calls) -> None:
    def fake_post(url, key, payload, timeout):
        calls.append((url, payload))
        if url == graders.DEEPSEEK_URL:
            raise _http_error(402)
        return _reply("from gemini")

    monkeypatch.setattr(graders, "_post", fake_post)
    assert graders.chat([{"role": "user", "content": "hi"}]) == "from gemini"
    gemini_url, gemini_payload = calls[1]
    assert gemini_url == graders.GEMINI_URL
    assert gemini_payload["model"] == graders.GEMINI_MODEL


def test_no_gemini_key_reraises(monkeypatch, calls) -> None:
    monkeypatch.delenv("GEMINI_API_KEY")

    def fake_post(url, key, payload, timeout):
        raise _http_error(402)

    monkeypatch.setattr(graders, "_post", fake_post)
    with pytest.raises(error.HTTPError):
        graders.chat([{"role": "user", "content": "hi"}])


def test_bad_request_is_not_failed_over(monkeypatch, calls) -> None:
    def fake_post(url, key, payload, timeout):
        calls.append((url, payload))
        raise _http_error(400)

    monkeypatch.setattr(graders, "_post", fake_post)
    with pytest.raises(error.HTTPError):
        graders.chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1
