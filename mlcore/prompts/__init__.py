# mlcore/prompts/__init__.py
from __future__ import annotations

from .assemble import build_system_instruction, build_user_prompt

__all__ = ["build_system_instruction", "build_user_prompt"]
