# Telegram test control plane

`blast-ops` is the only control plane for Telegram prod/test switching. `orchestrator-0` and `orchestrator-1` only host `tg-bot-public` and execute jobs.

## One-time setup on blast-ops

1. Copy `.env.telegram-test.example` to `/opt/blast/telegram-test/.env`.
2. Fill `TG_TEST_API_ID`, `TG_TEST_API_HASH`, `TG_TEST_CREDITS_DB_URL`, `TG_WEBHOOK_SECRET`, `TG_TEST_AUDIO_PATH`, genre/style labels, and explicit `TG_TEST_NODE0/1_*` connection fields.
3. Optionally set `TG_TEST_OWNER_SESSION_STRING`, but only for a Telegram test account whose phone starts with `99966`.
4. Keep `TG_TEST_BOT_TOKEN` empty if `prepare` should create the bot through test `BotFather`.

## CI control workflow

Use GitHub Actions workflow `Telegram Test Control`.

Recommended sequence:

```bash
prepare
status
enter-test
provision --user-count 50 --concurrency 10
run --user-count 1 --concurrency 1
run --user-count 5 --concurrency 5
run --user-count 10 --concurrency 10
run --user-count 25 --concurrency 10
run --user-count 50 --concurrency 10
exit-test
status
```

`prepare` validates the test DB, creates the test bot if missing, verifies `/test/getMe`, and pushes `.env.telegram-test` to both orchestrator nodes. `enter-test` deletes the prod webhook, restarts `tg-bot-public` with `TG_BOT_API_ENV=test`, and lets the test bot claim the existing public webhook URL. `exit-test` deletes the test webhook and restarts prod bot mode.

## Safety invariants

- Do not use local SSH aliases; all fan-out uses explicit host/user/port/key env.
- Do not copy `TG_TEST_API_HASH` or `TG_TEST_OWNER_SESSION_STRING` to orchestrators.
- Do not use prod `TG_SESSION` unless it is proven to be a test account session.
- Prod bot is intentionally unavailable during the test window.
- Always wait for `0 active jobs` before `enter-test` and before `exit-test`.
