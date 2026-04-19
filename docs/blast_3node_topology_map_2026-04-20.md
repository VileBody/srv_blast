# Blast 3-Node Topology Map (2026-04-20)

## Целевая схема

- `blast-ops` (mgmt/ingress/monitoring/logs/runner)
- `orchestrator-0` (prod worker node)
- `orchestrator-1` (prod worker node)
- `blast-worker-node-0` (Windows render pool member)
- `blast-render-node-dist` (Windows render pool member)

Shared managed:

- Redis (shared)
- Postgres (shared)
- S3 (shared)

## Service placement

### blast-ops

- ingress nginx (API + webhook routing)
- `tg-bot` (team/internal)
- `asset-ui`
- `finance-bot`
- observability: `loki`, `promtail`, `prometheus`, `alertmanager`, `grafana`, `dozzle`
- logs pipeline V2 (`scripts/logs_pipeline.py` + systemd timers)
- self-hosted GitHub runner (`blast-deploy-infra`)

### orchestrator-0 / orchestrator-1

- `orchestrator-api`
- `worker-build`
- `worker-render`
- `tg-bot-public` (webhook mode)
- `promtail-edge` (logs -> Loki on blast-ops)

### Windows render pool

- existing Windows nodes, dispatch via current orchestrator Windows contract.

## Queue pinning contract

- Node-local queues:
  - `build.orchestrator-0`, `render.orchestrator-0`
  - `build.orchestrator-1`, `render.orchestrator-1`
- Job metadata stores:
  - `origin_node`
  - `build_queue`
  - `render_queue`
- Requeue preserves original node queue binding.

## Telegram public bot delivery mode

- `TG_DELIVERY_MODE=webhook` for prod worker nodes.
- Nginx ingress on blast-ops forwards webhook path to `tg-bot-public` upstream pool.
- Redis dedup by `update_id` protects from duplicate webhook deliveries.
- Rollback mode: `TG_DELIVERY_MODE=polling`.

## CI/CD model

- Trigger: `push main`.
- Runner host: only `blast-ops`.
- Infra deploy: local `infra-ops` on blast-ops.
- Prod deploy: SSH fan-out to `orchestrator-0` and `orchestrator-1`.
- Workflow: `.github/workflows/deploy-split-main.yml`.

## Timeweb cutover notes

Current project: `blast` (`id=1955611`).

Safe rename/create sequence:

1. Create `blast-ops-new` (same preset as current `blast-ops`).
2. Bootstrap deploy user/docker/repo/runner on `blast-ops-new`.
3. Cut over runner + infra-ops to `blast-ops-new`.
4. Rename:
   - current `orchestrator` -> `orchestrator-0`
   - current `blast-ops` -> `orchestrator-1`
   - `blast-ops-new` -> `blast-ops`
5. Enable fan-out deploy + ingress upstreams for both worker nodes.

API inventory command (read-only):

```bash
set -a; source .env.iac; set +a
curl -sS -H "Authorization: Bearer $TWC_TOKEN" \
  https://api.timeweb.cloud/api/v1/projects/1955611/resources | jq '.servers[] | {id, name, preset_id, status}'
```
