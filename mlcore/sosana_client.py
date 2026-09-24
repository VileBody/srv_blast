from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable, Optional

import httpx

from mlcore.openrouter_client import OpenRouterClient, OpenRouterSettings


@dataclass(frozen=True)
class SosanaSettings:
    api_key: str
    model: str
    temperature: float = 0.0
    timeout_s: float = 120.0
    base_url: str = "https://api.sosana.art/api"


class SosanaClient(OpenRouterClient):
    """Sosana's OpenAI-compatible Chat Completions client."""

    _provider_name = "sosana"
    _settings_name = "SosanaSettings"

    def __init__(
        self,
        settings: SosanaSettings,
        *,
        logger: Optional[logging.Logger] = None,
        request_func: Optional[Callable[..., httpx.Response]] = None,
    ):
        super().__init__(
            OpenRouterSettings(
                api_key=settings.api_key,
                model=settings.model,
                temperature=settings.temperature,
                timeout_s=settings.timeout_s,
                base_url=settings.base_url,
            ),
            logger=logger,
            request_func=request_func,
        )

    def _provider_payload(self) -> dict[str, object]:
        # The OpenRouter-specific provider routing object is not part of Sosana's API.
        return {}
