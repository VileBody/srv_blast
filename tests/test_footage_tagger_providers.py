from __future__ import annotations

import requests

import mlcore.footage_tagger as ft


def _valid_result():
    return {
        "color_tone": "cold",
        "people_type": "none",
        "theme_tags": ["night", "night city", "rain", "wet road"],
        "mood": "minor",
    }


def test_vision_endpoints_are_qwen_only(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEYS", "dk1,dk2")
    monkeypatch.setenv("GROQ_API_KEYS", "must-be-ignored")
    monkeypatch.setenv("TAG_PROVIDER_ORDER", "groq,qwen")

    eps = ft.vision_endpoints()

    assert [e["provider"] for e in eps] == ["qwen", "qwen"]
    assert [e["api_key"] for e in eps] == ["dk1", "dk2"]
    assert all(e["base_url"].endswith("/compatible-mode/v1") for e in eps)


def test_tag_one_frame_rotates_qwen_keys_only_on_failure(monkeypatch) -> None:
    endpoints = [
        {"provider": "qwen", "base_url": "b1", "api_key": "k1", "model": "qwen-vl-max"},
        {"provider": "qwen", "base_url": "b1", "api_key": "k2", "model": "qwen-vl-max"},
    ]
    calls = []

    def second_key_succeeds(image_b64, *, base_url, api_key, model, prompt="", timeout=30.0):
        calls.append(api_key)
        return _valid_result() if api_key == "k2" else None

    monkeypatch.setattr(ft, "call_openai_vision", second_key_succeeds)
    assert ft._tag_one_frame("img", endpoints, prompt="p") == ft.normalize_vision_result(_valid_result())
    assert calls == ["k1", "k2"]


def test_tag_one_frame_all_fail_returns_none(monkeypatch) -> None:
    endpoints = [{"provider": "qwen", "base_url": "b1", "api_key": "k", "model": "m"}]
    monkeypatch.setattr(ft, "call_openai_vision", lambda *a, **k: None)
    assert ft._tag_one_frame("img", endpoints, prompt="p") is None


def test_qwen_request_ignores_global_proxy(monkeypatch) -> None:
    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": '{"mood": "minor"}'}}]}

    class _Session:
        def __init__(self):
            self.trust_env = True
            self.proxies = {}
            self.closed = False

        def post(self, url, **kwargs):
            assert url.startswith("https://dashscope-intl.aliyuncs.com/")
            return _Response()

        def close(self):
            self.closed = True

    session = _Session()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setenv("HTTPS_PROXY", "http://broken-global-proxy")
    monkeypatch.delenv("DASHSCOPE_PROXY_URL", raising=False)

    assert ft.call_openai_vision(
        "img",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        api_key="key",
        model="qwen-vl-max",
    ) == {"mood": "minor"}
    assert session.trust_env is False
    assert session.proxies == {}
    assert session.closed is True


def test_normalize_vision_result_maps_aliases_and_drops_unknown_tags() -> None:
    out = ft.normalize_vision_result({
        "color_tone": "cool",
        "people_type": "man",
        "theme_tags": ["Night", "rainy", "wet pavement", "neon glow", "invented tag"],
        "mood": "MINOR",
    })

    assert out == {
        "color_tone": "cold",
        "people_type": "guys",
        "has_people": True,
        "theme_tags": ["night", "rain", "wet road", "neon lights"],
        "mood": "minor",
    }


def test_semantically_invalid_qwen_result_uses_next_qwen_key(monkeypatch) -> None:
    endpoints = [
        {"provider": "qwen", "base_url": "b1", "api_key": "k1", "model": "qwen-vl-max"},
        {"provider": "qwen", "base_url": "b1", "api_key": "k2", "model": "qwen-vl-max"},
    ]
    calls = []

    def response(image_b64, *, base_url, api_key, model, prompt="", timeout=30.0):
        calls.append(api_key)
        if api_key == "k1":
            return {"color_tone": "cold", "people_type": "none", "theme_tags": ["night"], "mood": "minor"}
        return _valid_result()

    monkeypatch.setattr(ft, "call_openai_vision", response)
    assert ft._tag_one_frame("img", endpoints, prompt="p") == ft.normalize_vision_result(_valid_result())
    assert calls == ["k1", "k2"]


def test_v2_prompt_is_bound_to_production_vocabulary() -> None:
    prompt = ft.build_vision_prompt(media_kind="photo")

    assert "6-10 DISTINCT values" in prompt
    assert "ALLOWED THEME TAGS" in prompt
    assert "wet road" in prompt
    assert "Never infer" in prompt
