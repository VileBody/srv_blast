from __future__ import annotations

import mlcore.footage_tagger as ft


def test_vision_endpoints_order_and_key_gating(monkeypatch) -> None:
    monkeypatch.setenv("TAG_PROVIDER_ORDER", "qwen,groq")
    monkeypatch.setenv("DASHSCOPE_API_KEYS", "dk1,dk2")
    monkeypatch.setenv("GROQ_API_KEYS", "gk1")
    eps = ft.vision_endpoints()
    assert [e["provider"] for e in eps] == ["qwen", "qwen", "groq"]
    assert eps[0]["base_url"].endswith("/compatible-mode/v1")
    assert eps[-1]["base_url"] == ft._GROQ_BASE

    # reversed order
    monkeypatch.setenv("TAG_PROVIDER_ORDER", "groq,qwen")
    eps2 = ft.vision_endpoints()
    assert [e["provider"] for e in eps2][0] == "groq"

    # only providers with keys appear
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setattr(ft, "_fallback_groq_keys", lambda: [])
    monkeypatch.setenv("TAG_PROVIDER_ORDER", "qwen,groq")
    eps3 = ft.vision_endpoints()
    assert {e["provider"] for e in eps3} == {"qwen"}


def test_tag_one_frame_qwen_first_groq_only_on_failure(monkeypatch) -> None:
    endpoints = [
        {"provider": "qwen", "base_url": "b1", "api_key": "k1", "model": "qwen-vl-max"},
        {"provider": "groq", "base_url": "b2", "api_key": "k2", "model": "llama"},
    ]
    calls = []

    # qwen succeeds -> groq must NOT be called (groq "rests")
    def qwen_ok(image_b64, *, base_url, api_key, model, prompt="", timeout=30.0):
        calls.append(base_url)
        return {"mood": "minor"} if base_url == "b1" else None

    monkeypatch.setattr(ft, "call_openai_vision", qwen_ok)
    assert ft._tag_one_frame("img", endpoints, prompt="p") == {"mood": "minor"}
    assert calls == ["b1"]  # qwen only; groq not touched

    # qwen fails -> falls over to groq, in order
    calls.clear()

    def qwen_429(image_b64, *, base_url, api_key, model, prompt="", timeout=30.0):
        calls.append(base_url)
        return {"mood": "minor"} if base_url == "b2" else None

    monkeypatch.setattr(ft, "call_openai_vision", qwen_429)
    assert ft._tag_one_frame("img", endpoints, prompt="p") == {"mood": "minor"}
    assert calls == ["b1", "b2"]  # qwen tried first, then groq


def test_tag_one_frame_all_fail_returns_none(monkeypatch) -> None:
    endpoints = [{"provider": "qwen", "base_url": "b1", "api_key": "k", "model": "m"}]
    monkeypatch.setattr(ft, "call_openai_vision", lambda *a, **k: None)
    assert ft._tag_one_frame("img", endpoints, prompt="p") is None
