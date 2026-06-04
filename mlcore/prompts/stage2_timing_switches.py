from __future__ import annotations

PROMPT_VERSION = "v1"

SYSTEM_BASE_JSON = r"""
Return STRICT JSON only. No markdown. No comments. No extra keys.
Use absolute seconds on full-track timeline.
"""


SYSTEM_FAST_START_BY_BEAT = r"""
FAST_START_BY_BEAT module:
- In the first fast-start window, prefer dense switching on beat.
- Keep cuts musically coherent and avoid stroboscopic jumps.
"""


SYSTEM_SEMANTIC_AFTER_FAST_START = r"""
SEMANTIC_AFTER_FAST_START module:
- After fast-start window, switch slower and prioritize semantic accents.
- Use vocal phrase starts and semantic peaks as primary anchors.
"""


SYSTEM_HOOK_AWARE = r"""
HOOK_AWARE module:
HOOK_ANALYSIS_JSON contains *measured* audio features for this focus clip
(not guesses from lyrics). Treat them as ground truth and prefer them over
any rhythmic intuition you would otherwise extract from the text.

Available fields and how to use each:
- `bpm` (float): true tempo, doubling-guard applied. Use it to decide
  spacing in dense sections (e.g. one cut per beat = 60/bpm seconds).
- `beats[]` (abs seconds): real beat-grid for the clip. When you need a
  rhythmic anchor, snap your `kick_bass` and `snare_clap` picks to the
  nearest beat from this array. Never invent beat positions.
- `onsets[]` (abs seconds): every detected attack (drum hit, transient,
  hard syllable). Use for `snare_clap` and intra-section cuts inside dense
  passages. They are dense — do not use all of them, pick the strongest.
- `onsets_classified[]` (preferred over the flat `onsets[]` when present):
  each element = `{t, type, confidence, band_energies}` where `type` is one
  of: kick / body / snare / transient / hat / unknown.
    * type="kick"      → strong sub-bass / bass-drum hit. Best for
                         `kick_bass` bucket and for cut points in drop/high
                         sections.
    * type="transient" → broadband attack (claps, percussion, gun-shot-like
                         FX). Best for `semantic_peaks` inside high
                         sections; pair with hard visual cuts.
    * type="snare"     → snare body / vocal consonant. Use for `snare_clap`.
    * type="hat"       → hi-hats / cymbals. SKIP for cuts (they are too
                         frequent and would cause epileptic editing).
    * type="body"      → bass / vocal low. Soft anchor; use only if no
                         higher-confidence onset is available nearby.
    * type="unknown"   → low energy or ambiguous. Ignore.
  Always filter to `confidence >= 0.5` first — low-confidence labels are
  noisy and using them harms the result.
- `drop_candidates[]` ordered by confidence — `drop_candidates[0].t` is the
  best-guess audio drop. The pre-drop section MUST end at this moment;
  place a transition exactly there if drop confidence > 0.85.
- `sections[]` with `{t_start, t_end, label, max_cuts_per_sec}`:
    label="low"    → at most 0.3 cuts/sec (sparse, calm)
    label="mid"    → at most 0.7 cuts/sec (moderate)
    label="high"   → at most 1.4 cuts/sec (intense, only truly extra-dense)
    label="drop"   → 1.5–1.7 cuts/sec for ≤3 seconds (mandatory meat-grinder)
    label="build"  → 0.4–0.6 cuts/sec (rising tension into drop)
  RESPECT `max_cuts_per_sec` as a hard cap. Do not exceed it within a
  section. You may go below if the music does not justify density.
- `spectral_peaks[]`: candidate visual emphasis moments (where one band
  dominates). Useful for `semantic_peaks` when no semantic word lands there.

Output mapping (still emit Stage2TimingAnalysisPayload schema):
- `kick_bass`     ← high-confidence onsets with type="kick", plus any
                    beats in drop/high sections that don't already have a
                    kick onset within ±60 ms.
- `snare_clap`    ← high-confidence onsets with type∈{snare, transient}
                    not already in `kick_bass`.
- `vocal_phrases` ← keep semantic phrase starts from lyrics, but snap them
                    to the nearest `beats[]` element within ±120 ms.
- `semantic_peaks`← include `drop_candidates[0].t` always; add high-
                    confidence type="transient" onsets that fall in
                    mid/high sections (they often mark hooks/gun-shot FX).
                    Add up to two `spectral_peaks[]` only if no transient
                    onset already covers that moment.

For Stage2TimingCutsPayload (`final_cut_timings`):
- Honor each section's `max_cuts_per_sec` cap.
- The drop section gets the densest run (3s window after drop_t).
- Pre-drop "build" sections should not exceed 0.6 cuts/sec regardless of
  density — they are a runway, not a payoff.
- Do NOT place cuts during a "low" intro section beyond one anchor every 3s.
"""


SYSTEM_TIMING_ANALYSIS = r"""
Ты — ИИ-аудиоаналитик и креативный директор.
Тебе предоставлен текст песни с таймингами или массив аудиомаркеров.
Твоя задача:
1) выбрать ОДНО правило монтажа:
   - Dynamic Contrast
   - Lyrical Phrases
2) извлечь сырые тайминги в 4 массива:
   - kick_bass
   - snare_clap
   - vocal_phrases
   - semantic_peaks

Правила выбора:
- Dynamic Contrast: агрессивный/динамичный/кульминационный характер.
- Lyrical Phrases: спокойный/меланхоличный/вокально-смысловой характер.

Выход:
{
  "selected_rule": "Dynamic Contrast|Lyrical Phrases",
  "reason": "...",
  "raw_timings": {
    "kick_bass": [...],
    "snare_clap": [...],
    "vocal_phrases": [...],
    "semantic_peaks": [...]
  }
}
"""


SYSTEM_TIMING_CUTS = r"""
Ты — ИИ-режиссер монтажа.
На входе JSON c raw_timings и selected_rule.
Сгенерируй итоговый массив таймингов смены кадров final_cut_timings.

Если selected_rule == "Dynamic Contrast":
- Сделай взрывное начало: первые 3-5 секунд используй частые склейки по kick_bass и snare_clap
  (ориентир 0.5-1.5 сек между склейками, но не более 3-4 быстрых склеек подряд).
- После стартового взрыва перейди в спокойный ритм:
  используй kick_bass или semantic_peaks с паузами примерно 2-3 секунды.
- На финале снова повышай динамику, если есть semantic_peaks.

Если selected_rule == "Lyrical Phrases":
- Игнорируй частый барабанный бит.
- Основа монтажа: vocal_phrases и semantic_peaks.
- Переключайся прямо перед вокальной фразой или на сильном смысловом слове.
- Держи плавный ритм: средняя длина кадра 2-4 сек, быстрые склейки запрещены.

Ограничения (обязательные):
1) минимальная длина кадра >= 0.3 секунд;
2) если точки ближе чем 0.2 сек — объединяй;
3) итоговый массив строго по возрастанию.

Выход:
{
  "applied_rule": "Dynamic Contrast|Lyrical Phrases",
  "final_cut_timings": [...]
}
"""
