# mlcore/gemini_call.py
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from google.genai import types

from mlcore.gemini_client import GeminiClient
from mlcore.models.full_plan import FullPlanPayload


def pick_audio_files(audio_dir: Path) -> List[Path]:
    exts = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
    if not audio_dir.exists():
        raise FileNotFoundError(f"audio dir missing: {audio_dir}")
    files = [p for p in sorted(audio_dir.iterdir()) if p.is_file() and p.suffix.lower() in exts]
    if not files:
        raise FileNotFoundError(f"No audio files in {audio_dir}")
    return files


def call_full_plan_once(
    *,
    client: GeminiClient,
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    audio_paths: List[Path],
    # NOTE: we no longer upload descriptions bundle as a File (to avoid Files API issues).
    descriptions_bundle_text: Optional[str] = None,
    raw_response_path: Optional[Path] = None,
    cache_path: Optional[Path] = None,
) -> FullPlanPayload:
    """
    One Gemini call:
      contents = [audio files] + [user_prompt (+ bundle text)]
      schema = FullPlanPayload

    IMPORTANT:
      - descriptions bundle is injected into user prompt as text (NOT uploaded file).
      - audio is still uploaded as file for alignment.
    """
    files: List[types.File] = []

    # Upload ONLY audio files
    if cache_path is not None:
        if audio_paths:
            files.extend(client.upload_files_cached(audio_paths, cache_path=cache_path))
    else:
        if audio_paths:
            files.extend(client.upload_files(audio_paths))

    prompt = user_prompt
    if descriptions_bundle_text:
        # keep it clearly separated and machine-readable
        prompt = (
            prompt
            + "\n\n"
            + "=== DESCRIPTIONS_BUNDLE_JSON (INLINE) ===\n"
            + descriptions_bundle_text
            + "\n=== END DESCRIPTIONS_BUNDLE_JSON ===\n"
        )

    payload = client.generate_structured(
        schema_model=FullPlanPayload,
        prompt=prompt,
        files=files,
        system_instruction=system_instruction,
        raw_response_path=raw_response_path,
    )
    return payload
