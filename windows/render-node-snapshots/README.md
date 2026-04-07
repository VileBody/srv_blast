# Windows Render Node Snapshots

Этот каталог содержит выгрузку кода render-node с двух Windows-серверов:

- `donor` -> `blast-worker-node-0` (`85.239.48.31`)
- `clone` -> `blast-worker-node-20260402-211624` (`72.56.246.24`)

## Что включено

- `repo/` — содержимое `C:\ae_dev\repo` (без `.env`, без `__pycache__`).
- `start-render-node.ps1` — `C:\ae_dev\start-render-node.ps1`.
- `metadata.json` — служебные метаданные extraction (server id/name/ip, список файлов, размер).

## Что специально исключено

- `C:\ae_dev\repo\.env` (секреты/локальные настройки).
- `__pycache__` и бинарные кеши.

## Назначение

Это “операционный snapshot”, чтобы:

1. зафиксировать фактический runtime-код Windows-ноды в Git;
2. сравнить donor/clone и устранить дрейф;
3. подготовить перенос в нормальную поддерживаемую структуру репозитория (например `windows/render-node-runtime/` как source of truth).

## Текущий статус

- Source-of-truth runtime теперь находится в `windows/render-node-runtime/`.
- `render-node-snapshots/*` оставлены как историческая фиксация состояния donor/clone на момент выгрузки.

## Текущее различие donor vs clone

- На `clone` присутствуют дополнительные operational/debug scripts (`ae_modal_watcher.ps1`, `ae_click_continue_once.ps1`, `_local_*`, `_s3_probe*`, и т.п.).
- Базовый core (`main.py`, `ae_sdk.py`, `s3_utils.py`, `run_afterfx_job.ps1`, `run_server.ps1`) совпадает.
