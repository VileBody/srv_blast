# Dynamic CTC window policy

`local-ctc-viterbi-v10-dynamic-window-redaction-espeak-demucs-4.1.0`
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

Accepted candidates are grouped by per-word start/end stability. The selected
candidate is the medoid of the largest high-scoring consensus group. If no
group reaches the required size, alignment fails explicitly with
`ALIGNMENT_WINDOW_MISMATCH`; there is no Gemini fallback.

## Configuration

- `ALIGNMENT_DYNAMIC_WINDOW_MAX_ADJUST_SEC=2.0`
- `ALIGNMENT_DYNAMIC_WINDOW_STEP_SEC=0.5`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_EDGE_CLEARANCE_SEC=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_STABILITY_TOLERANCE_SEC=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_CONSENSUS_CANDIDATES=3`
- `ALIGNMENT_DYNAMIC_WINDOW_SCORE_TOLERANCE=0.12`
- `ALIGNMENT_DYNAMIC_WINDOW_MIN_BOUNDARY_DURATION_RATIO=0.15`

At most three adjustment magnitudes are used, producing no more than 25 search
windows. Changing these values changes alignment semantics and must be paired
with a new `ALIGNMENT_ALGORITHM_VERSION` so cached Stage 1 payloads cannot be
mixed across policies.

## Diagnostics

The alignment response contains `diagnostics.dynamic_window` with candidate,
rejection, consensus, selected-window and timing-deviation metrics. Candidate
diagnostics contain indexes and scores only, never the reference text.
