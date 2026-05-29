# mlcore/hooks/f5_cognition/prompts/__init__.py
from mlcore.hooks.f5_cognition.prompts.stage1 import (
    build_system_prompt,
    build_user_prompt,
)

__all__ = ["build_system_prompt", "build_user_prompt"]
