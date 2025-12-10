from __future__ import annotations

import logging
import mimetypes
import os
import time

from google import genai

from config import Config

log = logging.getLogger(__name__)

# Корректный MIME для .m4a
mimetypes.add_type("audio/mp4", ".m4a")


class GenaiClientBase:
    """Низкоуровневый клиент Gemini без доменной логики."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

        if cfg.outbound_proxy:
            os.environ["HTTPS_PROXY"] = cfg.outbound_proxy
            os.environ["HTTP_PROXY"] = cfg.outbound_proxy
            log.info("GenaiClientBase: using outbound proxy %s", cfg.outbound_proxy)
        else:
            os.environ.pop("HTTPS_PROXY", None)
            os.environ.pop("HTTP_PROXY", None)
            log.info("GenaiClientBase: no outbound proxy")

        self.client = genai.Client(api_key=cfg.gemini_api_key)

    def wait_file_active(
        self, file_obj, context: str, poll_interval: float = 2.0, max_wait: float = 600.0
    ):
        """Ждём, пока file_obj.state.name станет 'ACTIVE'."""
        start = time.time()
        name = getattr(file_obj, "name", None)

        while True:
            state = getattr(file_obj, "state", None)
            state_name = getattr(state, "name", None) if state else None

            if state_name == "ACTIVE":
                log.info("[%s] File %s is ACTIVE", context, name)
                return file_obj

            if time.time() - start > max_wait:
                log.error(
                    "[%s] File %s did not become ACTIVE within %.1f seconds (last state=%r)",
                    context,
                    name,
                    max_wait,
                    state_name,
                )
                raise RuntimeError(f"File {name} did not become ACTIVE in {context}")

            log.info(
                "[%s] Waiting for file %s to become ACTIVE (current state=%r)...",
                context,
                name,
                state_name,
            )
            time.sleep(poll_interval)
            file_obj = self.client.files.get(name=name)

    @staticmethod
    def extract_text_or_raise(resp, context: str) -> str:
        """Аккуратно достаём текст/JSON из ответа SDK."""
        raw = getattr(resp, "output_text", None) or getattr(resp, "text", None)
        if not raw:
            try:
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", []) or []:
                        if getattr(part, "text", None):
                            raw = part.text
                            break
                    if raw:
                        break
            except Exception as exc:  # noqa: BLE001
                log.warning("[%s] Failed to inspect response parts: %s", context, exc)

        if not raw:
            log.error("[%s] Empty model output: %r", context, resp)
            raise RuntimeError(f"Empty model output in {context}")

        log.debug("[%s] Raw model output (truncated): %s", context, raw[:500])
        return raw
