# Dynamic CTC window policy

`local-ctc-viterbi-v16-robust-word-stability-redaction-espeak-demucs-4.1.0`
uses one Demucs pass and one Wav2Vec2 inference over an expanded analysis crop.
It then evaluates a bounded set of CTC/Viterbi search windows over slices of the
same emission matrix.

The user clip remains the output contract. Search windows may move or expand,
but a candidate is rejected when any aligned word falls outside the user clip.
This prevents a short clip from compressing the last words into its boundary.

## Candidate acceptance

A candidate must satisfy all of the following:

- every word is fully inside the user clip, within one emission-frame tolerance;
- at least three high-scoring windows agree on both boundaries and at least 90%
  of interior words;
- isolated interior outliers stay below the derived maximum deviation cap;
- each boundary has direct confidence/clearance evidence or stable timing with
  no confident counter-evidence outside the authoritative user clip;
- boundary-word duration per CTC token is not abnormally compressed.

Interior words below `ALIGNMENT_MIN_WORD_CONFIDENCE` remain explicit quality
warnings and lower the candidate score, but do not by themselves reject a
stable window. Music/vocal separation can produce isolated weak interior words
even when the acoustic boundaries and timings agree across window probes.

Hard-valid candidates and evidence-limited probes are grouped with a robust
per-word metric. A confident first or last word keeps the strict stability
tolerance. A weak boundary may vary by one search step plus one emission frame,
because its position is inferred from neighboring probes. At least 90% of
interior words must remain within the strict tolerance; the remaining 10% may
vary only up to the derived cap (`max(3 * tolerance, weak-boundary tolerance)`).
This prevents a short adlib or interjection from splitting an otherwise stable
long fragment while still rejecting broad drift.

Direct confidence and acoustic clearance remain preferred, and the left and
right evidence may come from different probes. When a boundary posterior is
weak, a cluster of at least three independent windows may prove it through
stable timing. This mode is rejected when at least three expanded probes
confidently place that same boundary outside the user clip. A word touching the
user clip is also accepted as a censored observation only when that side is
acoustically confident. These rules never permit output outside the user clip
or compressed boundary words.

The selected result is the medoid of the largest high-scoring supported
consensus group. If timings are unstable, a word is confidently outside the
user clip, or no hard-valid group reaches the required size, alignment fails
explicitly with `ALIGNMENT_WINDOW_MISMATCH`; there is no Gemini fallback.

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
Failures return the same safe numeric candidate summaries under
`error.details`; the client logs them with the request id.
