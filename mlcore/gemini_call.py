# mlcore/gemini_call.py
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional
import os

from google.genai import types

from mlcore.gemini_client import GeminiClient
from mlcore.models import BlocksTokensPayload
from mlcore.models.subtitles_spans import BlocksTokenSpansPayload
from mlcore.models.footage_plan import FootageSelectionPayload
from mlcore.models.footage_style import FootageStylePickPayload
from mlcore.models.full_plan import FullPlanPayload
from mlcore.models.stage1_asr import Stage1AsrPayload
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.stage1_scenario import Stage1ScenarioPayload


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
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1AsrPayload:
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
        schema_model=Stage1AsrPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_stage1_scenario_once(
    *,
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> Stage1ScenarioPayload:
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
        schema_model=Stage1ScenarioPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


def call_subtitles_plan_once(
    *,
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> BlocksTokensPayload:
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

    payload = client.generate_tokens_structured(
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload


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
    client: GeminiClient,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    extra_file_paths: Optional[List[Path]] = None,
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    prompt_dump_path: Optional[Path] = None,
    system_dump_path: Optional[Path] = None,
) -> FootageStylePickPayload:
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
        schema_model=FootageStylePickPayload,
        prompt=user_prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload
