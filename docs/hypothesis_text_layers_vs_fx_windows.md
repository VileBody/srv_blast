# Hypothesis: Text Timeline Separate From FX Timeline

## Context
Current pipeline binds text segmentation and visual blocks too tightly:
- block boundaries drive both wording and style timing,
- fast rap lines force awkward phrase cuts to satisfy block structure.

## Hypothesis
Split montage logic into two independent timelines:

1. **Text timeline** (dense, readability-driven)  
   - many short text segments,
   - reveal strictly by word timings,
   - segmentation optimized for readability (word/char constraints, natural phrase boundaries).

2. **FX timeline** (coarse, style-driven)  
   - fixed style windows in time (intro/bridge/glitch/dual/finale),
   - effects/transitions keyed by absolute time windows,
   - no dependency on number of text segments inside a window.

## Why this may help
- Keeps readable subtitles in high-BPM fragments.
- Preserves strong visual dramaturgy regardless of segment count.
- Reduces fragile coupling (“one weird phrase split breaks style block”).

## Example mental model
- `0.0–2.2s`: Intro FX window  
- `2.2–5.0s`: Bridge FX window  
- Text inside each window can be 2 segments or 20 segments, same FX preset applies.

## Risks
- More mapping logic: each text segment must be assigned to an FX window by time.
- Some current block-specific assumptions (e.g. mine/glitch coupling) need explicit special rules.
- Migration complexity if done all at once.

## Minimal migration path (future)
1. Keep existing block windows as FX windows.
2. Generate text segments independently from transcript.
3. Map segment -> FX preset by `segment.t_start`.
4. Preserve special-cases (`glitch + mine`, `dual`) as explicit opt-in windows.

## Status
Exploratory hypothesis only.  
No implementation committed under this doc by itself.

