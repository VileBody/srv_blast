# Team -> Public Mirror Runbook

Цель: любые изменения зеркального `team`-кода должны попадать в `public` в том же PR, с тестами.

## Когда mirror обязателен

Если PR меняет один из файлов:

- `services/tg_bot_botapi/app.py`
- `services/tg_bot_botapi/state_store.py`
- `services/tg_bot_botapi/orchestrator_client.py`
- `services/tg_bot_botapi/audio_prepare.py`
- `services/tg_bot_botapi/s3_client.py`

то в том же PR обязательно должны быть:

- изменение соответствующего `services/tg_bot_public/*` файла;
- изменение как минимум одного public-теста под:
  - `tests/test_tg_bot_public_*`
  - `tests/test_tg_public_*`

## Как проверяется

В CI добавлен gate:

- `scripts/check_team_public_parity.py`
- workflow: `.github/workflows/ci.yml` (`Team/Public parity gate`)

PR не пройдет CI, если есть изменение зеркального team-файла без mirror в public и без public regression tests.

## Рекомендованный порядок работы

1. Внести изменение в `team` файл.
2. Сразу зеркалить логику в `public` файл.
3. Добавить/обновить public-тест.
4. Локально проверить:

```bash
python3 scripts/check_team_public_parity.py --base-ref origin/main --head-ref HEAD
pytest -q tests/test_tg_bot_public_* tests/test_tg_public_*
```

5. Только после этого открывать PR.

## Что делать, если логика team-only

Если изменение реально не должно жить в public:

1. Не вносить его в перечисленные зеркальные файлы.
2. Либо разбить PR: team-only изменения вынести в team-only модули.
3. В PR-описании явно указать rationale, почему mirror не требуется.

Примечание: это сделано специально для исключения ручных переносов и скрытых регрессов.
