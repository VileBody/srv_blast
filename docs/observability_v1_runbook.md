# Observability V1 Runbook

Snapshot: `2026-04-08`

## Critical Alerts

### `OrchestratorDown`
1. Check `docker ps` for `orchestrator-api` and recent restart reasons.
2. Open Grafana Explore (Loki) for `service=orchestrator-api` and inspect last errors.
3. Verify Redis reachability from orchestrator container.
4. If crash-loop is config-related, rollback to previous known-good branch via standard deploy workflow.

### `QueueDepthHigh`
1. Check `queue_depth`, `inflight_jobs`, and `llm_inflight_by_worker_type` trends.
2. Inspect stuck jobs in `/admin/jobs` and `worker-build`/`worker-render` logs.
3. Confirm Windows donor node health (`/health`, `/render/{id}` path activity).
4. Scale worker capacity only via compose/deploy config changes (no hotfix shell patches).

### `FailedRatioSpike`
1. Inspect failures by stage (`build` / `dispatch` / `poll`).
2. Correlate with Gemini error classes (`429/503/other`) and fallback rate.
3. Check recent deployments in CI and revert via normal PR rollback if regression detected.

### `RenderPollTimeoutBurst`
1. Confirm current `WINDOWS_RENDER_URL`/pool correctness.
2. Check Windows node API responsiveness and current render process state.
3. Validate S3 output path existence for timed-out jobs (`dispatch_recovery` markers).

### `Gemini429503ShareHigh`
1. Check model-level call distribution and `gemini_fallback_total` trend.
2. If sustained, reduce ingress pressure (admission limits / queue policy) through config rollout.
3. Keep no-fallback contract except explicit transient fallback rules already in runtime.

## Warning Alerts

### `GeminiFallbackRateHigh`
- Investigate model saturation before it turns into hard failure.
- Verify primary model health and stage-specific spikes.

### `DispatchRecoveryFrequent`
- Indicates dispatch failures with existing output present.
- Review dispatch retries/backoff and Windows endpoint stability.

### `BuildStageLatencyP95High`
- Check build stage logs and Gemini latency panels.
- Validate that queue depth is not driving latency artifact.

## Operational Notes

- Alert routing: Alertmanager -> Telegram channel.
- Dashboards source-of-truth: provisioned JSON under `infra/runners/observability/grafana/dashboards`.
- Config changes are made only via PR + CI + deploy-runner.
- Public access should be via nginx + Basic Auth only:
  - `https://blast808.com/admin/obs/grafana/`
  - `https://blast808.com/admin/obs/prometheus/`
  - `https://blast808.com/admin/obs/alertmanager/`
- If Grafana returns 404 after successful login, verify:
  - `GRAFANA_ROOT_URL=https://blast808.com/admin/obs/grafana/`
  - `GRAFANA_SERVE_FROM_SUB_PATH=true`
  - then restart observability stack (`docker compose -f infra/runners/docker-compose.observability.yml --env-file infra/runners/.env.observability up -d`).
