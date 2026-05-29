# mlcore/hooks/f5_cognition/__init__.py
"""
F5 Cognition — "Мысль" hook.

Двухступенчатый pipeline:
  Stage 1 (gemini text)  → tts_text + voice_persona + voice_emotion + voice_pacing
  Stage 2 (gemini audio) → синтез голоса
  Mixer  (локально)      → overlay поверх первых N сек фокусного отрывка трека

Точка входа: `f5_cognition.pipeline.generate(F5Request) -> F5Response`.

Спецификация — outputs/tz_f5_gemini_tts.md (v1.2 + правки v1.3 в памяти).
"""
from mlcore.hooks.f5_cognition.models import F5Request, F5Response, F5Device
from mlcore.hooks.f5_cognition.pipeline import generate

__all__ = ["F5Request", "F5Response", "F5Device", "generate"]
