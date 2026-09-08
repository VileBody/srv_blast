# TikTok for Developers — подача на верификацию Blast

Единый рабочий документ: что заполнять, какими словами, что доделать до подачи.
Открывается с любого устройства, любому агенту достаточно этого файла — контекст внутри.

**Статус:** заявка не подана. Продуктовая часть готова и живёт на боевом домене;
остался один технический блокер — ключи приложения в проде (§4.1) — и съёмка демо (§5).
**Срок ревью:** 1–2 недели на чистую подачу. Ускорить нельзя, платного трека нет.
**Правило:** каждый отказ = новый круг ожидания. Дешевле закрыть §4 полностью, чем подавать «как есть».

**Проверено на проде 2026-09-09** (что реально отдаёт сервер, а не что написано в коде):

| Проверка | Результат |
|---|---|
| `GET https://app.blast808.com/api/tiktok/status` | `redirectUri: https://app.blast808.com/api/tiktok/callback`, `uploadSource: FILE_UPLOAD`, `scopes: user.info.basic,video.list,video.publish` — совпадает с тем, что просим в кабинете |
| Ключи приложения | `configured: false` — `TIKTOK_CLIENT_KEY`/`SECRET` в прод-окружении не заданы, интеграция работает в мок-режиме. **Это блокер демо-видео** |
| Домены | `blast808.com`, `www.blast808.com`, `app.blast808.com` — 200 по HTTPS |
| Футер лендинга | прямые ссылки `terms.html`, `privacy.html`, `cookies.html`, `personal-data-consent.html`, `offer.html`, `contacts.html` — без навигации по меню |
| Юр-тексты | TikTok упомянут в `landing/js/legal-documents.js` 45 раз (какие данные, кому передаём, сроки) |

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
| Terms of Service URL | `https://blast808.com/terms.html` |
| Privacy Policy URL | `https://blast808.com/privacy.html` |
| Website URL | `https://blast808.com` — лендинг, не форма входа |

Даём именно страницы лендинга, а не маршруты приложения (`/legal/policy`, `/legal/offer`):
они открываются без входа, и ревьюер попадёт на документ, а не на экран логина.

⚠️ Требование ревью: **ссылки на Privacy Policy и Terms должны быть видны на сайте без навигации по меню** — то есть в футере лендинга, прямыми ссылками. И сайт должен быть полноценным, а не заглушкой с формой входа.

### 2.2 Platform
Web → официальный сайт `https://blast808.com`.

### 2.3 Products

**Login Kit**
- Redirect URI: `https://app.blast808.com/api/tiktok/callback`
  (прод уже отдаёт ровно этот адрес в `/api/tiktok/status`; дефолт в коде — localhost,
  боевое значение приходит из `TIKTOK_REDIRECT_URI` и сверяется рантайм-контрактом)
- Scopes: `user.info.basic`

**Content Posting API**
- Включить **Direct Post** configuration
- Scope: `video.publish`

**Display API**
- Scope: `video.list`

### 2.4 URL properties (верификация домена)
Production использует `FILE_UPLOAD`: backend забирает готовый MP4 напрямую из настроенного
Timeweb S3 и загружает его по выданному TikTok `upload_url`. URL чужого S3-домена TikTok не
получает, поэтому URL property для `s3.twcstorage.ru` не требуется. Runtime требует
`TIKTOK_UPLOAD_SOURCE=FILE_UPLOAD` и не даст случайно включить `PULL_FROM_URL` в production,
пока у Blast нет собственного верифицированного media-домена.

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
> by us (payments go through T-Bank). Full details: https://blast808.com/privacy.html

---

## 4. Блокеры — доделать ДО подачи

Требования взяты из TikTok Content Sharing Guidelines. Ревьюер проверяет их по демо-видео
и по живому сайту. Каждый невыполненный пункт = отказ.

| # | Требование | Как сейчас | Файл |
|---|---|---|---|
| 1 | Тумблер **Stitch** обязателен для видео | ✅ есть; Comment/Duet/Stitch выключены по умолчанию | `TikTokPostPage.tsx` |
| 2 | Отключённые аккаунтом взаимодействия должны быть **disabled и серые** | ✅ UI блокирует; backend отклоняет обходной запрос | `MiniToggle`, `validate_video_post_settings` |
| 3 | Блок **Commercial content disclosure**: тумблер (по умолчанию выкл) + чекбоксы «Your Brand» / «Branded Content», хотя бы один при включённом тумблере | ✅ | `TikTokPostPage.tsx` |
| 4 | Branded content ⇒ приватность только public/friends, «Only me» блокируется | ✅ UI сбрасывает выбор; backend валидирует | frontend + `tiktok_api.py` |
| 5 | Текст согласия: **«By posting, you agree to TikTok's Music Usage Confirmation»** со ссылкой; при branded content — плюс ссылка на Branded Content Policy | ✅ | i18n + `TikTokPostPage.tsx` |
| 6 | Ник создателя из `creator_info` | ✅ есть | |
| 7 | Селектор приватности без значения по умолчанию | ✅ есть (`privacy = null`) | |
| 8 | Превью ролика | ✅ есть | |
| 9 | Уведомление о времени обработки + polling статуса | ✅ есть | |
| 10 | Ссылки Privacy/Terms доступны на сайте без навигации | ✅ проверено на живом лендинге 2026-09-09 | `landing/index.html` |
| 11 | Домен для `PULL_FROM_URL` верифицирован (или переход на `FILE_UPLOAD`) | ✅ выбран `FILE_UPLOAD` | §2.4 |
| 12 | Сайт работает на боевом домене по HTTPS, не localhost | ✅ `app.blast808.com` в проде, redirect URI боевой | `TIKTOK_REDIRECT_URI`, `APP_URL` |
| 13 | Ключи приложения заданы в проде (иначе OAuth и Direct Post идут в мок) | ❌ `configured: false` — см. §4.1 | прод-окружение |

Пункты 1–11 закрыты в коде и проверены тестами `tests/test_web_tiktok_posting.py`
(4 кейса: состав payload и отсутствие `is_aigc`, отклонение запрещённых аккаунтом
взаимодействий, branded content ≠ private, недоступный уровень приватности).
Открыт только №13 — и он же держит демо-видео.

### 4.1 Что включить в проде (единственный технический блокер)

Рантайм-контракт (`web_app/backend/app/runtime.py`) требует группу целиком — либо все
переменные, либо ни одной. Половина конфигурации хуже, чем её отсутствие: кнопка
«Подключить TikTok» появится и приведёт человека на ошибку провайдера.

```
TIKTOK_CLIENT_KEY=<из кабинета, Sandbox → потом Production>
TIKTOK_CLIENT_SECRET=<из кабинета>
TIKTOK_REDIRECT_URI=https://app.blast808.com/api/tiktok/callback
TIKTOK_TOKEN_KEY=<ключ шифрования токенов в БД, генерится один раз>
TIKTOK_UPLOAD_SOURCE=FILE_UPLOAD
TIKTOK_ALLOWED_USER_IDS=<e-mail и/или telegram chat id владельца>
```

`TIKTOK_ALLOWED_USER_IDS` — кому интеграция видна. Пока идёт ревью, приложение живёт в
песочнице: авторизоваться и публиковать может только владелец приложения, а остальным
показывать эту кнопку нельзя — они попадут в чужой sandbox и на ошибку авторизации.
Для всех, кого нет в списке, `/api/tiktok/status` отдаёт `configured: false`, и сайт
ведёт себя ровно как сейчас (кнопки нет), а серверные ручки отвечают 503 даже на
собранный руками запрос. Принимаются id пользователя, e-mail и telegram chat id через
запятую, регистр не важен. Пустое значение в проде — ошибка старта: «случайно всем»
здесь хуже, чем «никому». После одобрения заявки открыть всем — явным `*`.

Файл: `/home/deploy/blast_final/web_app/backend/.env.production` (его же проверяет
шаг «Validate deployment contract» в `deploy-web-production.yml`). После рестарта
контейнера `GET /api/tiktok/status` должен вернуть `configured: true` — это и есть
сигнал, что можно снимать демо.

⚠️ `TIKTOK_UPLOAD_SOURCE` в проде обязан быть `FILE_UPLOAD`: рантайм не даст поднять
приложение с `PULL_FROM_URL`, пока у Blast нет своего верифицированного media-домена.

---

## 5. Демо-видео

Одно видео на весь флоу (можно до 5 роликов по 50 МБ). Снимать на боевом домене
`app.blast808.com`, экран целиком, курсор видно, без ускорения и монтажа скачками.
Первое ревью — из sandbox, поэтому TikTok-аккаунт для съёмки должен быть добавлен
в кабинете как **Target user** (Sandbox → Target users), иначе авторизация не пройдёт.

Ревьюер смотрит две вещи: (1) интеграция настоящая и работает, (2) экран публикации
соответствует Content Sharing Guidelines. Всё остальное — контекст, но без него
заявку часто читают как «тестовый проект», поэтому продукт показываем целиком.

**Хронометраж:** 4–6 минут. Комментировать голосом или подписями на английском.

### Часть 1. Продукт настоящий (~60 сек)

1. `https://blast808.com` — лендинг: что за сервис, тарифы. Проскроллить до футера и
   **задержаться на ссылках Privacy Policy / Terms**, открыть Privacy Policy, показать
   раздел про TikTok (какие данные и зачем).
2. Перейти в приложение, войти. Показать, что это рабочий кабинет с проектами, а не заглушка.

### Часть 2. Как рождается ролик (~90 сек)

Это отвечает на вопрос «что вы вообще публикуете» — без него Direct Post выглядит
как безымянный аплоадер.

3. Новый проект → загрузить трек, вписать текст, выбрать отрывок.
4. Быстро пройти визард: **Фон** (футажи/фото/цвет), **Текст** (стили субтитров),
   **FX** (хук, переход, стилизация), **Пул** (распределение по роликам батча).
5. Запустить генерацию, показать прогресс и готовый батч роликов с превью.

### Часть 3. Login Kit (~40 сек)

6. Профиль → **«Подключить TikTok»**.
7. **Показать экран авторизации TikTok целиком**, не пролистывая: список запрашиваемых
   прав должен читаться. Подтвердить.
8. Вернуться в профиль: подтянулись **ник и аватар** аккаунта. Проговорить, что
   `user.info.basic` используется только для этого — показать, в какой аккаунт уйдёт ролик.

### Часть 4. Экран публикации — главная часть (~120 сек)

Открыть проект → выбрать ролик → **«Выложить в TikTok»** и **медленно**, задерживаясь
на каждом элементе, показать (порядок = порядок проверки у ревьюера):

9.  **Ник создателя** из `creator_info` — вслух: данные тянутся из TikTok перед публикацией.
10. **Превью ролика** и выбор кадра обложки.
11. Поле описания.
12. **Селектор приватности — пустой по умолчанию**. Открыть список, показать варианты,
    выбрать. Проговорить: значения приходят из `privacy_level_options` аккаунта.
13. **Тумблеры Comment / Duet / Stitch** — все выключены по умолчанию. Если аккаунт
    что-то запрещает, тумблер серый и не нажимается — показать это (лучше всего
    снимать на аккаунте, где хотя бы одно взаимодействие отключено в настройках TikTok).
14. **Commercial content disclosure**: включить тумблер → появляются чекбоксы
    «Your Brand» / «Branded Content», без выбора одного из них публикация заблокирована.
    Отметить «Branded Content» → показать, что приватность «Only me» становится недоступна.
15. **Текст согласия** «By posting, you agree to TikTok's Music Usage Confirmation»
    (и Branded Content Policy при branded content) — показать, что ссылки кликабельны.
16. Нажать **«Выложить»**. Показать уведомление о времени обработки и **смену статуса**
    (мы поллим `publish/status/fetch`, статус меняется на экране, а не «отправлено и всё»).

### Часть 5. Доказательство и Display API (~50 сек)

17. Открыть приложение TikTok или веб-профиль аккаунта и показать **реально
    опубликованный ролик** — тот самый, что был в превью.
18. Вернуться в Blast → **Статистика**: просмотры/лайки/комментарии, подтянутые через
    `video.list`, и вывод «какой параметр сработал». Проговорить: метрики только по
    своим роликам, только владельцу аккаунта, между пользователями не смешиваются.
19. Профиль → **«Отключить TikTok»**: показать, что связь разрывается и токены удаляются.

**Что обязательно попадает в кадр:** шаг 7 (экран прав), шаги 9–16 целиком (весь экран
публикации), шаг 17 (ролик в аккаунте), шаг 18 (Display API). Это четыре места, по которым
ревьюер решает, настоящая интеграция или нет.

**Чего в кадре быть не должно:** client secret, содержимое `.env`, консоль разработчика,
чужие аккаунты и посторонние вкладки.

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
[x] Боевой домен + HTTPS, приложение открывается          (проверено 2026-09-09)
[x] Футер лендинга: прямые ссылки Privacy Policy и Terms  (проверено 2026-09-09)
[x] §4 пункты 1–11 сделаны, видны на экране выкладки и покрыты тестами
[x] Решено: FILE_UPLOAD (и зафиксировано рантайм-контрактом)
[x] TIKTOK_REDIRECT_URI боевой: https://app.blast808.com/api/tiktok/callback
[ ] Ключи приложения в проде → /api/tiktok/status отдаёт configured:true   ← блокер
[ ] Иконка 1024×1024 PNG (экспорт из landing/assets/logo-star.svg на фоне #05010f)
[ ] Тексты §3 вставлены в поля кабинета
[ ] Sandbox: интеграция прогнана, ролик реально опубликован
[ ] Демо-видео по сценарию §5 записано
[ ] Подать → статус In Review (правки в этом состоянии недоступны)
```

Порядок: сначала ключи sandbox в прод-env → прогон флоу на боевом домене →
съёмка демо по §5 → заполнение полей §3 → подача.

---

## 8. После одобрения

- До аудита **все посты уходят в приватном режиме** — это нормально и ожидаемо.
  Публичными они станут после прохождения ревью.
- Обновления приложения делаются через **revision**, боевая версия при этом продолжает работать.
- Если отказали: в кабинете приходит причина; исправляем и подаём заново, счётчик попыток не ограничен.

---

## Контекст для агента, который откроет этот файл

Боевой код живёт в `Desktop/srv_blast/web_app` (репозиторий `srv_blast`), а НЕ в
`Desktop/blast_react_tailwind_fastapi_mock` — тот остался макетом и на прод не едет.

| Что | Где |
|---|---|
| Экран выкладки (весь UI требований Content Sharing Guidelines) | `web_app/frontend/src/pages/TikTokPostPage.tsx` |
| Клиент TikTok API + серверная валидация | `web_app/backend/app/tiktok_api.py` |
| Конфиг и контракт ключей | `web_app/backend/app/tiktok_config.py`, `runtime.py` |
| Хранение токенов (шифрование) | `web_app/backend/app/tiktok_token_store.py` |
| Тесты требований ревью | `tests/test_web_tiktok_posting.py` |
| Лендинг и юр-тексты (то, что увидит ревьюер) | `landing/`, `landing/js/legal-documents.js` |

Юр-документы лендинга рендерятся на клиенте из `legal-documents.js`: в браузере
ревьюера всё отображается, но «сырой» HTML страницы — пустая оболочка. Если ревью
придерётся к автоматической проверке — единственная правка здесь.
