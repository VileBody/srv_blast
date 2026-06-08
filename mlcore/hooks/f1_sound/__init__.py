"""F1 «Звук» hook — user-uploaded pre-drop sound + F2-style visual combo.

Unlike F5 «Мысль», F1 has NO LLM/TTS: the user uploads their own sound that
should play before the drop, and the bot threads its S3 URL into the pipeline.

Two parts (orchestrator emits ``full_edit_config["f1"]``):
  1. Audio: the user's sound placed as an audio layer in the pre-drop window
     [0.5s .. drop−0.5s] (see ``inject.py``). No subtitle, nothing picked.
  2. Visual: the SAME combo as F2 minus the pre-drop shapes — F3 ``hook_light``
     on the drop + seeded-random F3 transition on post-drop cuts. Built by
     ``mlcore.hooks.f2_object.overlay.build_overlay_jsx(shape=None, ...)``.

Build-side only; ``project_builder`` injects the audio layer and inlines the
visual JSX. No f1 selection → zero impact on regular jobs.
"""
