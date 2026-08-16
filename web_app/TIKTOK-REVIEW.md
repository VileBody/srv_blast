# TikTok for Developers — подача на верификацию Blast

Единый рабочий документ: что заполнять, какими словами, что доделать до подачи.
Открывается с любого устройства, любому агенту достаточно этого файла — контекст внутри.

**Статус:** заявка не подана. Блокеры перечислены в §4 — без них будет отказ.
**Срок ревью:** 1–2 недели на чистую подачу. Ускорить нельзя, платного трека нет.
**Правило:** каждый отказ = новый круг ожидания. Дешевле закрыть §4 полностью, чем подавать «как есть».

---

## 1. Что мы просим у TikTok и зачем

Три продукта, каждый нужно обосновать отдельно:

| Продукт | Скоупы | Зачем в Blast |
|---|---|---|
| **Login Kit** | `user.info.basic` | Подключение аккаунта артиста: показываем ник и аватар, чтобы человек видел, в какой аккаунт уйдёт ролик |
| **Content Posting API** (Direct Post) | `video.publish` | Публикация сгенерированных lyric-видео в аккаунт артиста по его команде |
| **Display API** | `video.list` | Аналитика: просмотры/лайки/комментарии по опубликованным роликам — на них строится разбор «какой параметр сработал» |

Больше скоупов не просить. Scope creep — одна из частых причин отказа.

---

## 2. Поля в кабинете (developers.tiktok.com → Manage apps)

### 2.1 Basic information

| Поле | Что вписать |
|---|---|
| App name | `Blast` — НЕ использовать слова TikTok/Tik Tok в названии, это автоматический отказ |
| App icon | 1024×1024 PNG/JPEG, до 5 МБ. Взять `srv_blast/landing/assets/logo.svg` → отрендерить в PNG 1024×1024 на тёмном фоне |
| Category | Content Creation / Marketing (ближайшее по смыслу) |
| Description | Текст из §3.1 |
| Terms of Service URL | `https://<домен>/legal/offer` |
| Privacy Policy URL | `https://<домен>/legal/policy` |
| Website URL | `https://<домен>` — лендинг, не форма входа |

⚠️ Требование ревью: **ссылки на Privacy Policy и Terms должны быть видны на сайте без навигации по меню** — то есть в футере лендинга, прямыми ссылками. И сайт должен быть полноценным, а не заглушкой с формой входа.

### 2.2 Platform
Web → официальный сайт `https://<домен>`.

### 2.3 Products

**Login Kit**
- Redirect URI: `https://<домен>/api/tiktok/callback`
  (в коде: `TIKTOK_REDIRECT_URI`, дефолт сейчас `http://localhost:5173/app/profile/tiktok/callback` — обязательно заменить и синхронизировать с `.env`)
- Scopes: `user.info.basic`

**Content Posting API**
- Включить **Direct Post** configuration
- Scope: `video.publish`

**Display API**
- Scope: `video.list`

### 2.4 URL properties (верификация домена)
Нужна, потому что мы публикуем через `PULL_FROM_URL` — TikTok сам забирает mp4 по ссылке
(`tiktok_api.init_direct_post_pull`). Без верификации домена/префикса этот путь не работает.

Верифицировать нужно **домен, откуда отдаются mp4** — то есть S3-бакет
(`s3.twcstorage.ru/...`), а не только сайт. Если верификация чужого домена невозможна —
переключиться на `FILE_UPLOAD` (он уже реализован: `tiktok_api.init_direct_post_file`)
либо отдавать mp4 со своего домена через прокси.

**Это решение нужно принять ДО подачи** — от него зависит демо-видео.

---

## 3. Готовые тексты (копировать как есть)

### 3.1 App description

> Blast is a web service for independent music artists. An artist uploads a track, and Blast
> automatically generates a batch of vertical lyric videos (1080×1920) with synced subtitles,
> visual effects and licensed footage, then helps publish them to the artist's own TikTok
> account and shows which creative choices performed best.
>
> The artist connects their TikTok account once, chooses a video from the generated batch,
> writes a caption, selects a privacy level and interaction settings, and publishes. Blast
> then reads public statistics of those posts to tell the artist which footage, subtitle style
> or hook drove the most views — so the next batch is based on data, not guesswork.
>
> Blast is a commercial product with paid subscriptions, operated by IE Chernov Nikita Romanovich
> (Russia). It is not a personal or test project.

### 3.2 Обоснование по продуктам (поле «how each product is used»)

**Login Kit / `user.info.basic`**
> Used once, when an artist connects their TikTok account in their Blast profile. We store the
> open_id to link the account, and display the creator's nickname and avatar in the UI so the
> artist always sees which TikTok account a video will be published to. We do not read anything
> else from the profile.

**Content Posting API / `video.publish`**
> Every publication is initiated manually by the artist from the "Post to TikTok" screen. Before
> publishing we call the creator_info endpoint and render the creator's nickname, a privacy level
> selector with no pre-selected value, interaction toggles (comment / duet / stitch) that are
> disabled and greyed out when the creator's account disallows them, a commercial content
> disclosure block, a preview of the video and the required Music Usage Confirmation consent.
> After sending we poll publish/status/fetch and show the real processing state. Blast never
> posts automatically, on a schedule, or in bulk without an explicit per-video action.

**Display API / `video.list`**
> Used to read public metrics (views, likes, comments, shares) of the videos published through
> Blast, in order to show the artist which creative parameter performed best. Data is shown only
> to the artist who owns the account and is never aggregated across users or resold.

### 3.3 Data handling (если спросят отдельно)
> We store the TikTok open_id, display name, avatar URL and OAuth tokens. Tokens are encrypted at
> rest with a key kept separately from the database and are never exposed to the browser.
> Disconnecting TikTok in the profile deletes the tokens immediately. Card data is never handled
> by us (payments go through T-Bank). Full details: https://<домен>/legal/policy

---

## 4. Блокеры — доделать ДО подачи

Требования взяты из TikTok Content Sharing Guidelines. Ревьюер проверяет их по демо-видео
и по живому сайту. Каждый невыполненный пункт = отказ.

| # | Требование | Как сейчас | Файл |
|---|---|---|---|
| 1 | Тумблер **Stitch** обязателен для видео | ❌ нет: есть только Comment и Duet | `TikTokPostPage.tsx` ~573–580 |
| 2 | Отключённые аккаунтом взаимодействия должны быть **disabled и серые** | ⚠️ значение выставляем в false, но тумблер остаётся кликабельным | `MiniToggle` (стр. 30) не принимает `disabled` |
| 3 | Блок **Commercial content disclosure**: тумблер (по умолчанию выкл) + чекбоксы «Your Brand» / «Branded Content», хотя бы один при включённом тумблере | ❌ нет вообще | `TikTokPostPage.tsx` |
| 4 | Branded content ⇒ приватность только public/friends, «Only me» блокируется | ❌ нет | там же |
| 5 | Текст согласия: **«By posting, you agree to TikTok's Music Usage Confirmation»** со ссылкой; при branded content — плюс ссылка на Branded Content Policy | ⚠️ у нас «Я имею АП на музыку и контент» — не то и без ссылок | ключ `tiktok.rights` |
| 6 | Ник создателя из `creator_info` | ✅ есть | |
| 7 | Селектор приватности без значения по умолчанию | ✅ есть (`privacy = null`) | |
| 8 | Превью ролика | ✅ есть | |
| 9 | Уведомление о времени обработки + polling статуса | ✅ есть | |
| 10 | Ссылки Privacy/Terms доступны на сайте без навигации | ⚠️ на лендинге есть, проверить футер после деплоя | |
| 11 | Домен для `PULL_FROM_URL` верифицирован (или переход на `FILE_UPLOAD`) | ❌ решение не принято | §2.4 |
| 12 | Сайт работает на боевом домене по HTTPS, не localhost | ❌ | `TIKTOK_REDIRECT_URI`, `APP_URL` |

Пункты 1–5 — это правки одного экрана `TikTokPostPage.tsx`. По объёму — один заход.

---

## 5. Демо-видео

Одно видео на весь флоу (можно до 5 роликов по 50 МБ). Снимать на боевом домене,
экран целиком, без ускорения и без монтажа скачками. Первое ревью — из sandbox.

**Сценарий (проговаривать голосом или подписями):**

1. Открыть `https://<домен>` — видно лендинг и в футере ссылки Privacy Policy / Terms.
2. Войти в приложение, открыть Профиль → нажать «Подключить TikTok».
3. Показать экран авторизации TikTok целиком: список запрашиваемых прав. Подтвердить.
4. Вернуться в профиль — показать, что подтянулись **ник и аватар** аккаунта (Login Kit).
5. Открыть проект с готовыми роликами → «Выложить в TikTok».
6. На экране выкладки **медленно показать**: ник создателя, поле описания, селектор приватности
   (что он пуст по умолчанию), тумблеры Comment/Duet/Stitch, блок Commercial content,
   текст Music Usage Confirmation, превью ролика.
7. Заполнить, нажать «Выложить». Показать статус обработки и его смену на завершённый.
8. Открыть TikTok и показать **реально опубликованный ролик** в аккаунте.
9. Вернуться в Blast → раздел Аналитика: показать просмотры/лайки, подтянутые через Display API
   (`video.list`), и вывод «какой параметр сработал».

Пункты 3, 6 и 8 — самые важные: они закрывают «докажи, что интеграция настоящая».

---

## 6. Частые причины отказа → чем закрываем

| Причина | Наш ответ |
|---|---|
| Расплывчатое описание («social media tool») | §3.1: кто пользуется, что именно делает, как часто публикует |
| Privacy policy — заглушка или без упоминания TikTok | Реальный документ, TikTok назван в разделах 2, 5, 6 (какие данные, кому передаём, сроки) |
| Название с упоминанием TikTok | Название `Blast` |
| Сайт — лендинг/логин-форма | Полноценный лендинг + рабочее приложение |
| «Похоже на тестовый проект» | Платные тарифы, оферта, реквизиты ИП, рабочий Telegram-бот |
| Демо не покрывает все скоупы | Сценарий §5 проходит по всем трём продуктам |
| Нарушены Content Sharing Guidelines | §4 — закрыть до подачи |
| Лишние скоупы | Просим ровно три |

---

## 7. Чек-лист подачи

```
[ ] Боевой домен + HTTPS, приложение открывается
[ ] Футер лендинга: прямые ссылки Privacy Policy и Terms
[ ] §4 пункты 1–5 сделаны и видны на экране выкладки
[ ] Решено: PULL_FROM_URL (+верификация домена) или FILE_UPLOAD
[ ] TIKTOK_REDIRECT_URI / APP_URL переведены на боевой домен и совпадают с кабинетом
[ ] Иконка 1024×1024 готова
[ ] Тексты §3 вставлены в поля
[ ] Sandbox: интеграция прогнана, ролик реально опубликован
[ ] Демо-видео по сценарию §5 записано
[ ] Подать → статус In Review (правки в этом состоянии недоступны)
```

---

## 8. После одобрения

- До аудита **все посты уходят в приватном режиме** — это нормально и ожидаемо.
  Публичными они станут после прохождения ревью.
- Обновления приложения делаются через **revision**, боевая версия при этом продолжает работать.
- Если отказали: в кабинете приходит причина; исправляем и подаём заново, счётчик попыток не ограничен.

---

## Контекст для агента, который откроет этот файл

Проект: `Desktop/blast_react_tailwind_fastapi_mock` (веб-приложение), лендинг —
`Desktop/srv_blast/landing`. Экран выкладки: `frontend/src/pages/TikTokPostPage.tsx`.
Клиент TikTok API: `backend/app/tiktok_api.py`, конфиг — `backend/app/tiktok_config.py`
(ключи только из `backend/.env`, в код не писать). Реквизиты и юр-тексты —
`frontend/src/data/legal-docs.ts`. Общий список задач перед деплоем — `PRE-DEPLOY.md`.
