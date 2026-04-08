# Release Smoke Checklist

Этот checklist фиксирует минимальный gate перед релизом и после автодеплоя.

## 1) Health/readiness (серия проверок)

```bash
for i in 1 2 3; do
  curl -fsS "$ORCHESTRATOR_PUBLIC_URL/health" | jq .
  sleep 2
done
```

Ожидание: `ok=true`, `checks.redis=true`, `checks.bundle=true`, `checks.llm_admission_ready=true`.

## 2) Enqueue + render dispatch/poll + result delivery

```bash
make smoke-release ARCHIVAL_AUDIO=/abs/path/to/archival.mp3
```

Команда использует `scripts/run_mr1_smoke_gate.py`:
- synthetic no-speech job;
- real archival `with_gemini` job;
- poll до терминального статуса.

Ожидание: обе job доходят до `SUCCEEDED`, в отчёте есть `output_url`.

## 3) Payment webhook/admin activate (idempotency + unlock path)

Позитивный smoke через admin activate:

```bash
python scripts/run_release_payment_smoke.py \
  --orch "$ORCHESTRATOR_PUBLIC_URL" \
  --chat-id "$SMOKE_CHAT_ID" \
  --credits 1 \
  --admin-token "$PAYMENT_ADMIN_TOKEN"
```

Ожидание:
- первый вызов `activated`;
- повтор с тем же `activation_id` даёт `already_activated`;
- credits не дублируются.

Негативный smoke:
- пустой/битый `X-Admin-Token` -> `403`.

## 4) Базовая диагностика

```bash
curl -fsS "$ORCHESTRATOR_PUBLIC_URL/metrics" | jq .
curl -fsS "$ORCHESTRATOR_PUBLIC_URL/metrics/prometheus" | head -n 40
```

Проверяем, что метрики доступны и содержат:
- `queue_depth`, `inflight_jobs`, `failed_jobs`;
- `llm_inflight_by_worker_type`;
- `webhook_outcomes`, `activate_outcomes`;
- `render_poll_timeout_outcomes`.
- Prometheus exposition содержит новые семейства:
  - `dispatch_attempt_total`, `render_poll_total`, `render_poll_timeout_total`;
  - `gemini_call_total`, `gemini_latency_seconds_bucket`, `gemini_fallback_total`.

## 5) Артефакты gate

Сохраняем:
- JSON-отчёт `out/release_smoke_gate_report.json`;
- ссылку на deploy run;
- job_id smoke задач;
- фрагмент `/health` и `/metrics` на момент gate.
