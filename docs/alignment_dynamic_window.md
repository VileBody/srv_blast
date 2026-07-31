# Dynamic CTC window policy

`local-ctc-viterbi-v12-dynamic-window-redaction-espeak-demucs-4.1.0`
uses one Demucs pass and one Wav2Vec2 inference over an expanded analysis crop.
It then evaluates a bounded set of CTC/Viterbi search windows over slices of the
same emission matrix.

The user clip remains the output contract. Search windows may move or expand,
but a candidate is rejected when any aligned word falls outside the user clip.
This prevents a short clip from compressing the last words into its boundary.

## Candidate acceptance

A candidate must satisfy all of the following:

- every word is fully inside the user clip, within one emission-frame tolerance;
- the first and last words meet `ALIGNMENT_MIN_WORD_CONFIDENCE`;
- the first and last words have acoustic clearance from adjustable search edges;
- boundary-word duration per CTC token is not abnormally compressed.

Interior words below `ALIGNMENT_MIN_WORD_CONFIDENCE` remain explicit quality
warnings and lower the candidate score, but do not by themselves reject a
stable window. Music/vocal separation can produce isolated weak interior words
even when the acoustic boundaries and timings agree across window probes.

Accepted candidates and edge-limited probes are grouped by per-word start/end
stability. Edge-limited probes may support consensus when their timings agree,
but can never be selected as the result. The selected candidate is a fully
accepted medoid from the largest high-scoring consensus group. If no group
reaches the required size or contains a selectable candidate, alignment fails explicitly with
`ALIGNMENT_WINDOW_MISMATCH`; there is no Gemini fallback.

## Configuration

- `ALIGNMENT_DYNAMIC_WINDOW_MAX_ADJUST_SEC=1.0`
- `ALIGNMENT_DYNAMIC_WINDOW_STEP_SEC=0.25`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_EDGE_CLEARANCE_SEC=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_STABILITY_TOLERANCE_SEC=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_CONSENSUS_CANDIDATES=3`
- `ALIGNMENT_DYNAMIC_WINDOW_SCORE_TOLERANCE=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_BOUNDARY_DURATION_RATIO=0.15`

At most three adjustment magnitudes are used, producing no more than 25 search
windows. Changing these values changes alignment semantics and must be paired
with a new `ALIGNMENT_ALGORITHM_VERSION` so cached Stage 1 payloads cannot be
mixed across policies.

The production defaults prioritize dense local probes (`0.25`, `0.5`, and
`1.0` seconds). This keeps enough neighboring windows for a meaningful
consensus when the correct acoustic boundary is only slightly displaced,
instead of spending most probes on distant windows that leave the user clip.

## Diagnostics

The alignment response contains `diagnostics.dynamic_window` with candidate,
rejection, consensus, selected-window and timing-deviation metrics. Candidate
diagnostics contain indexes and scores only, never the reference text.
