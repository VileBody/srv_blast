from __future__ import annotations

from services.orchestrator import llm_cache


def _key(
    monkeypatch,
    *,
    backend: str,
    revision: str = "",
    algorithm: str = "",
) -> llm_cache.CacheKey:
    monkeypatch.setenv("GEMINI_MODEL_STAGE1", "gemini-stage1")
    monkeypatch.setenv("GEMINI_MODEL_STAGE1_ASR", "gemini-stage1-asr")
    monkeypatch.setenv("GEMINI_MODEL_SUBTITLES", "gemini-subtitles")
    return llm_cache.build_cache_key(
        telegram_id="42",
        audio_hash="a" * 64,
        clip_start_sec=10.0,
        clip_end_sec=20.0,
        asr_mode="local_ctc" if backend == "local_ctc" else "forced_alignment",
        lyrics_text="точный фрагмент",
        subtitles_mode="legacy_blocks",
        user_drop_t=None,
        stage1_alignment_backend=backend,
        alignment_model_revision=revision,
        alignment_algorithm_version=algorithm,
    )


def test_cache_key_partitions_backend_revision_and_algorithm(monkeypatch) -> None:
    gemini = _key(monkeypatch, backend="gemini")
    local_a = _key(
        monkeypatch,
        backend="local_ctc",
        revision="revision-a",
        algorithm="algorithm-a",
    )
    local_b = _key(
        monkeypatch,
        backend="local_ctc",
        revision="revision-b",
        algorithm="algorithm-a",
    )
    local_c = _key(
        monkeypatch,
        backend="local_ctc",
        revision="revision-a",
        algorithm="algorithm-b",
    )

    paths = {
        llm_cache._stage_s3_keys(key)["stage1_asr"]
        for key in (gemini, local_a, local_b, local_c)
    }
    assert len(paths) == 4


def test_stage1_cache_group_preserves_alignment_metadata() -> None:
    assert "stage1_alignment_backend" in llm_cache._STAGE1_ASR_KEYS
    assert "stage1_alignment_metadata" in llm_cache._STAGE1_ASR_KEYS
