# Rust Gen Canary Runbook

`render_engine=rust-gen` is an explicit, native-only route. It does not fall
back to the AE node when the manager is unavailable or rejects a request.

## Prerequisites

- The Rust manager `/health` responds from `RUST_GEN_MANAGER_URL`.
- The manager token matches `RUST_GEN_MANAGER_TOKEN` when authentication is on.
- The manager host can resolve and download the presigned Timeweb S3 URLs.
- `S3_BUCKET_OUTPUT_VIDEO` is available to the orchestrator. Completed video is
  uploaded to `renders/<job_id>/output.mp4`, preserving the existing bot contract.

## Enable a Canary

1. Deploy the manager image and verify its health endpoint from the
   orchestrator network.
2. Set `RUST_GEN_ENABLED=1`, `RUST_GEN_MANAGER_URL`, and
   `RUST_GEN_MANAGER_TOKEN` in the orchestrator runtime.
3. Keep `RUST_GEN_CANARY_ENABLED=1` and list only the subtitle modes that are
   ready in `RUST_GEN_CANARY_SUBTITLE_MODES`.
4. Set `RUST_GEN_BOT_DEFAULT_ENABLED=1` only on the bot instance selected for
   the canary. Other bot instances continue sending `render_engine=ae`.
   Alternatively, keep the default off and put only reviewer IDs in
   `RUST_GEN_ALLOWED_CHAT_IDS`; those chats can opt in with hidden `/rustgen`
   and return to AE with `/ae`.
5. Submit one real render, then verify the job response contains
   `result.rust_gen`, `output_url`, and, when uploaded, `output_manifest_url`.

## Observe

- Logs: `rust_gen_dispatch_attempt`, `rust_gen_dispatch_accepted`, and
  `rust_gen_render_outcome` include job and render IDs without presigned URLs.
- Redis/Prometheus-labelled counters: `rust_gen_dispatch_total` and
  `rust_gen_poll_total`, labelled by subtitle mode and outcome.
- Investigate `error`, `bad_response`, `timeout`, or a missing `video` artifact
  before expanding the canary.

## Roll Back

Set `RUST_GEN_BOT_DEFAULT_ENABLED=0` first so new jobs continue on AE. Existing
Rust jobs continue polling and retain their result. To hard-stop native dispatch,
also set `RUST_GEN_ENABLED=0`; an explicit Rust request then fails visibly at the
API boundary rather than routing through AE.
