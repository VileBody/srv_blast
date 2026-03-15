# mlcore/gemini_client.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import mimetypes
import os
import time
import logging

from pydantic import BaseModel
from google import genai
from google.genai import types

from mlcore.cr_patch import normalize_segment_inplace
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
    fallback_model: Optional[str] = None
    temperature: float = 0.0
    proxy: str = ""
    timeout_s: float = 120.0
    max_output_tokens: Optional[int] = None
    max_thinking_tokens: Optional[int] = None
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
    if isinstance(seg, dict):
        normalize_segment_inplace(seg, force_two_line=False, mine_mode=False)
    return seg


def _sanitize_mine_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(seg, dict):
        normalize_segment_inplace(seg, force_two_line=False, mine_mode=True)
    return seg


def _sanitize_payload_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply _sanitize_segment everywhere it fits.
    Additionally: repair legacy mine_drop -> mine when possible (variant A).
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

    # ---- Legacy repair: mine_drop -> mine (variant A)
    try:
        b5 = d.get("block_5")
        if isinstance(b5, dict):
            if not isinstance(b5.get("mine"), dict) and isinstance(b5.get("mine_drop"), dict):
                md = b5["mine_drop"]
                txt = str(md.get("text", "") or "")
                t0 = float(md.get("t_start"))
                t1 = float(md.get("t_end"))
                b5["mine"] = {
                    "phrase": "\r" + txt if txt else "",
                    "tokens": [{"text": txt, "t_start": t0, "t_end": t1, "trailing": ""}],
                }

            if isinstance(b5.get("mine"), dict):
                b5["mine"] = _sanitize_mine_segment(b5["mine"])
    except Exception:
        pass

    return d


# =============================================================================
# Gemini Client
# =============================================================================

class GeminiClient:
    """
    IMPORTANT POLICY:
      - No internal retry loops here.
      - Optional single-shot fallback to another Gemini model is allowed
        only for transient capacity/rate-limit failures.
      - If Gemini fails transiently, Celery should retry the whole job.
    """

    def __init__(self, settings: GeminiSettings, *, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger("mlcore.gemini_client")
        self._client = make_client(api_key=settings.api_key, proxy=settings.proxy, timeout_s=float(settings.timeout_s))
        self._model = settings.model
        fm = str(settings.fallback_model or "").strip()
        self._fallback_model: Optional[str] = fm if (fm and fm != self._model) else None
        self._fallback_client: Optional[genai.Client] = None
        if self._fallback_model is not None:
            self._fallback_client = make_client(
                api_key=settings.api_key,
                proxy=settings.proxy,
                timeout_s=float(settings.timeout_s),
            )
        self._temperature = float(settings.temperature)
        self._timeout_s = float(settings.timeout_s)
        self._max_output_tokens: Optional[int] = None
        self._max_thinking_tokens: Optional[int] = None
        if settings.max_output_tokens is not None:
            mot = int(settings.max_output_tokens)
            if mot <= 0:
                raise RuntimeError(f"max_output_tokens must be > 0, got {settings.max_output_tokens!r}")
            self._max_output_tokens = mot
        if settings.max_thinking_tokens is not None:
            mtt = int(settings.max_thinking_tokens)
            if mtt <= 0:
                raise RuntimeError(f"max_thinking_tokens must be > 0, got {settings.max_thinking_tokens!r}")
            self._max_thinking_tokens = mtt
        self._thinking_config = self._build_thinking_config()
        self._max_attempts = int(settings.max_attempts)
        self._upload_max_attempts = self._env_int("GEMINI_UPLOAD_MAX_ATTEMPTS", 4, min_value=1)
        self._upload_backoff_max_s = float(self._env_float("GEMINI_UPLOAD_BACKOFF_MAX_S", 8.0, min_value=0.1))
        self._upload_backoff_base_s = float(self._env_float("GEMINI_UPLOAD_BACKOFF_BASE_S", 1.0, min_value=0.1))

    def _env_int(self, name: str, default: int, *, min_value: int) -> int:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return int(default)
        try:
            val = int(raw)
        except Exception:
            self._logger.warning("gemini_env_parse_failed name=%s value=%r using_default=%s", name, raw, default)
            return int(default)
        if val < min_value:
            self._logger.warning(
                "gemini_env_out_of_range name=%s value=%s min=%s using_min",
                name,
                val,
                min_value,
            )
            return int(min_value)
        return int(val)

    def _env_float(self, name: str, default: float, *, min_value: float) -> float:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            return float(default)
        try:
            val = float(raw)
        except Exception:
            self._logger.warning("gemini_env_parse_failed name=%s value=%r using_default=%s", name, raw, default)
            return float(default)
        if val < min_value:
            self._logger.warning(
                "gemini_env_out_of_range name=%s value=%s min=%s using_min",
                name,
                val,
                min_value,
            )
            return float(min_value)
        return float(val)

    def _exc_text(self, exc: BaseException) -> str:
        parts: List[str] = [type(exc).__name__]
        try:
            parts.append(str(exc))
        except Exception:
            pass
        try:
            parts.append(repr(exc))
        except Exception:
            pass
        return "\n".join([p for p in parts if p])

    def _is_transient_capacity_error(self, exc: BaseException) -> bool:
        text = self._exc_text(exc)
        if not text:
            return False
        lo = text.lower()
        if "503" in lo and "unavailable" in lo:
            return True
        if "429" in lo and (
            "resource_exhausted" in lo
            or "too many requests" in lo
            or "rate limit" in lo
        ):
            return True
        return False

    def _is_transient_upload_error(self, exc: BaseException) -> bool:
        text = self._exc_text(exc)
        lo = text.lower()
        transient_markers = [
            "connection reset by peer",
            "remoteprotocolerror",
            "server disconnected without sending a response",
            "readerror",
            "connecterror",
            "read timeout",
            "timed out",
            "temporarily unavailable",
            "connection aborted",
            "eof occurred in violation of protocol",
            "broken pipe",
        ]
        for marker in transient_markers:
            if marker in lo:
                return True
        if "503" in lo or "429" in lo:
            return True
        return False

    def _upload_file_with_retry(self, p: Path, *, sha_short: Optional[str] = None) -> types.File:
        attempts = int(self._upload_max_attempts)
        base_sleep = float(self._upload_backoff_base_s)
        max_sleep = float(self._upload_backoff_max_s)
        last_exc: Optional[BaseException] = None
        sha_label = str(sha_short or "-")
        for attempt in range(1, attempts + 1):
            try:
                return self._client.files.upload(file=str(p))
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if (not self._is_transient_upload_error(exc)) or attempt >= attempts:
                    raise
                sleep_s = min(max_sleep, base_sleep * (2 ** (attempt - 1)))
                self._logger.warning(
                    "gemini_upload_retry file=%s sha=%s attempt=%d/%d sleep_s=%.2f err=%s",
                    str(p),
                    sha_label,
                    attempt,
                    attempts,
                    sleep_s,
                    self._exc_text(exc),
                )
                time.sleep(sleep_s)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"gemini_upload_unreachable file={p}")

    def _generate_content_with_optional_fallback(
        self,
        *,
        contents: List[object],
        config: types.GenerateContentConfig,
        call_kind: str,
    ) -> Any:
        try:
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except Exception as primary_exc:  # noqa: BLE001
            if self._fallback_model is None:
                raise
            if self._fallback_client is None:
                raise
            if not self._is_transient_capacity_error(primary_exc):
                raise

            self._logger.warning(
                "gemini_model_fallback_triggered kind=%s primary=%s fallback=%s reason=%s",
                call_kind,
                self._model,
                self._fallback_model,
                self._exc_text(primary_exc),
            )
            try:
                resp = self._fallback_client.models.generate_content(
                    model=self._fallback_model,
                    contents=contents,
                    config=config,
                )
                self._logger.info(
                    "gemini_model_fallback_success kind=%s primary=%s fallback=%s",
                    call_kind,
                    self._model,
                    self._fallback_model,
                )
                return resp
            except Exception as fallback_exc:  # noqa: BLE001
                self._logger.warning(
                    "gemini_model_fallback_failed kind=%s primary=%s fallback=%s primary_err=%s fallback_err=%s",
                    call_kind,
                    self._model,
                    self._fallback_model,
                    self._exc_text(primary_exc),
                    self._exc_text(fallback_exc),
                )
                raise fallback_exc from primary_exc

    def _build_thinking_config(self) -> Optional[types.ThinkingConfig]:
        if self._max_thinking_tokens is None:
            return None
        fields = set(getattr(types.ThinkingConfig, "model_fields", {}).keys())
        kwargs: Dict[str, Any]
        if "thinking_budget" in fields:
            kwargs = {"thinking_budget": int(self._max_thinking_tokens)}
        elif "thinkingBudget" in fields:
            kwargs = {"thinkingBudget": int(self._max_thinking_tokens)}
        else:
            self._logger.warning(
                "gemini_thinking_budget_unsupported sdk_thinking_fields=%s requested=%s; "
                "continuing without thinking budget cap",
                sorted(fields),
                self._max_thinking_tokens,
            )
            return None
        return types.ThinkingConfig(**kwargs)

    def _json_generate_cfg(
        self,
        *,
        schema_model: type[BaseModel],
        system_instruction: Optional[str],
    ) -> types.GenerateContentConfig:
        kwargs: Dict[str, Any] = {
            "temperature": self._temperature,
            "response_mime_type": "application/json",
            "response_json_schema": schema_model.model_json_schema(),
            "system_instruction": system_instruction,
        }
        if self._max_output_tokens is not None:
            kwargs["max_output_tokens"] = int(self._max_output_tokens)
        if self._thinking_config is not None:
            kwargs["thinking_config"] = self._thinking_config
        return types.GenerateContentConfig(**kwargs)

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
            uploaded = self._upload_file_with_retry(p, sha_short=sha[:12])
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
            f = self._upload_file_with_retry(p)
            f = self._wait_file_active(f)
            out.append(f)
        return out

    # ==========================================================
    # Minimal probe (text-only)
    # ==========================================================

    def probe_text(self, text: str = "u alive?") -> str:
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
        contents: List[object] = []
        if files:
            contents.extend(files)
        contents.append(prompt)

        cfg = self._json_generate_cfg(
            schema_model=BlocksTokensPayload,
            system_instruction=system_instruction,
        )

        log = self._logger
        log.info(
            "gemini_call_tokens model=%s timeout_s=%s temperature=%s max_output_tokens=%s max_thinking_tokens=%s",
            self._model,
            self._timeout_s,
            self._temperature,
            str(self._max_output_tokens),
            str(self._max_thinking_tokens),
        )

        resp = self._generate_content_with_optional_fallback(
            contents=contents,
            config=cfg,
            call_kind="tokens_structured",
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
        contents: List[object] = []
        if files:
            contents.extend(files)
        contents.append(prompt)

        cfg = self._json_generate_cfg(
            schema_model=schema_model,
            system_instruction=system_instruction,
        )

        log = self._logger
        log.info(
            "gemini_call_generic model=%s max_output_tokens=%s max_thinking_tokens=%s",
            self._model,
            str(self._max_output_tokens),
            str(self._max_thinking_tokens),
        )

        resp = self._generate_content_with_optional_fallback(
            contents=contents,
            config=cfg,
            call_kind="generic_structured",
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
