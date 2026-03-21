from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import base64
import json
import logging

import httpx
from pydantic import BaseModel

from mlcore.cr_patch import patch_payload_dict_inplace
from mlcore.gemini_client import _sanitize_payload_dict
from mlcore.models import BlocksTokensPayload


@dataclass(frozen=True)
class OpenRouterSettings:
    api_key: str
    model: str
    temperature: float = 0.0
    timeout_s: float = 120.0
    base_url: str = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(
        self,
        settings: OpenRouterSettings,
        *,
        logger: Optional[logging.Logger] = None,
        request_func: Optional[Callable[..., httpx.Response]] = None,
    ):
        self._logger = logger or logging.getLogger("mlcore.openrouter_client")
        self._api_key = (settings.api_key or "").strip()
        self._model = (settings.model or "").strip()
        self._temperature = float(settings.temperature)
        self._timeout_s = float(settings.timeout_s)
        self._base_url = (settings.base_url or "").rstrip("/")
        self._request_func = request_func or httpx.post

        if not self._api_key:
            raise RuntimeError("OpenRouterSettings.api_key is empty")
        if not self._model:
            raise RuntimeError("OpenRouterSettings.model is empty")
        if not self._base_url:
            raise RuntimeError("OpenRouterSettings.base_url is empty")

    def _audio_format(self, p: Path) -> str:
        ext = p.suffix.lower().lstrip(".")
        if ext in {"mp3", "wav", "m4a", "aac", "flac", "ogg"}:
            return ext
        raise RuntimeError(f"openrouter_unsupported_audio_format: {p.name}")

    def _audio_part(self, p: Path) -> Dict[str, Any]:
        raw = p.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "type": "input_audio",
            "input_audio": {
                "data": b64,
                "format": self._audio_format(p),
            },
        }

    def _extract_text(self, obj: Dict[str, Any]) -> str:
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"openrouter_bad_response_no_choices: {obj!r}")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise RuntimeError(f"openrouter_bad_response_no_message: {obj!r}")

        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            pieces: List[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
            text = "".join(pieces).strip()
            if text:
                return text
        raise RuntimeError(f"openrouter_bad_response_no_text_content: {obj!r}")

    def _strip_json_fence(self, text: str) -> str:
        s = (text or "").strip()
        if not s.startswith("```"):
            return s
        lines = s.splitlines()
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip() == "":
            lines.pop()
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _post_chat_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = self._request_func(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as e:
            raise RuntimeError(f"openrouter_timeout: {e!r}") from e
        except httpx.TransportError as e:
            raise RuntimeError(f"openrouter_transport_error: {e!r}") from e

        if int(resp.status_code) >= 400:
            body = ""
            try:
                body = resp.text
            except Exception:  # noqa: BLE001
                body = "<unreadable>"
            raise RuntimeError(
                f"openrouter_http_error status={resp.status_code} body={body[:2000]!r}"
            )

        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            text = ""
            try:
                text = resp.text
            except Exception:  # noqa: BLE001
                text = "<unreadable>"
            raise RuntimeError(
                f"openrouter_bad_json_response err={e!r} text_head={text[:2000]!r}"
            ) from e

    def _messages(
        self,
        *,
        prompt: str,
        system_instruction: Optional[str],
        audio_paths: Optional[List[Path]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if system_instruction:
            out.append({"role": "system", "content": system_instruction})

        user_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for p in audio_paths or []:
            pp = p.expanduser().resolve()
            if not pp.exists():
                raise FileNotFoundError(str(pp))
            user_parts.append(self._audio_part(pp))
        out.append({"role": "user", "content": user_parts})
        return out

    def _response_format(self, *, schema_model: type[BaseModel]) -> Dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema_model.__name__,
                "strict": True,
                "schema": schema_model.model_json_schema(),
            },
        }

    def _generate_text(
        self,
        *,
        schema_model: type[BaseModel],
        prompt: str,
        system_instruction: Optional[str],
        audio_paths: Optional[List[Path]],
        raw_response_path: Optional[Path],
    ) -> str:
        payload = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": self._messages(
                prompt=prompt,
                system_instruction=system_instruction,
                audio_paths=audio_paths,
            ),
            "response_format": self._response_format(schema_model=schema_model),
            "provider": {
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        }
        self._logger.info("openrouter_call model=%s timeout_s=%s", self._model, self._timeout_s)
        obj = self._post_chat_completion(payload)
        text = self._extract_text(obj)

        if raw_response_path is not None:
            raw_response_path.parent.mkdir(parents=True, exist_ok=True)
            raw_response_path.write_text(text, encoding="utf-8")
            self._logger.info("openrouter_raw_saved path=%s", str(raw_response_path))

        return text

    def generate_tokens_structured(
        self,
        *,
        prompt: str,
        system_instruction: Optional[str] = None,
        audio_paths: Optional[List[Path]] = None,
        raw_response_path: Optional[Path] = None,
    ) -> BlocksTokensPayload:
        text = self._generate_text(
            schema_model=BlocksTokensPayload,
            prompt=prompt,
            system_instruction=system_instruction,
            audio_paths=audio_paths,
            raw_response_path=raw_response_path,
        )
        try:
            data = json.loads(self._strip_json_fence(text))
            if isinstance(data, dict):
                data = _sanitize_payload_dict(data)
                patch_payload_dict_inplace(data)
            return BlocksTokensPayload.model_validate(data)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "openrouter_tokens_schema_validation_failed "
                f"err={e!r} text_head={text[:8000]!r}"
            ) from e

    def generate_structured(
        self,
        *,
        schema_model: type[BaseModel],
        prompt: str,
        system_instruction: Optional[str] = None,
        audio_paths: Optional[List[Path]] = None,
        raw_response_path: Optional[Path] = None,
    ) -> BaseModel:
        text = self._generate_text(
            schema_model=schema_model,
            prompt=prompt,
            system_instruction=system_instruction,
            audio_paths=audio_paths,
            raw_response_path=raw_response_path,
        )
        try:
            data = json.loads(self._strip_json_fence(text))
            return schema_model.model_validate(data)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "openrouter_schema_validation_failed "
                f"schema={schema_model.__name__} err={e!r} text_head={text[:8000]!r}"
            ) from e

