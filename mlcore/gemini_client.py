# mlcore/gemini_client.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import mimetypes
import time
import logging

from pydantic import BaseModel
from google import genai
from google.genai import types

from mlcore.models import BlocksTokensPayload


def normalize_proxy(proxy: str) -> str:
    p = (proxy or "").strip()
    if not p:
        return ""
    return p


def make_client(*, api_key: str, proxy: str, timeout_s: float) -> genai.Client:
    """
    timeout_s — общий таймаут на HTTP запрос.
    """
    proxy_norm = normalize_proxy(proxy)

    client_args: Dict[str, Any] = {}
    async_client_args: Dict[str, Any] = {}
    if proxy_norm:
        client_args["proxy"] = proxy_norm
        async_client_args["proxy"] = proxy_norm

    client_args["timeout"] = timeout_s
    async_client_args["timeout"] = timeout_s

    http_options = types.HttpOptions(
        client_args=client_args,
        async_client_args=async_client_args,
    )
    return genai.Client(api_key=api_key, http_options=http_options)


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    model: str
    temperature: float = 0.0
    proxy: str = ""
    timeout_s: float = 120.0
    max_attempts: int = 1  # NOTE: kept for compat; retries handled by Celery now


# =============================================================================
# Helpers: file hashing / mime
# =============================================================================

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guess_mime(p: Path) -> str:
    mt, _ = mimetypes.guess_type(str(p))
    return mt or "application/octet-stream"


# =============================================================================
# Helpers: LLM payload sanitation (canonicalize trailing + phrase)
# =============================================================================

def _sanitize_trailing(tokens: List[Dict[str, Any]]) -> None:
    """
    Enforce:
      - trailing in {" ", "\r", ""}
      - only last token may have trailing=""
      - last token must have trailing=""
    """
    if not tokens:
        return

    for i, t in enumerate(tokens):
        tr = t.get("trailing", " ")
        if tr not in (" ", "\r", ""):
            tr = " "
        if tr == "" and i != len(tokens) - 1:
            tr = " "
        t["trailing"] = tr

    tokens[-1]["trailing"] = ""


def _recon_phrase(tokens: List[Dict[str, Any]]) -> str:
    return "".join(str(t.get("text", "")) + str(t.get("trailing", "")) for t in tokens)


def _push_r_from_phrase_into_trailing_if_safe(seg: Dict[str, Any]) -> None:
    """
    If seg.phrase contains '\r' but recon(tokens) doesn't,
    and seg.phrase equals recon(tokens) with '\r' replaced by " ",
    then restore '\r' by changing the corresponding token.trailing " " -> "\r".
    Supports at most one '\r'.
    """
    phrase = seg.get("phrase")
    tokens = seg.get("tokens")
    if not isinstance(phrase, str) or "\r" not in phrase:
        return
    if not isinstance(tokens, list) or not tokens or not all(isinstance(x, dict) for x in tokens):
        return

    _sanitize_trailing(tokens)
    recon0 = _recon_phrase(tokens)

    # already ok
    if "\r" in recon0:
        return

    # safe-only: only difference is \r vs space
    if phrase.replace("\r", " ") != recon0:
        return

    br_pos = phrase.index("\r")

    # find token boundary that produced the recon character at br_pos
    cum = 0
    for t in tokens:
        txt = str(t.get("text", ""))
        tr = str(t.get("trailing", ""))
        cum += len(txt) + len(tr)
        if cum == br_pos + 1:
            if t.get("trailing") == " ":
                t["trailing"] = "\r"
            break

    _sanitize_trailing(tokens)


def _sanitize_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonicalize one {phrase, tokens[]} segment:
      1) fix trailing
      2) if phrase had \r but tokens didn't, restore \r in trailing when safe
      3) set phrase := recon(tokens)
    """
    if not isinstance(seg, dict):
        return seg
    tokens = seg.get("tokens")
    if not isinstance(tokens, list) or not tokens or not all(isinstance(x, dict) for x in tokens):
        return seg

    _push_r_from_phrase_into_trailing_if_safe(seg)
    _sanitize_trailing(tokens)
    seg["phrase"] = _recon_phrase(tokens)
    seg["tokens"] = tokens
    return seg


def _sanitize_payload_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply _sanitize_segment everywhere it fits + normalize mine_drop from glitch_peak last token.
    """
    def at(path: List[str]) -> None:
        cur: Any = d
        for k in path[:-1]:
            cur = cur.get(k)
            if not isinstance(cur, dict):
                return
        seg = cur.get(path[-1])
        if isinstance(seg, dict):
            cur[path[-1]] = _sanitize_segment(seg)

    at(["block_1"])
    at(["block_2", "p1"])
    at(["block_2", "p2"])
    at(["block_3"])
    at(["block_4", "p1"])
    at(["block_4", "p2"])
    at(["block_5", "slowly_in"])
    at(["block_5", "fast_reveal"])
    at(["block_5", "glitch_peak"])
    at(["block_6"])
    at(["block_7", "part1"])
    at(["block_7", "part2"])

    # mine_drop canonicalization: last token of glitch_peak
    try:
        peak_tokens = d["block_5"]["glitch_peak"]["tokens"]
        if isinstance(peak_tokens, list) and peak_tokens and isinstance(peak_tokens[-1], dict):
            last = peak_tokens[-1]
            d["block_5"]["mine_drop"] = {
                "text": str(last.get("text", "")),
                "t_start": float(last.get("t_start")),
                "t_end": float(last.get("t_end")),
            }
    except Exception:
        pass

    return d


# =============================================================================
# Gemini Client
# =============================================================================

class GeminiClient:
    """
    IMPORTANT POLICY:
      - No internal retries here.
      - If Gemini fails transiently, Celery should retry the whole job.
    """

    def __init__(self, settings: GeminiSettings, *, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger("mlcore.gemini_client")
        self._client = make_client(api_key=settings.api_key, proxy=settings.proxy, timeout_s=float(settings.timeout_s))
        self._model = settings.model
        self._temperature = float(settings.temperature)
        self._timeout_s = float(settings.timeout_s)
        self._max_attempts = int(settings.max_attempts)

    # ==========================================================
    # Files API helpers (get + wait ACTIVE)
    # ==========================================================

    def _file_get(self, name: str) -> Optional[types.File]:
        try:
            return self._client.files.get(name=name)
        except Exception:
            return None

    def _file_state_str(self, f: Any) -> str:
        s = getattr(f, "state", None)
        if s is None:
            return "UNKNOWN"
        return str(s)

    def _wait_file_active(self, f: types.File, *, max_wait_s: float = 90.0) -> types.File:
        """
        After upload files can be PROCESSING.
        Waiting prevents race: upload -> generate_content fails/acts weird.
        """
        name = getattr(f, "name", None)
        if not name:
            return f

        st = self._file_state_str(f)
        if "ACTIVE" in st:
            return f

        self._logger.info("gemini_file_wait_active name=%s state=%s", name, st)

        t0 = time.time()
        delay = 0.5
        while True:
            if time.time() - t0 > max_wait_s:
                self._logger.warning("gemini_file_wait_timeout name=%s last_state=%s", name, st)
                return f

            time.sleep(delay)
            delay = min(delay * 1.6, 5.0)

            fresh = self._file_get(name)
            if not fresh:
                self._logger.warning("gemini_file_get_failed_while_waiting name=%s", name)
                return f

            st = self._file_state_str(fresh)
            if "ACTIVE" in st:
                self._logger.info("gemini_file_active name=%s", name)
                return fresh

    # ==========================================================
    # Cache: sha256 -> file.name (to avoid slow reuploads)
    # ==========================================================

    def _load_cache(self, cache_path: Path) -> Dict[str, Any]:
        try:
            if cache_path.exists():
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), dict):
                    return data
        except Exception:
            pass
        return {"version": 1, "files": {}}

    def _save_cache(self, cache_path: Path, cache: Dict[str, Any]) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def upload_files_cached(self, paths: Iterable[Path], *, cache_path: Path) -> List[types.File]:
        """
        Idempotent upload:
          - sha256 local file
          - cache hit -> files.get(name) -> wait ACTIVE -> use
          - else upload -> wait ACTIVE -> save to cache
        """
        cache = self._load_cache(cache_path)
        files_map: Dict[str, Any] = cache.get("files", {})

        out: List[types.File] = []
        for p in paths:
            p = p.expanduser().resolve()
            if not p.exists():
                raise FileNotFoundError(str(p))

            sha = _sha256_file(p)
            size = int(p.stat().st_size)
            mime = _guess_mime(p)

            rec = files_map.get(sha)
            if isinstance(rec, dict) and rec.get("name"):
                cached_name = str(rec["name"])
                self._logger.info("gemini_cache_hit sha=%s name=%s", sha[:12], cached_name)

                got = self._file_get(cached_name)
                if got is not None:
                    got = self._wait_file_active(got)
                    out.append(got)
                    continue

                self._logger.info("gemini_cache_stale sha=%s name=%s -> reupload", sha[:12], cached_name)

            self._logger.info("uploading file=%s sha=%s", str(p), sha[:12])
            uploaded = self._client.files.upload(file=str(p))
            uploaded = self._wait_file_active(uploaded)

            up_name = getattr(uploaded, "name", None)
            if up_name:
                files_map[sha] = {"name": up_name, "size": size, "mime": mime, "basename": p.name}
                cache["files"] = files_map
                self._save_cache(cache_path, cache)

            out.append(uploaded)

        return out

    # ==========================================================
    # Backward-compatible plain upload_files (kept!)
    # ==========================================================

    def upload_files(self, paths: Iterable[Path]) -> List[types.File]:
        """
        Kept for compatibility with older orchestrator.
        Improved: also waits ACTIVE.
        """
        out: List[types.File] = []
        for p in paths:
            p = p.expanduser().resolve()
            if not p.exists():
                raise FileNotFoundError(str(p))
            self._logger.info("uploading file=%s", str(p))
            f = self._client.files.upload(file=str(p))
            f = self._wait_file_active(f)
            out.append(f)
        return out

    # ==========================================================
    # Minimal probe (text-only)
    # ==========================================================

    def probe_text(self, text: str = "u alive?") -> str:
        """
        One-shot lightweight probe call (no files, no JSON schema).
        Useful for debugging transient 5xx.
        """
        cfg = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="text/plain",
            system_instruction="Reply with a short plain text message.",
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=[text],
            config=cfg,
        )
        out = getattr(resp, "text", None)
        return out if isinstance(out, str) else ""

    # ==========================================================
    # Generate structured tokens (NO retries)
    # ==========================================================

    def generate_tokens_structured(
        self,
        *,
        prompt: str,
        files: Optional[List[types.File]] = None,
        system_instruction: Optional[str] = None,
        raw_response_path: Optional[Path] = None,
    ) -> BlocksTokensPayload:
        """
        Single attempt. No internal retries.
        Postprocess:
          - canonicalize trailing
          - canonicalize phrase (derived from tokens)
        """
        contents: List[object] = []
        if files:
            contents.extend(files)
        contents.append(prompt)

        cfg = types.GenerateContentConfig(
            temperature=self._temperature,
            response_mime_type="application/json",
            response_json_schema=BlocksTokensPayload.model_json_schema(),
            system_instruction=system_instruction,
        )

        log = self._logger
        log.info("gemini_call_tokens model=%s timeout_s=%s temperature=%s", self._model, self._timeout_s, self._temperature)

        resp = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=cfg,
        )
        text = getattr(resp, "text", None)
        if not text or not isinstance(text, str):
            raise RuntimeError(f"Gemini returned empty/non-text response. resp={resp!r}")

        if raw_response_path is not None:
            raw_response_path.parent.mkdir(parents=True, exist_ok=True)
            raw_response_path.write_text(text, encoding="utf-8")
            log.info("gemini_raw_saved path=%s", str(raw_response_path))

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = _sanitize_payload_dict(data)
                from mlcore.cr_patch import patch_payload_dict_inplace
                patch_payload_dict_inplace(data)
            return BlocksTokensPayload.model_validate(data)
        except Exception as e:
            head = text[:8000]
            raise RuntimeError(
                "Failed to validate Gemini JSON against BlocksTokensPayload. "
                f"err={e!r} text_head={head!r}"
            ) from e

    # ==========================================================
    # Generic structured JSON call (NO retries)
    # ==========================================================

    def generate_structured(
        self,
        *,
        schema_model: type[BaseModel],
        prompt: str,
        files: Optional[List[types.File]] = None,
        system_instruction: Optional[str] = None,
        raw_response_path: Optional[Path] = None,
    ) -> BaseModel:
        """
        Generic structured JSON call with arbitrary Pydantic schema.
        Single attempt. No internal retries.
        """
        contents: List[object] = []
        if files:
            contents.extend(files)
        contents.append(prompt)

        cfg = types.GenerateContentConfig(
            temperature=self._temperature,
            response_mime_type="application/json",
            response_json_schema=schema_model.model_json_schema(),
            system_instruction=system_instruction,
        )

        log = self._logger
        log.info("gemini_call_generic model=%s", self._model)

        resp = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=cfg,
        )
        text = getattr(resp, "text", None)
        if not text or not isinstance(text, str):
            raise RuntimeError(f"Gemini returned empty/non-text response. resp={resp!r}")

        if raw_response_path is not None:
            raw_response_path.parent.mkdir(parents=True, exist_ok=True)
            raw_response_path.write_text(text, encoding="utf-8")
            log.info("gemini_raw_saved path=%s", str(raw_response_path))

        data = json.loads(text)
        return schema_model.model_validate(data)
