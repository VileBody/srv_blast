# Prod/Infra Split Rollout (2026-04-10)

## Goal

Separate client-critical path from ops tooling to reduce blast radius:

- `PROD PATH VM`: `orchestrator-api`, `worker-build`, `worker-render`, `tg-bot-public`.
- `INFRA OPS VM`: `tg-bot` (team), `asset-ui/admin`, `finance-bot`, `dozzle`, `loki`, `prometheus`, `grafana`, `alertmanager`, `github-runner`, `alert-bot` (when enabled).

Logs from `PROD PATH VM` are shipped by `promtail-edge` to `Loki` on `INFRA OPS VM`.

## Preconditions

- Two healthy Linux VMs with SSH access.
- Two self-hosted GitHub runners configured:
  - labels: `self-hosted,blast-deploy-prod`
  - labels: `self-hosted,blast-deploy-infra`
- Repo variables configured:
  - `DEPLOY_SPLIT_ENABLED=true`
  - `BLAST_REPO_DIR_PROD`
  - `BLAST_REPO_DIR_INFRA`
  - optional `DEPLOY_PRUNE_OTHER_STACK=true`
- Secrets configured:
  - `DEPLOY_GH_TOKEN`

## Rollout Plan

1. **Bootstrap infra VM**
   - Deploy `infra-ops` stack.
   - Confirm Grafana/Loki/Prometheus/Alertmanager/Dozzle healthy.
   - Keep existing prod services untouched.

2. **Bootstrap prod VM**
   - Deploy `prod-path` stack.
   - Start `promtail-edge` on prod VM with `PROMTAIL_LOKI_URL` to infra Loki.
   - Keep old prod path as fallback until canary passes.

3. **Canary traffic**
   - Run canary jobs end-to-end through prod VM orchestrator.
   - Verify render dispatch, S3 upload, Telegram delivery, and queue health.

4. **Cutover**
   - Switch ingress (nginx/DNS) to new prod VM.
   - Enable `DEPLOY_PRUNE_OTHER_STACK=true` for split deploys.
   - Stop old prod-path services on infra VM.

5. **Stabilization (24h)**
   - Observe latency, queue depth, error rate, Telegram timeouts.
   - Keep rollback path documented and ready.

6. **Finalize**
   - Remove obsolete services and stale config from wrong VM.
   - Update runbooks and on-call playbook.

## Test Checklist (Step-by-step)

### A. Infra VM checks

- [ ] `docker ps` includes: `obs-loki`, `obs-grafana`, `obs-prometheus`, `obs-alertmanager`, `dozzle`, `github-runner-blast`.
- [ ] Grafana login works.
- [ ] Prometheus targets show `up` for intended jobs.
- [ ] Loki query returns fresh container logs.

### B. Prod VM checks

- [ ] `docker ps` includes: `orchestrator-api`, `worker-build`, `worker-render`, `tg-bot-public`, `promtail-edge`.
- [ ] `GET /health` for orchestrator is `200`.
- [ ] Celery workers have no heartbeat/broker-loss errors in startup logs.
- [ ] `promtail-edge` can push to Loki (no retry storm).

### C. End-to-end canary

- [ ] Create one build job from Telegram.
- [ ] Confirm build success (`stage=build` complete).
- [ ] Confirm dispatch success to render node.
- [ ] Confirm output uploaded to S3.
- [ ] Confirm Telegram delivery succeeds within timeout budget.

### D. 24h observation window

- [ ] Latency p50/p95 stable vs pre-cut baseline.
- [ ] Queue depth stable (no prolonged growth).
- [ ] Error rate does not regress.
- [ ] No sustained Telegram timeout spikes.

## Rollback

If canary or 24h checks fail:

1. Re-point ingress to previous known-good VM.
2. Re-enable previous stack (`deploy_branch.sh <branch> all` or explicit stack).
3. Keep split config, but pause cutover until root cause is fixed.

