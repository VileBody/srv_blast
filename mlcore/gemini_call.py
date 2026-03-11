# mlcore/gemini_call.py
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, TypeVar
import os
import logging

from google.genai import types

from mlcore.gemini_client import GeminiClient
from mlcore.llm_router import (
    RoutedCallResult,
    run_routed_call,
)
from mlcore.models import BlocksTokensPayload
from mlcore.models.subtitles_spans import BlocksTokenSpansPayload
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.stage1_scenario import Stage1ScenarioPayload
from mlcore.models.switch_timing import Stage2TimingAnalysisPayload, Stage2TimingCutsPayload
from mlcore.models.tagged_subtitles import TaggedSubtitlesPayload
from mlcore.openrouter_client import OpenRouterClient


def _is_ascii_name(name: str) -> bool:
    try:
        name.encode("ascii")
        return True
    except Exception:
        return False


def _prepare_upload_paths(paths: List[Path]) -> List[Path]:
    """
    Gemini SDK/httpx may fail on non-ASCII filename in multipart headers.
    For such files, copy to a temp ASCII-safe path and upload that file.
    """
    out: List[Path] = []
    tmp_root = Path(tempfile.gettempdir()) / "gemini_upload_ascii"
    tmp_root.mkdir(parents=True, exist_ok=True)

    for p in paths:
        pp = p.expanduser().resolve()
        if _is_ascii_name(pp.name):
            out.append(pp)
            continue

        digest = hashlib.sha1(str(pp).encode("utf-8")).hexdigest()[:12]
        ext = pp.suffix or ".bin"
        safe = tmp_root / f"upload_{digest}{ext}"
        if not safe.exists():
            shutil.copy2(pp, safe)
        out.append(safe)

    return out


def pick_audio_files(audio_dir: Path) -> List[Path]:
    """
    IMPORTANT:
      - We must provide EXACTLY ONE audio track to Gemini, otherwise alignment becomes undefined.
    Priority:
      1) AUDIO_FILE_PATH env var (single explicit file)
      2) first audio file found in audio_dir (sorted)
    """
    # (1) explicit single file
    env_path = (os.environ.get("AUDIO_FILE_PATH") or "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            raise FileNotFoundError(f"AUDIO_FILE_PATH missing: {p}")
        return [p.resolve()]

    # (2) fallback: pick first supported file in directory
    exts = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
    if not audio_dir.exists():
        raise FileNotFoundError(f"audio dir missing: {audio_dir}")
    files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in exts]
    if not files:
        raise FileNotFoundError(f"No audio files in {audio_dir}")
    return [files[0].resolve()]


T = TypeVar("T")


def _provider_raw_path(raw_response_path: Optional[Path], *, provider: str) -> Optional[Path]:
    if raw_response_path is None:
        return None
    return raw_response_path.with_name(f"{raw_response_path.stem}_{provider}{raw_response_path.suffix}")


def _sync_canonical_raw_path(
    *,
    raw_response_path: Optional[Path],
    routed: RoutedCallResult[object],
) -> None:
    if raw_response_path is None:
        return
    tagged = _provider_raw_path(raw_response_path, provider=routed.provider)
    if tagged is None or not tagged.exists():
        return
    if tagged.resolve() == raw_response_path.resolve():
        return
    raw_response_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tagged, raw_response_path)


def _run_routed(
    *,
    stage_name: str,
    provider_mode: str,
    hedge_delay_s: float,
    logger: Optional[logging.Logger],
    gemini_call: Callable[[], T],
    openrouter_call: Callable[[], T],
) -> RoutedCallResult[T]:
    return run_routed_call(
        mode=provider_mode,
        stage=stage_name,
        hedge_delay_s=float(hedge_delay_s),
        gemini_call=gemini_call,
        openrouter_call=openrouter_call,
        logger=logger,
    )


def call_full_plan_once(
    *,
    client: GeminiClient,
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    extra_file_paths: Optional[List[Path]] = None,
    # If provided: descriptions bundle is injected into user prompt as text.
    # (We keep this because it is the most reliable "inplace" mode.)
    descriptions_bundle_text: Optional[str] = None,
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    # log what we actually send
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> FullPlanPayload:
    """
    One Gemini call:
      contents = [uploaded files...] + [prompt_text]
      schema = FullPlanPayload

    Typical modes:
      - Mode A (inplace): descriptions_bundle_text is provided -> appended inline to prompt.
      - Mode B (as txt): extra_file_paths includes a .txt that contains JSON bundle.

    IMPORTANT:
      - audio is uploaded as file for alignment.
      - prompt/system can be dumped to disk for debugging.
    """
    files: List[types.File] = []

    # Upload audio files
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if cache_path is not None:
        if audio_paths:
            files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
    else:
        if audio_paths:
            files.extend(client.upload_files(audio_upload_paths))

    # Upload extra context files (optional)
    if extra_file_paths:
        if cache_path is not None:
            files.extend(client.upload_files_cached(extra_file_paths, cache_path=cache_path))
        else:
            files.extend(client.upload_files(extra_file_paths))

    prompt = user_prompt
    if descriptions_bundle_text:
        prompt = (
            prompt
            + "\n\n"
            + "=== DESCRIPTIONS_BUNDLE_JSON (INLINE) ===\n"
            + descriptions_bundle_text
            + "\n=== END DESCRIPTIONS_BUNDLE_JSON ===\n"
        )

    # dump system + final prompt (exactly what is sent)
    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")

    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(prompt, encoding="utf-8")

    payload = client.generate_structured(
        schema_model=FullPlanPayload,
        prompt=prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_stage1_plan_once(
    *,
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1PlanPayload:
    files: List[types.File] = []
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if cache_path is not None:
        if audio_paths:
            files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
    else:
        if audio_paths:
            files.extend(client.upload_files(audio_upload_paths))

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    payload = client.generate_structured(
        schema_model=Stage1PlanPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_stage1_asr_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1AsrPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> Stage1AsrPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_structured(
            schema_model=Stage1AsrPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> Stage1AsrPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=Stage1AsrPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return Stage1AsrPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage1_asr",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return Stage1AsrPayload.model_validate(routed.value)


def call_stage1_forced_alignment_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1ForcedAlignmentPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> Stage1ForcedAlignmentPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_structured(
            schema_model=Stage1ForcedAlignmentPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> Stage1ForcedAlignmentPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=Stage1ForcedAlignmentPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return Stage1ForcedAlignmentPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage1_forced_alignment",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return Stage1ForcedAlignmentPayload.model_validate(routed.value)


def call_stage1_scenario_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1ScenarioPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> Stage1ScenarioPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_structured(
            schema_model=Stage1ScenarioPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> Stage1ScenarioPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=Stage1ScenarioPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return Stage1ScenarioPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage1_scenario",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return Stage1ScenarioPayload.model_validate(routed.value)


def call_subtitles_plan_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> BlocksTokensPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> BlocksTokensPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_tokens_structured(
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> BlocksTokensPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_tokens_structured(
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return BlocksTokensPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage2_subtitles",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return BlocksTokensPayload.model_validate(routed.value)


def call_subtitles_tagged_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> TaggedSubtitlesPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> TaggedSubtitlesPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        out = client.generate_structured(
            schema_model=TaggedSubtitlesPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )
        return TaggedSubtitlesPayload.model_validate(out)

    def _openrouter_call() -> TaggedSubtitlesPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=TaggedSubtitlesPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return TaggedSubtitlesPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage2_subtitles_tagged",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return TaggedSubtitlesPayload.model_validate(routed.value)


def call_subtitles_spans_once(
    *,
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> BlocksTokenSpansPayload:
    files: List[types.File] = []
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if cache_path is not None:
        if audio_paths:
            files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
    else:
        if audio_paths:
            files.extend(client.upload_files(audio_upload_paths))

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    payload = client.generate_structured(
        schema_model=BlocksTokenSpansPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_timing_analysis_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage2TimingAnalysisPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> Stage2TimingAnalysisPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_structured(
            schema_model=Stage2TimingAnalysisPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> Stage2TimingAnalysisPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=Stage2TimingAnalysisPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return Stage2TimingAnalysisPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage2_timing_analysis",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return Stage2TimingAnalysisPayload.model_validate(routed.value)


def call_timing_cuts_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage2TimingCutsPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> Stage2TimingCutsPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        return client.generate_structured(
            schema_model=Stage2TimingCutsPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> Stage2TimingCutsPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        out = openrouter_client.generate_structured(
            schema_model=Stage2TimingCutsPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return Stage2TimingCutsPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage2_timing_cuts",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return Stage2TimingCutsPayload.model_validate(routed.value)


def call_footage_plan_once(
    *,
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    extra_file_paths: Optional[List[Path]] = None,
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> FootageSelectionPayload:
    files: List[types.File] = []
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if cache_path is not None:
        if audio_paths:
            files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
    else:
        if audio_paths:
            files.extend(client.upload_files(audio_upload_paths))

    if extra_file_paths:
        if cache_path is not None:
            files.extend(client.upload_files_cached(extra_file_paths, cache_path=cache_path))
        else:
            files.extend(client.upload_files(extra_file_paths))

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    payload = client.generate_structured(
        schema_model=FootageSelectionPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_footage_style_once(
    *,
    client: Optional[GeminiClient],
    openrouter_client: Optional[OpenRouterClient] = None,
    provider_mode: str = "gemini",
    hedge_delay_s: float = 60.0,
    logger: Optional[logging.Logger] = None,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    extra_file_paths: Optional[List[Path]] = None,
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> FootageStylePickPayload:
    audio_upload_paths = _prepare_upload_paths(audio_paths)

    if system_dump_path is not None:
        system_dump_path.parent.mkdir(parents=True, exist_ok=True)
        system_dump_path.write_text(system_instruction or "", encoding="utf-8")
    if prompt_dump_path is not None:
        prompt_dump_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_dump_path.write_text(user_prompt, encoding="utf-8")

    def _gemini_call() -> FootageStylePickPayload:
        if client is None:
            raise RuntimeError("Gemini client is required for provider mode with gemini")
        files: List[types.File] = []
        if cache_path is not None:
            if audio_upload_paths:
                files.extend(client.upload_files_cached(audio_upload_paths, cache_path=cache_path))
        else:
            if audio_upload_paths:
                files.extend(client.upload_files(audio_upload_paths))
        if extra_file_paths:
            if cache_path is not None:
                files.extend(client.upload_files_cached(extra_file_paths, cache_path=cache_path))
            else:
                files.extend(client.upload_files(extra_file_paths))

        return client.generate_structured(
            schema_model=FootageStylePickPayload,
            prompt=user_prompt,
            files=files,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="gemini"),
        )

    def _openrouter_call() -> FootageStylePickPayload:
        if openrouter_client is None:
            raise RuntimeError("OpenRouter client is required for provider mode with openrouter")
        if extra_file_paths:
            raise RuntimeError(
                "OpenRouter path for call_footage_style_once does not support extra_file_paths"
            )
        out = openrouter_client.generate_structured(
            schema_model=FootageStylePickPayload,
            prompt=user_prompt,
            audio_paths=audio_upload_paths,
            system_instruction=system_instruction,
            raw_response_path=_provider_raw_path(raw_response_path, provider="openrouter"),
        )
        return FootageStylePickPayload.model_validate(out)

    routed = _run_routed(
        stage_name="stage2_style",
        provider_mode=provider_mode,
        hedge_delay_s=hedge_delay_s,
        logger=logger,
        gemini_call=_gemini_call,
        openrouter_call=_openrouter_call,
    )
    _sync_canonical_raw_path(raw_response_path=raw_response_path, routed=routed)
    return FootageStylePickPayload.model_validate(routed.value)
