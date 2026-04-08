# Tech Debt Closure Checklist (2026-04-08)

Цель: закрыть остаточный техдолг без hotfix-практик, через прозрачный CI/CD и проверяемые DoD.

## 1) Dispatch Contract Unification (sync/async)

- [x] Зафиксировать единый контракт render dispatch (`/render` async + poll), убрать неявные fallback переходы.
- [x] Явно задать API mode через конфиг и задокументировать в runbook.
- [ ] Добавить smoke-кейс: timeout dispatch + existing output в S3 -> `SUCCEEDED` без re-dispatch.
- [ ] Добавить метрику/алерт: `dispatch_timeout_but_output_exists`.
- [ ] DoD: 0 повторных re-dispatch при наличии `output.mp4` на канареечных прогонах.

## 2) E2E Safety Net (не только targeted/source tests)

- [ ] Вынести минимальный nightly e2e-smoke (enqueue -> build -> dispatch -> poll -> delivery).
- [ ] Добавить отдельный smoke на payment-confirmed -> credits exactly once.
- [ ] Сделать артефакты e2e обязательными (job_id, stage timeline, output_url).
- [ ] DoD: 7 дней подряд e2e-green без ручных ретраев.

## 3) Team -> Public Mirror Discipline (через CI)

- [x] Ввести CI parity-gate: изменения в зеркальных `tg_bot_botapi/*` требуют зеркала в `tg_bot_public/*`.
- [x] Обязать обновление public-тестов в том же PR при зеркальном переносе.
- [x] Добавить скрипт проверки в репозиторий: `scripts/check_team_public_parity.py`.
- [x] Вынести в отдельный short runbook “как делать mirror PR без ручного переноса” (`docs/team_public_mirror_runbook.md`).
- [ ] DoD: ни один PR с изменениями `team`-зеркала не проходит CI без `public`+tests.

## 4) Batch Failure Branch Coverage

- [x] Добавить тест на `master_failed` с корректным user flow + refund behavior.
- [x] Добавить тест на `enqueue_next_version_failed` и корректный terminal state.
- [x] Добавить тест на partial success (>=1 success, >=1 fail) с ожидаемым итогом.
- [x] DoD: покрыты все критичные terminal ветки batch-машины.

## 5) Windows Render Operational Debt (single-node ceiling + switchover)

- [ ] Прогнать runbook на donor node end-to-end и обновить операционные шаги по факту.
- [ ] Зафиксировать стандарт запуска (single uvicorn process, env-source, session model).
- [ ] Внести switchover checklist для donor/clone без ручных разъездов конфигов.
- [ ] Подготовить CI/CD path для runtime папки win-node (без ручного копирования).
- [ ] DoD: 3 последовательных dispatch smoke на donor проходят по регламенту.

## Execution Order

1. Team->Public CI discipline (уже стартовали, закрепить runbook).
2. Batch failure test coverage.
3. Dispatch contract unification + metrics/alerts.
4. Windows operational runbook + rollout hygiene.
5. Nightly e2e safety net и стабилизация.
