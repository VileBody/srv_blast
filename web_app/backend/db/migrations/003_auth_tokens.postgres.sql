-- Токены подтверждения входа через Telegram (Postgres). Близнец: 003_auth_tokens.sqlite.sql.
--
-- Раньше токены жили в обычном словаре в памяти процесса. Из-за этого бот и веб-процесс
-- могли не видеть один и тот же токен (несколько воркеров, рестарт между «получил ссылку»
-- и «нажал /start»), и подтверждение приходилось повторять по несколько раз.
CREATE TABLE IF NOT EXISTS auth_tokens (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL DEFAULT '',
    purpose    TEXT NOT NULL DEFAULT 'telegram',
    profile    JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified   BOOLEAN NOT NULL DEFAULT false,
    chat_id    TEXT,
    polls      INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_tokens_created_idx ON auth_tokens (created_at);
