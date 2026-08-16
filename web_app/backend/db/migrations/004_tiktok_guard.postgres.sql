-- Реестр использованных аккаунтов TikTok (Postgres-вариант). Близнец: 004_tiktok_guard.sqlite.sql.
--
-- Правило продукта: один аккаунт TikTok даёт бесплатный лимит только один раз. Проверить
-- это можно только по истории — поэтому связка (аккаунт TikTok → аккаунт в сервисе)
-- пишется НАВСЕГДА и НЕ удаляется вместе с аккаунтом пользователя: иначе обойти правило
-- можно было бы удалением своего аккаунта.
--
-- Таблица хранит только идентификатор open_id, без содержимого профиля TikTok
-- (см. раздел 6 политики конфиденциальности).
CREATE TABLE IF NOT EXISTS tiktok_account_usage (
    open_id       TEXT NOT NULL,          -- идентификатор аккаунта TikTok
    user_id       TEXT NOT NULL,          -- аккаунт в сервисе, который его подключал
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (open_id, user_id)
);
-- Обратный обход: по юзеру — все его аккаунты TikTok (нужен для «кольца» аккаунтов)
CREATE INDEX IF NOT EXISTS tiktok_account_usage_user_idx ON tiktok_account_usage (user_id);
