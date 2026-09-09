-- Состояние приложения (Postgres-вариант). SQLite-близнец: 002_app_state.sqlite.sql.
--
-- Хранение документами (JSONB): приложение мутирует словари на месте, и документ
-- один-в-один повторяет то, что было в памяти. Разложить проекты/треки по отдельным
-- таблицам можно следующей миграцией — контракт репозитория при этом не меняется.

-- Реестр личностей (раньше backend/data/users.json)
CREATE TABLE IF NOT EXISTS app_users (
    key        TEXT PRIMARY KEY,          -- email или tg:<chat_id>
    user_id    TEXT NOT NULL,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS app_users_user_id_idx ON app_users (user_id);

-- Воркспейс = все данные одного юзера (профиль, подписка, проекты, треки, исходники)
CREATE TABLE IF NOT EXISTS workspaces (
    user_id    TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Батч генерации со всеми роликами
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    project_id      TEXT,
    idempotency_key TEXT,
    data            JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_user_idx ON jobs (user_id);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_idem_idx
    ON jobs (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Итерации (A/B-прогоны) хранятся списком на проект
CREATE TABLE IF NOT EXISTS iterations (
    project_id TEXT PRIMARY KEY,
    data       JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Поток продуктовых событий: из него считаются воронка, удержание и сводки
CREATE TABLE IF NOT EXISTS analytics_events (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    user_id TEXT,
    ts      TEXT NOT NULL,
    props   JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS analytics_events_ts_idx ON analytics_events (ts);
