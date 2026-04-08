# Windows Node Restart Automation

Этот flow автоматизирует цикл:

1. restart render-node с детальным логом шагов;
2. проверка health;
3. pin runtime-пула orchestrator на test node;
4. canary job через orchestrator;
5. если canary `SUCCEEDED` — добавление test node в pool;
6. если canary `FAILED` — автоматический rollback pool.

## Что добавлено

- `windows/render-node-runtime/restart_node_workflow.ps1`
  - единый restart workflow на ноде;
  - лог в `C:\ae_dev\logs\node_restart_workflow.log`;
  - шаги `WF_STEP=... STATUS=...` для удобного анализа.
- `infra/windows_ops/restart_render_node.yml`
  - ansible playbook для запуска restart workflow через WinRM.
- `scripts/windows_node_rollout.py`
  - end-to-end orchestration скрипт (restart + canary + pool update).
- API orchestrator:
  - `GET /windows-nodes`
  - `PUT /windows-nodes`

## Запуск

Пример (clone node):

```bash
python scripts/windows_node_rollout.py \
  --node-host 72.56.246.24 \
  --node-user Administrator \
  --node-password '<PASSWORD>' \
  --test-node-url http://72.56.246.24:8000 \
  --orchestrator-url http://127.0.0.1:8080 \
  --canary-audio-s3-url 's3://<bucket>/raw/<audio>.mp3' \
  --canary-mode no_gemini \
  --start-afterfx \
  --kill-afterfx-first
```

Скрипт пишет JSON-логи в stdout (`event=...`) и завершится non-zero кодом при любой ошибке.

## API pool management

Текущий runtime-состояние пула:

```bash
curl -s http://127.0.0.1:8080/windows-nodes
```

Явно задать runtime URLs:

```bash
curl -s -X PUT http://127.0.0.1:8080/windows-nodes \
  -H 'Content-Type: application/json' \
  -d '{"urls":["http://85.239.48.31:8000","http://72.56.246.24:8000"]}'
```

Очистить runtime override (fallback на env `WINDOWS_RENDER_URL(S)`):

```bash
curl -s -X PUT http://127.0.0.1:8080/windows-nodes \
  -H 'Content-Type: application/json' \
  -d '{"urls":[]}'
```

## Auto-disable stuck node

Dispatch/poll теперь могут автоматически переводить ноду в `enabled=false` (не удаляя ее из пула):

- `dispatch`: при подряд ошибках отправки (`WINDOWS_NODE_DISABLE_AFTER_DISPATCH_ERRORS`);
- `poll`: при `WINDOWS_POLL_TIMEOUT_S` (`WINDOWS_NODE_DISABLE_ON_POLL_TIMEOUT=1`).

При auto-disable:

- нода остается в `/windows-nodes` (в `nodes`), но пропадает из `effective_urls`;
- инкрементируется метрика `windows_node_state_change_total{event="auto_disabled",...}`;
- при настроенных `ALERT_TELEGRAM_BOT_TOKEN` + `ALERT_TELEGRAM_CHAT_ID` отправляется ops-уведомление.

Пример явного runtime payload с disabled-нодой:

```bash
curl -s -X PUT http://127.0.0.1:8080/windows-nodes \
  -H 'Content-Type: application/json' \
  -d '{
    "nodes": [
      {"url":"http://85.239.48.31:8000","enabled":false,"disabled_reason":"manual_disable"},
      {"url":"http://72.56.246.24:8000","enabled":true}
    ]
  }'
```
