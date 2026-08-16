-- Токены подтверждения входа через Telegram (SQLite). Близнец: 003_auth_tokens.postgres.sql.
--
-- Раньше токены жили в обычном словаре в памяти процесса. Из-за этого бот и веб-процесс
-- могли не видеть один и тот же токен (несколько воркеров, рестарт между «получил ссылку»
-- и «нажал /start»), и подтверждение приходилось повторять по несколько раз.
CREATE TABLE IF NOT EXISTS auth_tokens (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL DEFAULT '',
    purpose    TEXT NOT NULL DEFAULT 'telegram',
    profile    TEXT NOT NULL DEFAULT '{}',
    verified   INTEGER NOT NULL DEFAULT 0,
    chat_id    TEXT,
    polls      INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_tokens_created_idx ON auth_tokens (created_at);
