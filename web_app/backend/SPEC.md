# Blast Web App — System Prompt для агента разработки

## Назначение

Ты разрабатываешь Blast Web App — полноценный SaaS-продукт на основе Telegram-бота @blast808bot. Сайт полностью заменяет бота в части генерации видеоконтента и управления проектами.

URL приложения: `blast808.com/app/*`
Существующий лендинг (blast808.com) не трогать — он уже задеплоен.

---

## 1. Стек технологий

Выбери оптимальный стек самостоятельно. Рекомендация:

- **Framework:** Next.js 14+ (App Router, TypeScript)
- **Стилизация:** CSS Custom Properties (токены ниже) + CSS Modules или Tailwind — на твоё усмотрение
- **БД:** PostgreSQL через Prisma ORM
- **Аутентификация:** NextAuth.js v5 (Credentials + Google OAuth)
- **Хранилище:** S3-совместимое (TwcStorage — уже используется для лендинга)
- **Оплата:** TBank API (уже интегрировано в бота, реиспользуем)
- **Waveform:** wavesurfer.js или peaks.js
- **Превью субтитров:** Remotion (серверный pre-render). Финальный рендер роликов — на существующей рендер-машине оркестратора, НЕ Remotion. Remotion отвечает ТОЛЬКО за превью
- **State management:** для многошагового визарда — выделенный стор (Zustand или React Context+reducer), не разрозненные useState. Для серверных данных и поллингов (jobs, projects, vibes, drops) — SWR или React Query с инвалидацией. Auth/подписка/кредиты — глобальный контекст на уровне App Shell
- **Деплой:** path-based: `blast808.com/app/...`

Если видишь, что другой стек решает задачу лучше — предложи и обоснуй перед тем, как начинать.

### 1.1 Перенос функционала с существующего бота (важно)

У Blast уже есть полностью работающий Telegram-бот @blast808bot на готовом оркестраторе. Сайт НЕ переписывает логику генерации с нуля — он переносит существующий функционал бота в веб-интерфейс.

Принцип работы агента:
- Перед реализацией каждого блока генерации — изучи соответствующий флоу и эндпоинты в коде бота/оркестратора (у тебя есть к ним доступ). Логика генерации, дроп-детекция, вайб-ранжирование, рендер, оплата TBank, доставка видео с S3 — всё это уже реализовано на стороне оркестратора. Переиспользуй, адаптируя интерфейс под веб
- Двигайся поэтапно: один блок визарда → найти его аналог в боте → подвязать к тому же оркестратору → проверить → следующий блок. Не пытайся подключить всё сразу
- Контракты запрос/ответ оркестратора бери из реального кода бота, а не выдумывай. Где интерфейс приходится адаптировать под веб (мультишаговость, превью в реальном времени) — сохраняй ту же доменную логику
- S3-доставка видео (CORS, signed/public URLs, CDN) — по той же схеме, что уже работает в боте

Это снимает необходимость проектировать оркестратор заново: он есть, задача — грамотно подвязать.

---

## 2. Дизайн-система

### 2.1 CSS Custom Properties (root)

```css
:root {
  /* Фоны */
  --bg:              #05010f;
  --nav-bg:          #150F25;
  --card-bg:         #060114;
  --card-dark-start: #0d0828;
  --card-dark-mid:   #0a0520;
  --card-dark-end:   #080318;

  /* Текст */
  --text:    #f6f5fd;
  --text-80: rgba(246, 245, 253, 0.80);
  --text-60: rgba(246, 245, 253, 0.60);
  --text-40: rgba(246, 245, 253, 0.40);
  --text-20: rgba(246, 245, 253, 0.20);

  /* Акцент */
  --accent:       #5f42b9;
  --accent-80:    rgba(95, 66, 185, 0.80);
  --accent-20:    rgba(95, 66, 185, 0.20);
  --accent-10:    rgba(95, 66, 185, 0.10);
  --accent-light: #8b6fe6;
  --pill-border:  #8b6fe6;
  --border:       rgba(95, 66, 185, 0.25);
  --border-hover: rgba(139, 111, 230, 0.50);

  /* Статусы (использовать ТОЛЬКО эти токены, не хардкодить hex) */
  --success:     #22c55e;
  --success-bg:  rgba(34, 197, 94, 0.15);
  --error:       #ef4444;
  --error-bg:    rgba(239, 68, 68, 0.15);
  --warning:     #f59e0b;
  --warning-bg:  rgba(245, 158, 11, 0.15);
  --info:        var(--accent-light);
  --info-bg:     var(--accent-20);

  /* Градиенты */
  --grad-main: linear-gradient(175deg, #8b6fe6 0%, #5f42b9 100%);
  --grad-btn:  linear-gradient(90deg,  #8b6fe6 0%, #5f42b9 100%);
  --grad-card: linear-gradient(135deg, #0d0828 0%, #0a0520 50%, #080318 100%);
  --grad-text: linear-gradient(90deg,  #8b6fe6 0%, #5f42b9 100%);

  /* Типографика */
  --font: 'Point', -apple-system, BlinkMacSystemFont, sans-serif;

  /* Spacing-шкала (использовать ТОЛЬКО эти значения для padding/margin/gap) */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;
  --space-8: 64px;

  /* Радиусы */
  --r40: 40px;   /* кнопки, прогресс-бары (pill) */
  --r20: 20px;   /* большие карточки, hero-контейнеры */
  --r15: 15px;   /* auth-панель */
  --r12: 12px;   /* инпуты, мелкие карточки, превью */
  --r9:  9px;    /* теги, пилюли */

  /* Переходы (использовать эти, не выдумывать свои длительности) */
  --t-fast:  0.15s ease;
  --t-base:  0.25s ease;
  --t-slow:  0.4s ease;

  /* Z-index слои (единая шкала, не хардкодить произвольные числа) */
  --z-base:    1;
  --z-sticky:  10;
  --z-sidebar: 100;
  --z-drawer:  200;
  --z-overlay: 900;
  --z-modal:   1000;
  --z-toast:   1100;
}
```

**Правило для агента:** все отступы — только из spacing-шкалы (`var(--space-N)`). Все длительности анимаций — только из `--t-*`. Все цвета статусов — только из токенов статусов. Никаких хардкодных hex в компонентах за пределами этого блока.

### 2.2 Шрифт Point

Файлы TTF — в `/public/fonts/` (предоставит клиент как zip-архив):

```css
@font-face { font-family: 'Point'; font-weight: 350; src: url('/fonts/Point-Book.ttf') format('truetype'); }
@font-face { font-family: 'Point'; font-weight: 400; src: url('/fonts/Point-Regular.ttf') format('truetype'); }
@font-face { font-family: 'Point'; font-weight: 500; font-style: italic; src: url('/fonts/Point-MediumItalic.ttf') format('truetype'); }
@font-face { font-family: 'Point'; font-weight: 700; src: url('/fonts/PointBold.ttf') format('truetype'); }
```

Базовые размеры:

| Контекст | Desktop | Mobile (≤768px) |
|---|---|---|
| Body | 18px | 14px |
| H1 | 56–64px | 32px |
| H2 | 36–40px | 24px |
| Eyebrow-лейблы | 11px | 11px |
| Кнопки | 18px | 14px |

### 2.3 Ключевые компоненты

**Primary Button:**
```css
height: 60px; padding: 0 36px; border-radius: var(--r40);
background: var(--grad-btn); color: var(--text); font-size: 18px;
border: none; cursor: pointer;
transition: opacity 0.2s, transform 0.15s;
/* hover: */ opacity: 0.88; transform: translateY(-1px);
/* disabled: */ opacity: 0.35; cursor: not-allowed;
```

**Secondary Button:**
```css
height: 60px; padding: 0 36px; border-radius: var(--r40);
background: transparent; border: 1px solid var(--pill-border);
color: var(--text-80); font-size: 18px;
/* hover: */ background: var(--accent-20);
```

**Card:**
```css
background: var(--grad-card);
border: 1px solid rgba(95, 66, 185, 0.25);
border-radius: var(--r20); padding: 24px;
```

**Eyebrow-лейбл:**
```css
font-size: 11px; font-weight: 500; text-transform: uppercase;
letter-spacing: 0.07em;
background: var(--grad-text);
-webkit-background-clip: text; -webkit-text-fill-color: transparent;
background-clip: text;
```

**Инпут / Textarea:**
```css
background: rgba(246, 245, 253, 0.05);
border: 1px solid rgba(95, 66, 185, 0.30);
border-radius: var(--r12); color: var(--text);
font-family: var(--font); font-size: 16px; padding: 14px 18px;
/* focus: */ border-color: var(--accent-light); outline: none;
/* placeholder: */ color: var(--text-40);
```

**Тег / Пилюля:**
```css
height: 37px; padding: 0 var(--space-4); border-radius: var(--r9);
border: 1px solid var(--pill-border); background: transparent;
color: var(--text-80); font-size: 14px; transition: all var(--t-fast);
/* selected: */ background: var(--accent-20); border-color: var(--accent-light); color: var(--text);
```

### 2.4 Состояния компонентов (применять ко ВСЕМ интерактивным элементам)

Каждый интерактивный элемент должен иметь все релевантные состояния. Это обязательно — не опускать ни одно.

**Focus (доступность, обязательно для всех):**
```css
:focus-visible { outline: 2px solid var(--accent-light); outline-offset: 2px; }
```

**Кнопки — все 6 состояний:**
| Состояние | Стиль |
|---|---|
| default | как описано в 2.3 |
| hover | `opacity: 0.88; transform: translateY(-1px)` |
| active | `transform: translateY(0) scale(0.98)` |
| focus-visible | outline-ring (выше) |
| disabled | `opacity: 0.35; cursor: not-allowed; pointer-events: none` |
| loading | контент заменяется на спиннер (`ti-loader-2` + `@keyframes spin`), ширина кнопки фиксируется, `cursor: wait` |

Loading обязателен для всех кнопок, запускающих сетевой запрос: «Зарегистрироваться», «Войти», «Оплатить и создать», «Запустить», «Подключить TikTok».

**Инпуты / Textarea — все состояния:**
```css
/* default */  border: 1px solid var(--border);
/* hover */    border-color: var(--border-hover);
/* focus */    border-color: var(--accent-light); outline: none;
               box-shadow: 0 0 0 3px var(--accent-10);
/* error */    border-color: var(--error);
/* disabled */ opacity: 0.4; cursor: not-allowed;
```
Error-сообщение под инпутом: `color: var(--error); font-size: 13px; margin-top: var(--space-1)`. Появляется по валидации (на blur или submit), не во время набора.

**Карточки выбора (вайбы, субтитры, тарифы, хуки) — состояния:**
```css
/* default */  border: 1px solid var(--border);
/* hover */    border-color: var(--border-hover); transform: translateY(-2px);
/* selected */ border: 2px solid var(--accent-light); + ti-check в углу;
/* disabled */ opacity: 0.4; cursor: not-allowed;
```

**Status badge — единый компонент для всех статусов в приложении:**
```css
display: inline-flex; align-items: center; gap: var(--space-1);
height: 24px; padding: 0 var(--space-3); border-radius: var(--r9);
font-size: 13px;
```
Цветовые варианты (использовать токены, см. 2.1):
| Вариант | bg | text | Где |
|---|---|---|---|
| success | `--success-bg` | `--success` | Активный, Готово, Опубликовано |
| info | `--info-bg` | `--info` | В процессе, В очереди, Генерируется |
| neutral | `rgba(246,245,253,.08)` | `--text-40` | Завершён |
| error | `--error-bg` | `--error` | Ошибка |

**Empty states — единый паттерн:**
Иконка 48px (`var(--text-40)`) → заголовок (H3) → одна строка описания (`var(--text-60)`) → primary CTA. Всё по центру, вертикальный стек, `gap: var(--space-4)`.

**Toast / уведомления:**
Появляются справа сверху, `z-index: var(--z-toast)`, авто-скрытие через 4с. Success/error варианты по токенам. Использовать для: «Трек загружен», «Проект создан», «Скопировано», ошибок сети.

**Skeleton / Shimmer (для всех загрузок данных):**
```css
@keyframes shimmer { 0% { background-position: -200% 0 } 100% { background-position: 200% 0 } }
background: linear-gradient(90deg, var(--card-bg) 25%, var(--accent-10) 50%, var(--card-bg) 75%);
background-size: 200% 100%; animation: shimmer 1.5s infinite;
```
Применять к: загрузке проектов на Dashboard, превью в Preview Panel, thumbnail'ам видео, вайб-карточкам пока грузится ранкер.

---

## 3. Архитектура маршрутов

```
blast808.com/           → существующий лендинг (НЕ ТРОГАТЬ)
blast808.com/register   → Регистрация (публичный)
blast808.com/login      → Вход (публичный)

blast808.com/app/                → Dashboard
blast808.com/app/generate        → Визард генерации
blast808.com/app/projects        → Список проектов
blast808.com/app/projects/[id]   → Детали проекта
blast808.com/app/profile         → Личный кабинет
blast808.com/app/pricing         → Тарифы
blast808.com/app/stats           → Статистика (заглушка)
blast808.com/app/processing/[id] → Экран генерации в реальном времени
blast808.com/not-found           → 404
blast808.com/error               → Ошибка сервера
```

Middleware: все `/app/*` защищены. Неавторизованный пользователь → редирект на `/login`.

---

## 4. База данных (Prisma Schema)

```prisma
model User {
  id           String   @id @default(cuid())
  email        String   @unique
  name         String
  surname      String
  artistNick   String?
  avatarUrl    String?
  passwordHash String?
  googleId     String?  @unique
  tgUserId     String?
  tgVerified   Boolean  @default(false)
  createdAt    DateTime @default(now())

  projects           Project[]
  subscription       Subscription?
  tiktokAccount      TiktokAccount?
  generationSessions GenerationSession[]
  savedTracks        SavedTrack[]
}

model TgVerification {
  id        String   @id @default(cuid())
  userId    String   @unique
  tgUserId  String?
  verified  Boolean  @default(false)
  createdAt DateTime @default(now())
}

model Project {
  id          String        @id @default(cuid())
  userId      String
  name        String
  coverUrl    String?
  packageType PackageType
  status      ProjectStatus @default(IN_PROGRESS)
  startedAt   DateTime      @default(now())
  endsAt      DateTime?
  user        User          @relation(fields: [userId], references: [id])
  jobs        GenerationJob[]
}

enum PackageType   { TRIAL BLAST GLOW IMPULSE }
enum ProjectStatus { ACTIVE IN_PROGRESS COMPLETED }

model GenerationJob {
  id                String    @id @default(cuid())
  projectId         String
  userId            String
  orchestratorJobId String?
  stageData         Json
  status            JobStatus @default(PENDING)
  versions          Int       @default(1)
  rating            Int?
  outputUrls        String[]
  createdAt         DateTime  @default(now())
  completedAt       DateTime?
  project           Project   @relation(fields: [projectId], references: [id])
}

enum JobStatus { PENDING PROCESSING COMPLETED FAILED }

model Subscription {
  id                  String      @id @default(cuid())
  userId              String      @unique
  tier                PackageType
  creditsTotal        Int
  creditsUsed         Int         @default(0)
  renewsAt            DateTime?
  tbankSubscriptionId String?
  isActive            Boolean     @default(true)
  startedAt           DateTime    @default(now())
  user                User        @relation(fields: [userId], references: [id])
}

model TiktokAccount {
  userId       String    @id
  handle       String?
  accessToken  String
  refreshToken String?
  expiresAt    DateTime?
  user         User      @relation(fields: [userId], references: [id])
}

model GenerationSession {
  id        String   @id @default(cuid())
  userId    String
  projectId String?
  data      Json
  updatedAt DateTime @updatedAt
  user      User     @relation(fields: [userId], references: [id])
}

model SavedTrack {
  id        String   @id @default(cuid())
  userId    String
  s3Key     String
  filename  String
  durationS Float
  createdAt DateTime @default(now())
  expiresAt DateTime
  user      User     @relation(fields: [userId], references: [id])
}
```

---

## 5. Аутентификация

### 5.1 Регистрация (`/register`)

**Лейаут: 2 колонки, fullscreen (без сайдбара)**

Левая колонка (40%):
- Контейнер: `background: #140E24; border-radius: 15px; height: 100%; overflow: hidden; position: relative`
- Логотип Glow (SVG) — увеличенный, полупрозрачный, по центру/фону
- Поверх: placeholder-фото артиста (`object-fit: cover; position: absolute; bottom: 0; width: 100%`)
- Сейчас статичное фото; в будущем — карусель

Правая колонка (60%, flex column, justify: center, padding: 0 10%):
- H1: «Регистрация» (56px)
- Subtitle: «Готов продвинуть новый трек с помощью видео-контента?» (var(--text-60), 18px)
- Gap 20px, затем 3 инпута (100% ширины):
  - Имя, Фамилия, Email
- Primary кнопка 100% ширины: «Зарегистрироваться»
- Разделитель: «или» (text-center, var(--text-40))
- Две кнопки в ряд (по ~50% с gap 12px):
  - Secondary: «Войти через Telegram» (иконка TG SVG слева)
  - Secondary: «Войти через Google» (иконка Google SVG слева)
- Мелкий текст 14px: «Уже есть аккаунт? → Войти» (ссылка на /login)

**После успешной регистрации:**
1. Создать запись `TgVerification { userId, verified: false }`
2. Открыть модальное окно TG-верификации (не закрывать до подтверждения)

Email-верификация НЕ используется — ключевая механика подтверждения живёт в боте. Вместо письма бот при первом `/start` шлёт welcome-сообщение (это естественно уводит пользователя в Telegram, где и происходит верификация).

**TG-верификация popup (modal с backdrop):**
```
┌───────────────────────────────────────┐
│         [Логотип Blast  40px]         │
│                                       │
│   Подпишись на наш канал и запусти   │
│   бота — получишь первые бесплатные   │
│   генерации                           │
│                                       │
│   [Primary: Открыть @blast808bot →]   │
│                                       │
│   После запуска ты автоматически      │
│   вернёшься сюда                      │
│                                       │
│   [Secondary: Уже запустил — проверить●●●]│
└───────────────────────────────────────┘
```

- «Открыть @blast808bot»: deep link `https://t.me/blast808bot?start=verify_{userId}`
- «Уже запустил — проверить»: polling `GET /api/auth/tg-verify?userId={id}` каждые 3 секунды

**Механизм верификации (сторона бота):**
Когда бот получает `/start verify_{userId}`:
1. Шлёт welcome-сообщение
2. Проверяет подписку пользователя на TG-канал
3. Обновляет `TgVerification`: `{ tgUserId, verified: true }`
4. Отправляет пользователю кнопку «Вернуться в приложение» → `blast808.com/app?verified=true`

**Сторона сайта (`/api/auth/tg-verify`):**
- Возвращает `{ verified: boolean }`
- При `verified: true`: начислить стартовые кредиты, закрыть модал, редирект `/app`
- Polling таймаут: 5 минут, после — показать инструкцию «Попробуй обновить страницу»

**Blocked-стейт: зарегистрирован, но не верифицирован в TG.**
Если пользователь закрыл попап и зашёл снова (`tgVerified: false`), он попадает на дашборд, но НЕ может генерировать. Любая попытка «Создать проект» / запустить генерацию → редирект на блокирующий экран:
```
[Логотип Blast]
Подтверди аккаунт в Telegram
Запусти бота и подпишись на канал, чтобы получить
первые бесплатные генерации
[Открыть @blast808bot →]
```
То же поведение при нулевом балансе у верифицированного пользователя (кредиты кончились) — но редирект ведёт на `/app/pricing`, а не на верификацию. Логика: нет верификации → бот; есть верификация, но нет кредитов → тарифы.

### 5.2 Вход (`/login`)

**Лейаут:** идентичен регистрации (те же 2 колонки, та же левая панель)

Правая колонка:
- H1: «Добро пожаловать»
- Subtitle: «Войди в свой аккаунт»
- Инпут: Email
- Инпут: Пароль (с кнопкой `ti-eye`/`ti-eye-off` для показа)
- Primary кнопка 100%: «Войти»
- Разделитель: «или»
- Secondary кнопка 100%: «Войти через Google»
- Мелкий текст: «Нет аккаунта? → Зарегистрироваться»
- Сброс пароля: **не реализуем в v1**

---

## 6. App Shell

Все `/app/*` используют единый layout:

```
┌──────────┬──────────────────────────────────────────────────┐
│ Sidebar  │                 Main Content                      │
│  80px    │            (margin-left: 80px)                   │
│ fixed    │                                                   │
│          │                                                   │
│ [Logo]   │                                                   │
│          │                                                   │
│ [▶  ]   │                                                   │
│ [📁 ]   │                                                   │
│ [📊 ]   │                                                   │
│          │                                                   │
│ flex: 1  │                                                   │
│          │                                                   │
│ [Avatar] │                                                   │
└──────────┴──────────────────────────────────────────────────┘
```

**App-frame (общая рамка приложения) — фундамент вертикали:**

Сайдбар и контент-зона — соседние блоки внутри одного flex-контейнера высотой `100dvh`. Это гарантирует, что верхний и нижний края контейнеров автоматически совпадают с краями сайдбара (без ручных вычислений).

```css
.app-frame   { display: flex; height: 100dvh; }
.app-content {
  flex: 1; height: 100dvh; overflow: hidden;
  padding: var(--space-6);          /* 32px — ВЕРТИКАЛЬ совпадает с сайдбаром */
  max-width: 1440px; margin-inline: auto;   /* кэп + центрирование на больших экранах */
  display: flex; flex-direction: column; gap: var(--space-5);  /* 24px между секциями */
}
```

**Ключевой принцип выравнивания:** вертикальный паддинг сайдбара (`var(--space-6)`, сверху и снизу) ОБЯЗАН совпадать с вертикальным паддингом `.app-content`. Тогда лого вверху сайдбара встаёт на одну линию с заголовком hero, а аватарка внизу — на одну линию с нижним краем контейнеров. Горизонтальные паддинги при этом разные (сайдбар центрирует иконки, контент держит поле 32px) — это нормально, совпадать должна только вертикаль.

**Фолбэк для коротких экранов:** при высоте вьюпорта < 720px `.app-content` переключается на `height: auto; min-height: 100dvh; overflow-y: auto` — страница начинает скроллиться целиком вместо схлопывания контейнеров.

**Сайдбар:**
```css
width: 80px; height: 100dvh; position: fixed; left: 0; top: 0;
background: var(--nav-bg); /* #150F25 */
border-right: 1px solid rgba(95, 66, 185, 0.20);
display: flex; flex-direction: column; align-items: center;
padding: var(--space-6) 0;   /* 32px по вертикали — синхронно с .app-content; 0 по горизонтали (иконки центрируются) */
z-index: var(--z-sidebar);
```
Контент-зона при фиксированном сайдбаре получает `margin-left: 80px`.

Элементы сверху вниз:
- Логотип Blast SVG (`/assets/logo-main.svg`, 32px) → `/app`
- Gap `var(--space-6)` (32px)
- `/assets/nav-generate.svg` 24px → `/app/generate` (иконка плей)
- `/assets/nav-projects.svg` 24px → `/app/projects` (иконка лампочки)
- `/assets/nav-stats.svg` 24px, `opacity: 0.35` → `/app/stats` (иконка чарта, locked)
- `flex: 1` (spacer — расталкивает аватарку к низу)
- Аватар-круг 48px (фото пользователя или инициалы на `var(--accent-20)`) → `/app/profile`

Активный элемент: `color: var(--accent-light)` + `box-shadow: 0 0 12px var(--accent-20)`.

**Индикатор фоновой генерации:** пока есть активный `GenerationJob` со статусом PROCESSING, на иконке «Генерация» в сайдбаре показывается бейдж — пульсирующая точка `var(--accent-light)` в правом верхнем углу иконки. Клик по иконке → `/app/processing/{активный jobId}`. Когда все ролики джоба готовы → бейдж снимается + глобальный toast «Ролики готовы» (success, из 2.4) с кнопкой «Открыть». Это работает на любой странице — пользователь узнаёт о завершении, даже уйдя с экрана генерации. Источник данных: лёгкий polling `GET /api/jobs/active` на уровне App Shell (каждые 5с, только если есть активный джоб).

Использование иконок: `<img src="/assets/nav-generate.svg" width="24" height="24" aria-hidden="true" />` или инлайн SVG для управления цветом через `currentColor` (предпочтительно — тогда активное состояние работает через `color: var(--accent-light)` без фильтров).

**Мобайл (≤768px):**
- Сайдбар скрыт
- Шапка: логотип Blast слева + `ti-menu-2` справа
- Клик бургера → drawer `slideInLeft` (300ms) + overlay backdrop
- В drawer: те же навигационные элементы, но с текстовыми лейблами

---

## 7. Страницы

### 7.1 Dashboard (`/app`)

Дашборд заполняет всю высоту `.app-content` (flex-колонка с gap 24px). Высоты НЕ хардкодить — секции распределяются через flex, чтобы верх hero совпадал с верхом сайдбара, а низ нижнего ряда — с низом сайдбара.

**Hero-контейнер (полная ширина, `flex: 0 0 clamp(220px, 30%, 320px)`):**
```
┌────────────────────────────────────────────────────┐
│  Привет, это Бласт!                                │
│  Готов создать контент для продвижения             │
│  нового трека?                                     │
│                                                    │
│      [♪  Создать проект →]                         │
└────────────────────────────────────────────────────┘
```
- `clamp(220px, 30%, 320px)`: фикс-высота, которая не растягивается на больших экранах (упирается в 320) и не схлопывается на маленьких (минимум 220)
- Стиль: `.card`, большой H1 + subtitle (var(--text-60)) + primary кнопка, контент центрируется по вертикали внутри
- SVG-нота: встроена слева от текста кнопки

**Empty state (0 проектов):**
- Hero сохраняется полностью
- Под ним (в зоне нижнего ряда): единый empty-паттерн из 2.4 — `ti-folder` 48px + «Создай первый проект» + кнопка «Создать проект», по центру
- Нижние два контейнера не показываем

**Нижняя секция (`flex: 1` — забирает весь остаток высоты):**

Два контейнера в ряд: `display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-5); height: 100%`. Оба контейнера `height: 100%` → строго равны по высоте на любом экране. Внутренний контент, который может переполниться (список проектов), скроллит внутри контейнера (`overflow-y: auto`), сам фрейм остаётся прибит к вьюпорту.

**Левый: «Все проекты»**

Заголовок (flex, space-between): «Все проекты» (кликабелен → `/app/projects`) + иконка `ti-chevron-right` 20px (`var(--text-60)`, на hover заголовка → `var(--accent-light)`). Не строить кастомный шеврон из линий — использовать готовую иконку для чистоты.

Список (показываем 2 последних проекта):
```
[обложка 40×40] Название трека    14 200
                Активный           ╌╌╌╌╌ (sparkline 60×24px)
────────────────────────────────────────
[обложка 40×40] Название трека     8 500
                В процессе          ╌╌╌ (sparkline)
```
- Цифра просмотров: 28px, right-align, данные из TikTok API (если не подключён — «—»)
- Sparkline: SVG `<polyline>`, без осей, var(--accent-light)
- Статус-badge: использовать единый компонент из 2.4 (Активный → success, В процессе → info, Завершён → neutral)

**Правый: «Статистика видео»**

Заголовок (flex, space-between): «Статистика видео» (кликабелен → `/app/stats`) + иконка `ti-chevron-right` 20px (та же логика hover, что у блока «Все проекты»).

Левая часть:
- Eyebrow «ОХВАТ»
- Большое число месячного охвата (28px)
- Percentage badge: `↑ +12%` зелёный / `↓ -5%` красный + «за месяц»

Правая часть — сетка 2×2 из последних сгенерированных видео:
- Если TikTok не подключён: поверх сетки плашка «Подключи TikTok для реальной статистики» + кнопка → `/app/profile`
- Каждая ячейка: `<video autoplay muted loop playsinline>` или thumbnail из S3
- Размер ячеек: ~120×90px, `border-radius: var(--r12)`

---

### 7.2 Список проектов (`/app/projects`)

**Секция 1 — «Актуальный проект» (верхний контейнер):**
```
[Название проекта]          Текущий проект
────────────────────────────────────────────────
[📹 12 видео]     [██████████░░░░  68%]
                   10 из 15 сгенерировано
```
- Если нет ACTIVE-проекта: «Нет активного проекта» + кнопка «Создать»

**Секция 2 — «Все проекты» (нижний контейнер):**

Горизонтальный ряд карточек + кнопка «+» в конце:

Карточка проекта:
```
┌────────────────────────┐
│  [Обложка 200×120px]   │
│                        │
│  [BLAST]               │  ← badge пакета
│  Название трека        │
│  01.06 — 30.06.2025    │
│  [Автопостинг →]       │  ← secondary, placeholder
└────────────────────────┘
```
Badge-цвета: Trial → серый, Blast → accent, Glow → фиолетовый, Impulse → amber/gold

Кнопка «+» — круг 56px, `border: 2px solid var(--pill-border)` → открывает modal «Создать проект»

**Modal «Создать проект» — два сценария**

Сначала проверяем: есть ли у пользователя активная подписка с доступными кредитами (`Subscription.isActive && creditsUsed < creditsTotal`).

---

**Сценарий A — есть активная подписка (быстрый путь):**

Выбор тарифа НЕ показываем — он уже оплачен. Один компактный экран:
```
Новый проект                                          [×]
──────────────────────────────────────────────────────
Название проекта      [________________________]

Обложка               ( ) Авто (waveform трека)   ← default
                      ( ) Загрузить своё  [Выбрать файл]

Тариф: BLAST · осталось 5 из 15 генераций в этом месяце

                      [Отмена]    [Создать проект →]
```
- Без оплаты и без оферты (подписка уже активна)
- «Создать проект» → `POST /api/projects` → редирект `/app/projects/[id]`

---

**Сценарий B — нет активной подписки (2 шага):**

*Шаг 1 — данные проекта:*
```
Новый проект                                    Шаг 1 из 2
──────────────────────────────────────────────────────
Название проекта      [________________________]

Обложка               ( ) Авто (waveform трека)
                      ( ) Загрузить своё  [Выбрать файл]

                      [Отмена]         [Далее →]
```
«Далее» активна когда введено название.

*Шаг 2 — тариф и оплата:*
```
Выбери пакет                                    Шаг 2 из 2
──────────────────────────────────────────────────────
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│  Trial   │  │  Blast   │  │   Glow   │  │ Impulse  │
│  990 ₽   │  │1990₽/мес │  │ 7990 ₽   │  │ 29990 ₽  │
│  5 видео │  │ 15/мес   │  │ 30 видео │  │ 50 видео │
└──────────┘  └─[★ популярный]─┘ ─────────  ──────────
   (выбор одной карточки — selected-состояние из 2.4)

☐ Принимаю оферту и даю согласие на обработку данных

                      [← Назад]    [Оплатить и создать →]
```
- Карточка тарифа = selected-состояние из 2.4. Blast выделен бордером + badge «популярный»
- Полные условия каждого тарифа — ссылка «Подробнее → /app/pricing» (не дублировать всю таблицу в модале)
- Чекбокс оферты обязателен — блокирует кнопку (требование TBank)
- «Оплатить и создать» в loading-состоянии при сабмите

Flow оплаты: `POST /api/payments/create-order { name, coverChoice, packageType }` → TBank ссылка → редирект → webhook → создание Project → редирект `/app/projects/[id]`

**Modal-механика (общая):** overlay `z-index: var(--z-overlay)`, сам модал `var(--z-modal)`, закрытие по `×`, клику на overlay и Esc. Анимация появления: fade overlay + `scale(0.96) → scale(1)` модала за `var(--t-base)`.

---

### 7.3 Детали проекта (`/app/projects/[id]`)

Один большой контейнер (полная ширина main content).

**Хедер:** H2 с именем проекта + статус-badge справа.

**Sub-блок 1 — Прогресс (полная ширина, ~90px высота):**
```
Прогресс                                      68%
████████████████████████████░░░░░░░░░░░░
                              10 из 15 сгенерировано
```
Progress bar: `background: var(--grad-btn)`, border-radius 40px, animated width transition.

**Три колонки ниже (равной ширины):**

**Колонка 1: Контент-план**
```
Контент-план
──────────────────────────────────────────────
[48×48] Версия 1              [⬇ Скачать] [▶ TikTok]
        Impulse · Хук: Свайп
        30.06.2025  ●  Готово

[48×48] Версия 2              [⬇ Скачать] [▶ TikTok]
        Tape · Без хука
        29.06.2025  ●  Готово
```
- Thumbnail: S3 URL, 48×48px, rounded
- «Скачать»: прямая ссылка на S3
- «В TikTok»: `POST /api/tiktok/post`
  - Если TikTok не подключён → modal «Подключи TikTok в профиле»
  - Статус публикации: «Опубликовано» (зелёный) / «В очереди» (accent) / «Ошибка» (красный + Повторить)

**Колонка 2: Статистика**
- Если TikTok подключён: мини-чарт охвата (SVG) + лучшее видео + ER
- Если не подключён: `ti-chart-bar` 40px muted + «Подключи TikTok для статистики» + кнопка → `/app/profile`

**Колонка 3:** не реализуем («Личные идеи» — убрать). Если неудобно с 2-колоночной сеткой, сделай колонки 2:1.

---

### 7.4 Профиль / ЛК (`/app/profile`)

**Блок 1 — Информация:**
```
┌─────────────────────────────────────────────────────────────┐
│ [Фото 80×80]  Имя Фамилия           [Подключить TikTok →]  │
│               @псевдоним_артиста                            │
└─────────────────────────────────────────────────────────────┘
```
- Фото: кликабелен → file picker → загрузка на S3 → обновление `avatarUrl`
- Имя/ник: click-to-edit (input появляется inline, blur/Enter → сохранение)
- Кнопка TikTok: всегда видна справа
  - Не подключён: primary «Подключить TikTok» → TikTok OAuth
  - Подключён: зелёная плашка «TikTok: @handle» + мелкий «Отключить» (link) → `DELETE /api/tiktok/disconnect`

**Блок 2 — Подписка:**

*Случай A: Trial / нет подписки:*
```
┌────────────────────────────────────────────────────────┐
│ Trial                                                  │
│                                                        │
│ Использовано генераций                                 │
│ ████████░░  4 из 5                                     │
│                                                        │
│                    [Посмотреть тарифы →]               │
└────────────────────────────────────────────────────────┘
```

*Случай B: Активная подписка Blast/Glow/Impulse:*
```
┌────────────────────────────────────────────────────────┐
│ BLAST  ← gradient badge                               │
│                                                        │
│ Использовано: ████████████░░  10 из 15                 │
│ Следующее списание: 01.07.2025 · 1990 ₽               │
│                                                        │
│  Таймлайн подписки:                                    │
│                                                        │
│  ●─────────●─────────●─────────●                       │
│  Месяц 1   Месяц 2   Месяц 3   Месяц 4                │
│  Активен   Удвоение  Блогер    Дистрибуция             │
│            роликов   поддержки в сеть                  │
│            [Активир] [Активир] [Активир]               │
│                                                        │
│                         [Отменить подписку]            │
└────────────────────────────────────────────────────────┘
```
- Таймлайн: горизонтальный flex, 4 точки с коннекторами (1px линия)
- Достигнутая точка: `background: var(--success)` + `ti-check` внутри
- Активируемая: accent кнопка «Активировать»
- Будущая: серая точка, кнопка disabled

---

### 7.5 Тарифы (`/app/pricing`)

4 карточки в ряд (на мобайле 1 колонка со скроллом).

| | Trial | Blast | Glow | Impulse |
|---|---|---|---|---|
| Цена | 990 ₽ | 1990 ₽/мес | 7990 ₽ | 29990 ₽ |
| Тип | разовый | подписка | разовый | разовый |
| Видео | 5 | 15/мес | 30 | 50 |
| Хук-форматы | базовые | все F1–F5 | все F1–F5 | все F1–F5 |
| Блогеры | — | — | +2 закупки | 10–12 посевов |
| Стратегия | — | — | — | + стратегия релиза |

- Blast: выделен `border: 2px solid var(--accent-light)` + badge «Популярный» над карточкой
- У каждой карточки: название, цена (gradient text, 40px), список фич (с `ti-check`), CTA кнопка

**CTA зависит от статуса пользователя:**
- Текущий активный тариф → кнопка disabled с текстом «Текущий тариф» + badge на карточке
- Тариф выше текущего → «Апгрейд до {название}»
- Тариф ниже / разовый при наличии подписки → «Перейти» (или скрыть, на усмотрение продукта)
- Нет активной подписки → «Выбрать» / «Оплатить»

Перед кнопками оплаты обязательный чекбокс:
```
☐ Принимаю оферту и соглашаюсь с условиями обработки персональных данных
```
Не отмечен → кнопки оплаты disabled.

При клике: `POST /api/payments/create-order { packageType, projectId? }` → редирект на TBank.

---

### 7.6 Статистика (`/app/stats`) — Заглушка

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│                  [ti-chart-bar 64px, opacity 0.25]       │
│                                                          │
│            Система эволюции контента                     │
│                                                          │
│   Алгоритм анализирует retention-графики твоих           │
│   видео и предлагает, что изменить в следующей           │
│   итерации — чтобы дольше удерживать аудиторию.         │
│   Подключи TikTok, и мы начнём собирать данные           │
│   уже сейчас.                                            │
│                                                          │
│              [Подключить TikTok →]                       │
│                                                          │
│   ┌──────────────────────────────────────────────┐       │
│   │  [Placeholder-иллюстрация будущего UI]       │       │
│   │  (gradient blur / wireframe SVG)             │       │
│   └──────────────────────────────────────────────┘       │
│                                                          │
└──────────────────────────────────────────────────────────┘
```
- Кнопка TikTok → OAuth flow → сохранение токена в `TiktokAccount` (аналитику не показываем)

---

### 7.7 404 и Error страницы

**404 (`/not-found`):**
```
[Логотип Blast, 48px]
404 — Страница не найдена
[← Вернуться на главную]
```

**Error (`/error`):**
```
[Логотип Blast, 48px]
Что-то пошло не так
Попробуй обновить страницу или вернись позже
[Обновить ↺]    [← На главную]
```
Оба: `background: var(--bg)`, контент по центру, минимум декора.

---

## 8. Визард генерации (`/app/generate`)

### 8.0 Общая структура

Визард — единая страница с внутренним state (не отдельные URL для каждого этапа).

**3-панельный лейаут:**
```
┌──────────────────────┬───────────────────────────────────────────────┐
│                      │  [Stepper Bar]      ← тонкая полоса, 44px      │
│                      ├───────────────────────────────────────────────┤
│  Preview Panel       │  [Track + Next Bar] ← инфо трека + кнопка, 72px│
│  (~30vw, sticky,     ├───────────────────────────────────────────────┤
│  min 320px,          │         Settings Panel                        │
│  max 500px,          │         (меняется на каждом этапе)            │
│  full height)        │         (overflow-y: auto)                    │
│                      │                                               │
└──────────────────────┴───────────────────────────────────────────────┘
```
Разделение верхней зоны на две полосы (вместо одной тесной 100px) решает нехватку места на узких десктопах.

**Геометрия визарда (full-bleed исключение):** визард НЕ использует стандартное поле страницы `.app-content` — preview-панель идёт от края до края по высоте. Корневой контейнер визарда: `display: flex; height: 100dvh; padding: var(--space-5)` (24px рамка). Внутри — Preview Panel (слева) + правый столбец (Stepper + Track + Settings).

**Правый столбец — ИДЕНТИЧЕН на всех 5 этапах.** Меняется только контент внутри Settings Panel. Высоты фиксированы:
```css
.wizard-right { flex: 1; display: flex; flex-direction: column; gap: var(--space-5); height: 100%; }
/* Stepper Bar:  flex: 0 0 44px  */
/* Track Bar:    flex: 0 0 72px  */
/* Settings:     flex: 1 (забирает остаток, скроллит внутри) */
```
Поскольку Stepper (44) и Track (72) фиксированы, а Settings = `flex: 1`, высота Settings Panel одинакова на всех этапах при данном вьюпорте. Контент этапа, который выше панели, скроллит внутри Settings (`overflow-y: auto`), не меняя саму рамку.

**Масштабируемость по высоте** (правый столбец, 24px рамка сверху+снизу = 48):
| Вьюпорт | inner (−48) | Stepper | Track | gaps (2×24) | Settings |
|---|---|---|---|---|---|
| 900px | 852 | 44 | 72 | 48 | **688** |
| 1080px | 1032 | 44 | 72 | 48 | **868** |
| 1440px | 1392 | 44 | 72 | 48 | **1228** |

Settings растёт вместе с экраном, Stepper и Track держат фикс-высоту. На больших экранах контент Settings прижат к верху (`justify-content: flex-start`), не растягивается в пустоту.

**Preview Panel (левая, sticky):**
```css
width: clamp(320px, 30vw, 500px); height: 100%;
background: var(--card-bg);
border-right: 1px solid var(--border);
```
Высота `100%` от рамки визарда → preview по высоте совпадает с правым столбцом, края ровные.

**Поведение Preview Panel при смене контента (важно для премиальности):**
- Видео: `<video>` в контейнере с `aspect-ratio: 9/16`, по центру, `border-radius: var(--r12)`
- При смене источника (вайб / стиль / эффект): **crossfade**, не резкая подмена. Два слоя `<video>`: новый грузится под `opacity: 0`, по `canplay` → старый в `opacity: 0`, новый в `opacity: 1` за `var(--t-base)`
- Во время загрузки нового клипа: shimmer-оверлей поверх текущего кадра (не чёрный экран), снимается по `canplay`
- Если клип не загрузился: оставить предыдущий кадр + мелкий `ti-alert-circle` в углу, не ронять панель

**Stepper Bar (тонкая полоса сверху, 44px):**
```
┌──────────────────────────────────────────────────────────────┐
│   ①──────②──────③──────④──────⑤                              │
│  Трек    Фон    Хук   Титры  Финал                            │
└──────────────────────────────────────────────────────────────┘
```
- Пройденная точка (18px): `background: var(--accent)`, `ti-check` внутри
- Текущая точка (20px): `background: var(--accent)`, `box-shadow: 0 0 12px var(--accent-20)`
- Будущая точка (16px): `border: 1.5px solid var(--text-40)`, прозрачная
- Коннекторы: 1px `var(--text-20)`; участки до пройденных точек — `var(--accent)`
- Лейблы 11px под точками
- Клик на пройденную точку → возврат к этапу (данные сохраняются)
- **Адаптив:** при ширине Settings-зоны < 600px лейблы скрываются у всех точек кроме текущей

**Track + Next Bar (вторая полоса, 72px):**
```
┌──────────────────────────────────────────────────────────────┐
│ [обложка 44×44]  Название трека                    [Далее →] │
│                  MP3 · 3:24                                   │
└──────────────────────────────────────────────────────────────┘
```
- Левый блок: thumbnail (44×44, `var(--r12)`) + название (16px) + формат/длина (`var(--text-60)`, 13px)
- Правый блок: кнопка «Далее →»
  - Disabled пока не пройдена валидация (состояние из 2.4)
  - На Этапе 5: текст «Запустить →», primary с градиентом
  - При сабмите: loading-состояние

**Settings Panel (нижний правый):**
```css
flex: 1; overflow-y: auto; padding: var(--space-5) var(--space-6); background: var(--bg);
```

**Навигация между этапами:**
- Вперёд: кнопка «Далее →» (после валидации)
- Назад: клик на пройденную точку Stepper Bar
- Все данные всех этапов сохраняются в state при навигации в любую сторону
- Смена контента Settings Panel при переходе: лёгкий fade (`var(--t-fast)`)

**Сохранение сессии:**
- При каждом переходе: `POST /api/wizard/session { projectId, stage, data }`
- При загрузке `/app/generate`: `GET /api/wizard/session` → восстановить стейт если сессия незавершена
- Стейт также сохраняется в `localStorage` как fallback

**Привязка к проекту:**
- Если пришли из «Создать проект» с `?project={id}` → projectId установлен
- Если пришли из сайдбара без контекста → ДО Этапа 1 показать селектор проекта:
  ```
  К какому проекту?
  [Карточки активных проектов]
  [+ Создать новый проект]
  ```

**Фоновые задачи, запускаемые сразу при завершении Этапа 1:**
- `POST /api/wizard/analyze-track { s3Key }` → BPM + дроп-детекция (нужно в Этапе 3)
- `POST /api/wizard/rank-vibes { lyrics }` → LLM вайб-ранжирование (нужно в Этапе 2)

---

### 8.1 Этап 1 — «Трек»

**Settings Panel:**

**[A] Секция «ЗАГРУЗИ ТРЕК»** (eyebrow, required)

Drop zone:
```
┌───────────────────────────────────────────────┐  dashed border: 1.5px dashed var(--pill-border)
│         [ti-upload 20px, accent]              │
│  Перетащи файл или выбери с устройства        │
│       MP3, M4A, WAV · до 200 МБ              │
└───────────────────────────────────────────────┘
```
Hover: `background: rgba(95,66,185,0.08)`

После загрузки: `[ti-check зелёный] filename.mp3 · 3:24` — клик → замена файла

Под зоной: `↻ Использовать предыдущий трек` — ссылка-лейбл; показывать только если в `SavedTrack` есть актуальная запись (`expiresAt > now`)

Upload flow: `POST /api/wizard/upload-track` (multipart) → конвертация в MP3 → S3 с TTL → `SavedTrack`

---

**[B] Секция «ТЕКСТ ПЕСНИ»** (eyebrow, с разделителем `border-top`, required)

Строка заголовка (flex, space-between):
- Слева: eyebrow
- Справа: `<input type="checkbox" id="frag">` + лейбл «Указать конкретный отрывок»

Textarea: 100% ширины, 6 строк, placeholder «Вставь полный текст трека»

Если чекбокс включён — второй textarea ниже (плавное появление):
placeholder «Скопируй строки, которые должны войти в ролик (например, припев)»

---

**[C] Секция «ТАЙМИНГ»** (eyebrow, с разделителем, optional)

Segmented control (2 кнопки):
- «На усмотрение ИИ» — default, active
- «Указать вручную»

Active кнопка: `background: var(--accent-20); border-color: var(--accent-light)`

Если «Вручную» выбрано — строка инпутов:
```
С [ 1:24 ]   По [ 1:46 ]    (5–22 секунды)
```
Валидация диапазона: вне 5–22с → красная рамка + error-текст

---

**Валидация «Далее»:** трек загружен + текст непустой.

**Preview Panel — Этап 1:**
- Пустое состояние: `ti-music 32px` (var(--text-40)) по центру + «Загрузи трек, чтобы увидеть превью»
- После загрузки: wavesurfer.js waveform, play/pause кнопка снизу по центру, длительность
- Если тайминг задан вручную: accent-overlay на waveform для выбранного диапазона

**После завершения Этапа 1:** запустить `analyze-track` и `rank-vibes` в фоне.

---

### 8.2 Этап 2 — «Фон»

**Settings Panel:**

**3-way переключатель (sliding pill):**
```
┌─────────────────────────────────────────────────────────┐
│  [ Футажи ]     [ Цветной фон ]     [ Фото ]            │
│  └────────┘ ← скользящий accent pill, CSS transform     │
└─────────────────────────────────────────────────────────┘
```
Pill анимация: `transition: transform 0.25s ease`

---

**Режим «Футажи»** (default):

Если вайб-ранкер ещё работает:
```
[spinner]  Анализируем трек...
            [Обновить ↺]
```

Если готово — сетка вайб-карточек (2–3 в ряд):
- Карточка: **только название вайба**, без превью-изображения внутри карточки
- `border-radius: var(--r12)`, padding 12px
- **Multi-select:** можно выбрать несколько вайбов; каждый выбранный = `border: 2px solid var(--accent-light)` + checkmark в углу
- При выборе 2-го и последующих вайбов: inline-уведомление под сеткой:
  «Один вайб = один ролик. Финальное число роликов задаётся на Этапе 5 и ограничено тарифом.»
- Hover на карточку → Preview Panel мгновенно загружает нарезку этого вайба (без клика/выбора)
- Кнопка «Автовыбор по треку» → устанавливает top-1 вайб из ранкера

---

**Режим «Цветной фон»:**

3 плитки в ряд:
```
┌────────────┐  ┌────────────┐  ┌────────────┐
│  [#ffffff] │  │  [#000000] │  │  [#00b140] │
│   Белый    │  │   Чёрный   │  │  Хромакей  │
└────────────┘  └────────────┘  └────────────┘
```
- Каждая: `height: 80px; border-radius: var(--r12); cursor: pointer`
- Выбранная: `border: 2px solid var(--accent-light)`

Под плитками — checkbox:
```
☐ Сделать интерактивным — цвет фона меняется в такт треку  [скоро]
```
- Если отмечен: сохранить `interactive_bg: true` в stageData
- Badge «скоро» (`font-size: 11px; color: var(--text-40)`) — фича добавляется в пайплайн

---

**Режим «Фото»:**

Фотографии **не загружаются пользователем** — подбираются из нашей S3-библиотеки по вайб-механике, аналогично режиму «Футажи».

- Та же сетка: вайб-карточки с названием (без in-card превью)
- Если ранкер не готов: тот же spinner + «Обновить»
- Hover → Preview Panel показывает фото из S3-пула этого вайба
- Multi-select: те же правила что у футажей
- Caption под сеткой: «Фотографии подбираются из нашей библиотеки по вайбу трека»

---

**Валидация «Далее»:** футажи → минимум один вайб выбран; цвет → цвет выбран; фото → минимум один вайб выбран.

**Preview Panel — Этап 2:**
- **Футажи:** при hover или выборе вайба — автоматически загружается S3-нарезка этого вайба: `<video autoplay muted loop playsinline>` в пропорции 9:16. Ключ: `/app/blast808/media/v1/vibes/{vibeId}/preview.mp4` (технология реализована, нарезки прогоняются в S3)
- Если выбрано несколько вайбов: показывать нарезку первого выбранного + лейбл `+N ещё`
- **Цвет:** прямоугольник 9:16 с выбранным цветом + `border: 1px solid var(--text-20)`
- **Фото:** `<img object-fit: cover>` в пропорции 9:16 из S3-пула выбранного вайба

---

### 8.3 Этап 3 — «Хук»

**Settings Panel:**

**[Пропустить]** — secondary кнопка наверху, ширина 100%: «Без хука →»
Клик → `hook: null`, переход на Этап 4.

---

**Дроп-момент:**

Если анализ ещё выполняется:
```
[spinner] Ищем дроп в треке...
          [Обновить ↺]
```

Если готов — пилюли-кандидаты:
```
[1:24 ★]   [1:31]   [1:36]
```
★ — лучший кандидат: `border-color: var(--accent-light)` + `ti-target` иконка

Дополнительно:
- «В отрывке нет дропа» (radio/checkbox)
- «Ввести вручную» → text input (формат M:SS)

---

**Тип хука** (показывается только после выбора дроп-момента):

5 карточек в сетке (2+2+1):

**F1 — Звук**
«Аудио-вставка перед дропом»
При выборе: drop zone для аудио-файла (тот же стиль, max 10МБ, MP3)

**F2 — Объект**
«Фигура-переход + молния на дропе»
При выборе: ряд shape-кнопок: `[Ромб] [Квадрат] [Звезда-10] [Звезда-5] [Эллипс]`

**F3 — Эффект**
«Последовательный хук + переход + стилизация»
При выборе: 3 вертикальных секции (каждая с лейблом «шаг N» + кнопки + «Пропустить»):

Шаг 1 (хук на дропе): `[Молния] [Затвор] [Слоу-шаттер] [Пропустить]`
- Если «Слоу-шаттер»: доп. строка: `[Стандарт] [До конца ролика] [3 футажа после]`

Шаг 2 (переход): `[Снап-вайп] [Минимакс] [Инверт] [Экстракт] [Вспышки] [Тряска] [Пропустить]`

Шаг 3 (стилизация): `[Ксерокс] [Аналог-глитч] [Неон] [Старая камера] [Пиксель-зерно] [Тепловая карта] [Пропустить]`

**F4 — Движение**
«Морфинг в такт + вспышка на дропе»
При выборе: `[Свайп] [Тап] [Зум] [Задержи палец] [Качай головой]`

**F5 — Мысль**
«Голосовая TTS-вставка в первые секунды»
При выборе: `[Панчлайн] [Пропущенное слово] [Эхо] [Вопрос к треку] [Инверсия]`
(превью для F5 не реализуем в v1)

---

Ограничение: если «В отрывке нет дропа» → F1–F4 недоступны (disabled с tooltip), доступны только F5 или «Без хука».

**Валидация «Далее»:** «Без хука» нажата ИЛИ (дроп выбран + тип хука + суб-опции).

**Preview Panel — Этап 3:** pre-rendered видео-клип с применённым эффектом. При каждом выборе хук-типа или суб-опции → Preview Panel обновляется на соответствующий pre-rendered клип. Ключ: `/app/blast808/media/v1/previews/hooks/{hookType}/{subOption}.mp4`. Shimmer-плейсхолдер во время загрузки. Без выбора — чистый футаж из Этапа 2.

---

### 8.4 Этап 4 — «Субтитры»

**Settings Panel:**

5 карточек в сетке 2×2+1 (последняя по центру):

Стили: Impulse / Jakson / Tape / Trendy / Brat

Каждая карточка — **только название стиля**, без описания и без мини-превью:
- Название (16px, weight 500)
- `padding: 16px 20px; border-radius: var(--r12); cursor: pointer`
- Невыбранная: `border: 1px solid rgba(95,66,185,0.25)`
- Выбранная: `border: 2px solid var(--accent-light)` + `ti-check` в правом верхнем углу

**Валидация «Далее»:** один стиль выбран.

**Preview Panel — Этап 4:**
При клике на карточку стиля — Preview Panel немедленно обновляется: Remotion pre-rendered `<video autoplay muted loop playsinline>` в пропорции 9:16 с полноценной анимацией субтитров поверх выбранного футажа. Запрос: `GET /api/preview/subtitle?style={name}&audioKey={s3Key}&lyrics={firstLine}`. Shimmer-плейсхолдер пока загружается.

---

### 8.5 Этап 5 — «Финал»

**Settings Panel:**

**[A] Секция «ЦВЕТА»** (eyebrow)

Цвет субтитров:
```
Цвет субтитров       [■] [#ffffff ______]   [По умолчанию]
```
- `<input type="color">` синхронизирован с HEX text input
- Default: `#ffffff`

Акцентный цвет:
```
Акцентный цвет       [■] [#8b6fe6 ______]   [По умолчанию]
```
Default: `#8b6fe6`

---

**[B] Секция «КОЛИЧЕСТВО РОЛИКОВ»** (eyebrow)

Принцип: **один вайб = один ролик строго.** Перемножения нет. Количество роликов выводится из числа вайбов, выбранных на Этапе 2 (для режимов «Футажи»/«Фото»), и ограничивается тарифом и кредитами.

Поведение:
- Если на Этапе 2 выбрано N вайбов → по умолчанию генерируется N роликов (по одному на вайб, в порядке приоритета выбора)
- Сегмент-селектор позволяет уменьшить число: `[1] [2] ... [N]` (макс = число выбранных вайбов). При выборе K < N → уведомление: «Будет создано K роликов по первым K выбранным вайбам»
- Только для платных (Blast/Glow/Impulse). Для Trial / без подписки: ролик всегда 1 (берётся первый вайб), селектор скрыт
- Если итоговое число роликов > оставшихся кредитов → значения сверх лимита disabled + inline error: «Доступно N генераций (по тарифу)»
- Для режима «Цветной фон» (вайбов нет): ролик всегда 1, секция скрыта

Итоговое число роликов `videosToGenerate = min(выбрано в селекторе, число вайбов, остаток кредитов)`.

---

**[C] Секция «ИТОГ»** (eyebrow)

```
Готово к генерации
─────────────────────────────────────────────────
Субтитры          Impulse
Тайминг           1:24 — 1:46
Исходники         Ночной город, Закат, Неон (3 вайба)
Хук               F4 — Свайп
Цвет субтитров    ● #ffffff
Акцентный цвет    ● #8b6fe6
Роликов           3 (по одному на вайб)
─────────────────────────────────────────────────
[Начать заново]           [Запустить →]
```

«Начать заново» (secondary): очистить сессию + state, редирект на Этап 1.

«Запустить →» (primary, 100%):
1. Проверить кредиты: если `videosToGenerate` > остатка → modal «Нужно пополнить баланс» + кнопка к тарифам
2. `POST /api/wizard/submit { projectId, stageData, videosToGenerate }` → создаёт `GenerationJob` (по одному видео на вайб), списывает кредиты, отправляет задачу в оркестратор
3. Редирект → `/app/processing/{jobId}`

**Preview Panel — Этап 5:**
Финальный Remotion-превью: футаж + субтитры + хук-оверлей (серверный render).
Лейбл «Финальное превью» мелким текстом вверху.

---

## 9. Экран генерации (`/app/processing/[jobId]`)

Отдельная страница. Сайдбар виден.

**Заголовок страницы:**
«Генерация видео» (H1) + «— Название проекта» (var(--text-60))

---

**Для каждого ролика — контейнер (~100px высота):**
```
┌──────────────────────────────────────────────────────────────┐
│ [thumb]  Ролик 1              ████████████░░░░  75%   [⬇ Скачать]│
│ 60×80px  Impulse · Хук: Свайп · Ночной город         [★ Оценить]│
│ shimmer  ● Генерируется                                      │
└──────────────────────────────────────────────────────────────┘
```
- Thumbnail: shimmer-анимация → реальный кадр из видео после готовности
- Описание: стиль + хук + исходник (из `stageData`)
- Прогресс: progress bar `<progress>` + процент
- Статус (левая иконка):
  - В очереди: `ti-clock` серый
  - Генерируется: pulsing accent dot + «Генерируется»
  - Готово: `ti-check` зелёный + «Готово»
  - Ошибка: `ti-x` красный + «Ошибка» + `[Повторить]` кнопка
- «Скачать»: disabled → активна после готовности (прямая S3 ссылка)
- «Оценить»: открывает rating modal

**Polling:** `GET /api/jobs/{jobId}` каждые 3 секунды.

---

**Оценка — НЕ модал, а ненавязчивая карточка-приглашение.**

Когда все ролики готовы, под списком роликов плавно появляется (slide-up + fade) карточка-приглашение. Она не перехватывает фокус и не блокирует скачивание — пользователь может её проигнорировать.

```
┌──────────────────────────────────────────────────────────┐
│  Как получилось?                                    [×]  │
│  [  До 5  ]   [  5–6  ]   [  7–8  ]   [  9–10  ]         │
└──────────────────────────────────────────────────────────┘
```

Та же оценка доступна кнопкой «★ Оценить» на каждой версии — клик скроллит к этой карточке (или раскрывает её inline под конкретной версией).

Логика после выбора оценки (раскрывается внутри той же карточки, без нового окна):
- **До 5:** textarea «Опиши, что не так» + «Отправить» + подпись «Мы исправим бесплатно — свяжемся с тобой»
- **5–6:** quick feedback-теги: `[Субтитры] [Исходники] [Переходы] [Другое]` → сохранить, свернуть карточку
- **7–10:** «Отлично!» + если Trial → мягкий блок с предложением Blast-подписки (не агрессивный, кнопка «Посмотреть тарифы»)

Рейтинг сохраняется: `POST /api/jobs/{jobId}/rate { rating, feedback? }`. После отправки карточка сворачивается в строку «Спасибо за оценку ✓».

---

**После закрытия модала:**
```
              [Сделать ещё один ролик →]   [К проекту →]
```
«Ещё один ролик» → `/app/generate?project={id}` (сессия с тем же проектом, стейт очищается)

---

## 10. API-эндпоинты

### Auth
| Метод | Путь | Описание |
|---|---|---|
| POST | /api/auth/register | Регистрация (без email-верификации; welcome-сообщение шлёт бот) |
| GET | /api/auth/tg-verify | Polling статуса TG верификации |
| NextAuth | /api/auth/[...nextauth] | Все стандартные хендлеры (login, callback, session) |

### Wizard
| Метод | Путь | Описание |
|---|---|---|
| POST | /api/wizard/upload-track | Загрузка аудио → S3, конвертация в MP3 |
| GET | /api/wizard/previous-track | Последний SavedTrack пользователя |
| POST | /api/wizard/analyze-track | Запуск BPM + дроп-детекции |
| GET | /api/wizard/drops | Результаты дроп-анализа |
| POST | /api/wizard/rank-vibes | Запуск LLM вайб-ранжирования |
| GET | /api/wizard/vibes | Результаты вайб-ранкера |
| GET | /api/wizard/session | Загрузка незавершённой сессии |
| POST | /api/wizard/session | Сохранение стейта этапа |
| POST | /api/wizard/submit | Финальный сабмит → создание GenerationJob |

### Preview (серверные, Remotion)
| Метод | Путь | Описание |
|---|---|---|
| GET | /api/preview/subtitle | Pre-render превью стиля субтитров (MP4/WebM) |
| GET | /api/preview/composite | Финальный превью (Этап 5) |

### Jobs
| Метод | Путь | Описание |
|---|---|---|
| GET | /api/jobs/[id] | Статус + прогресс по каждому видео |
| GET | /api/jobs/active | Активный джоб пользователя (для бейджа в сайдбаре) |
| POST | /api/jobs/[id]/rate | Сохранение рейтинга и feedback |

### Projects
| Метод | Путь | Описание |
|---|---|---|
| GET | /api/projects | Список проектов + последний активный |
| POST | /api/projects | Создание (после подтверждения оплаты) |
| GET | /api/projects/[id] | Детали + список джобов |

### Payments
| Метод | Путь | Описание |
|---|---|---|
| POST | /api/payments/create-order | Генерация TBank payment link |
| POST | /api/payments/webhook | TBank webhook (обязательно верифицировать подпись!) |
| POST | /api/payments/cancel-sub | Отмена подписки |

### TikTok
| Метод | Путь | Описание |
|---|---|---|
| GET | /api/tiktok/auth | Старт OAuth flow |
| GET | /api/tiktok/callback | Callback → сохранение токена |
| POST | /api/tiktok/post | Публикация видео |
| DELETE | /api/tiktok/disconnect | Отключение аккаунта |

### Profile
| Метод | Путь | Описание |
|---|---|---|
| PATCH | /api/profile | Обновление имени, ника, аватара |
| POST | /api/profile/avatar | Загрузка аватара → S3 |

---

## 11. S3 Storage

Base URL: `https://s3.twcstorage.ru/f7cef916-asset-storage/`

Существующие ассеты лендинга: `/landing/blast808/media/v1/` (не трогать)

Новые ассеты приложения: `/app/blast808/media/v1/` (новый prefix)

Структура ключей:
```
/app/blast808/media/v1/
  tracks/{userId}/{jobId}/source.mp3        ← загруженные треки (TTL 7 дней)
  videos/{userId}/{jobId}/{version}.mp4     ← готовые видео
  avatars/{userId}/avatar.jpg               ← аватары пользователей
  covers/{projectId}/cover.jpg              ← обложки проектов
  previews/subtitles/{style}/{hash}.mp4     ← pre-rendered превью субтитров (кешируются)
```

---

## 12. Ассеты

### Шрифты
Шрифт Point уже размещён и используется текущим лендингом (в репозитории на гите или на S3 — на том же источнике, что и лендинг). НЕ дублировать файлы. Подключить тем же `@font-face`, что и лендинг, указав существующий путь.

`[TODO: вставить точный URL/путь к Point TTF из источника лендинга]`

Начертания (как в 2.2): Point-Book 350, Point-Regular 400, Point-MediumItalic 500 italic, PointBold 700.

### SVG-иконки
Путь: `/public/assets/` — предоставит клиент как `blast_assets.zip`.

Полный список файлов с точными именами:

```
blast_assets.zip
├── logo-main.svg        ← молния Blast (сайдбар top, страницы auth, 404, loading)
├── logo-wordmark.svg    ← текстовый логотип Blast (если отдельно от молнии; иначе не нужен)
├── logo-glow.svg        ← логотип Glow (фон левой панели на /register и /login)
├── icon-note.svg        ← музыкальная нота (кнопка «Создать проект» на Dashboard)
├── nav-generate.svg     ← иконка плей (сайдбар: пункт «Генерация»)
├── nav-projects.svg     ← иконка лампочки (сайдбар: пункт «Проекты»)
└── nav-stats.svg        ← иконка чарта (сайдбар: пункт «Статистика»)
```

**Важно для nav-*.svg:** экспортируй с `fill="currentColor"` (или `stroke="currentColor"`) вместо хардкодного цвета — тогда активное состояние (`color: var(--accent-light)`) будет работать автоматически через CSS без фильтров.

Если в Figma SVG экспортируется с хардкодным цветом (#ffffff или #8b6fe6) — открой файл и замени hex на `currentColor` вручную перед упаковкой в zip.

### Placeholder'ы (генерируй сам)
- Артисты на auth-страницах: 2–3 темных abstract placeholder
- Дефолтная обложка проекта: gradient rectangle `var(--grad-card)`
- Shimmer анимация для загрузки: `@keyframes shimmer { 0% { background-position: -200% 0 } 100% { background-position: 200% 0 } }` с `background: linear-gradient(90deg, var(--card-bg) 25%, rgba(95,66,185,0.1) 50%, var(--card-bg) 75%)`

---

## 13. Адаптив

**Подход:** Desktop First, Mobile Aware.

```css
@media (max-width: 1280px) { /* container padding: 40px */ }
@media (max-width: 1024px) { /* sidebar: только иконки */ }
@media (max-width: 768px)  { /* полный мобайл */ }
```

**Мобайл (≤768px):**
- Сайдбар скрыт → burger в шапке (`ti-menu-2`)
- Drawer: `transform: translateX(-100%)` → `translateX(0)` при открытии, `transition: 300ms ease`
- Dashboard блоки: 1 колонка
- Визард: вертикальный стек (Preview → Stepper Bar → Track+Next Bar → Settings)
- Тарифы: 1 колонка со скроллом
- Проекты: вертикальный список вместо горизонтального ряда

---

## 14. Технические детали

- **Консистентность дизайна:** все компоненты строятся из токенов раздела 2 и состояний раздела 2.4. Никаких произвольных hex, отступов или длительностей вне токен-шкал. Если нужен новый цвет/отступ — сначала добавить токен, потом использовать
- **Доступность:** все интерактивные элементы имеют `focus-visible` ring; иконки-кнопки имеют `aria-label`; декоративные иконки — `aria-hidden="true"`; контраст текста на фонах не ниже AA
- **Reduced motion:** обернуть все transition/animation в `@media (prefers-reduced-motion: no-preference)`, либо глобально `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; } }`. Crossfade в Preview Panel → мгновенная подмена при reduce
- **Next.js Image** для всех изображений (автоматическая оптимизация + lazy load + blur placeholder)
- **Видео-превью:** `preload="none"` + `IntersectionObserver` — загружать только когда в viewport
- **Wavesurfer:** инициализировать только после успешного upload трека, destroy при размонтировании
- **Remotion-превью:** серверный pre-render на backend, отдавать как MP4 с S3. НЕ делать клиентский рендер в браузере
- **Вайб-ранжирование:** async, не блокировать переход Этап 1 → 2; показывать loading если не готово при входе на Этап 2
- **Дроп-детекция:** async, показывать loading в Этапе 3 если не готово
- **TG polling:** каждые 3с, `setInterval` с `clearInterval` при успехе или таймауте. Таймаут 5 минут → показать «Попробуй обновить страницу»
- **Payments:** ОБЯЗАТЕЛЬНО верифицировать подпись TBank в webhook. Ключ подписи в `.env`
- **Идемпотентность кредитов:** `/api/wizard/submit` принимает idempotency-key (генерируется на клиенте при входе в Этап 5). Списание кредитов и создание джоба — в одной БД-транзакции с проверкой баланса. Повторный сабмит с тем же ключом не создаёт второй джоб и не списывает повторно. Защищает от двойного списания при двух вкладках / дабл-клике
- **Инвалидация зависимостей визарда:** этапы связаны (субтитры Этапа 4 строятся на футаже Этапа 2; хук Этапа 3 — на дроп-моменте трека Этапа 1). При изменении раннего этапа сбрасывать зависимые поля поздних: сменил вайб/футаж → инвалидировать выбор/превью субтитров и финальное превью; сменил трек → инвалидировать дроп, хук, субтитры. Показывать ненавязчивый toast «Настройки субтитров сброшены — поменялся футаж», чтобы пользователь понимал, почему слетел выбор
- **Session security:** httpOnly cookies через NextAuth, CSRF защита автоматически
- **Credentials:** ВСЕ секреты только через `.env.local`. В код не вносить
- **Формы и валидация:** `react-hook-form` + `zod`-схемы для всех форм (регистрация, вход, создание проекта, ручной тайминг, HEX-цвета). Тайминг M:SS парсится и валидируется по границам 5–22с; HEX — по маске `#RRGGBB`. Ошибки показываются по правилам error-состояния из 2.4
- **Error boundaries:** каждый крупный раздел (визард, дашборд, проекты) обёрнут в error boundary с fallback-UI (не белый экран). Suspense-границы для асинхронных загрузок с skeleton из 2.4. Падение одной ручки не должно ронять всю страницу

---

## 15. Environment Variables

```env
# Database
DATABASE_URL=

# NextAuth
NEXTAUTH_URL=https://blast808.com
NEXTAUTH_SECRET=

# Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# TBank
TBANK_TERMINAL_KEY=
TBANK_SECRET_KEY=
TBANK_WEBHOOK_SECRET=

# S3 / TwcStorage
S3_ENDPOINT=
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET=f7cef916-asset-storage
S3_REGION=

# TikTok OAuth
TIKTOK_APP_ID=
TIKTOK_APP_SECRET=

# Blast Orchestrator (бот бэкенд)
ORCHESTRATOR_BASE_URL=
ORCHESTRATOR_API_KEY=
```

---

## 16. Безопасность

Базовый периметр, который нужно заложить сразу, а не оставлять на потом. Сгруппировано по ключевым дырам видео-SaaS с оплатой.

### Авторизация и доступ
- Каждый API-роут под `/api/*` (кроме публичных auth) проверяет сессию NextAuth на сервере. Нет сессии → 401
- **Проверка владения ресурсом (IDOR):** при запросе `/api/projects/[id]`, `/api/jobs/[id]` и т.п. — серверно проверять, что ресурс принадлежит текущему userId. Иначе любой подменит id в URL и получит чужой проект/видео. Это самая частая дыра — закрыть на каждом роуте, читающем ресурс по id
- Middleware защищает все `/app/*` страницы; API защищается отдельно (middleware на страницах не покрывает роуты)

### Загрузка файлов (трек, аватар, обложка, аудио-хук)
- Серверная проверка MIME-типа по содержимому (magic bytes), не по расширению. `.exe`, переименованный в `.mp3`, должен отлетать
- Серверный лимит размера (трек 200 МБ, аватар/обложка — свой меньший лимит), не полагаться на клиентскую валидацию
- Имена файлов в S3 генерировать самим (`{userId}/{uuid}`), не использовать пользовательское имя в пути — защита от path traversal
- Rate limiting на upload-роуты (например, N загрузок в минуту на пользователя)

### Платежи
- Webhook TBank: ОБЯЗАТЕЛЬНО верифицировать подпись. Неподписанный/неверный — отклонять
- Начисление кредитов и активация подписки происходят ТОЛЬКО по верифицированному webhook, никогда по клиентскому редиректу с TBank (редирект можно подделать)
- Сумма и пакет валидируются на сервере при создании заказа, не принимаются с клиента как есть
- Идемпотентность webhook: повторный webhook с тем же payment_id не начисляет кредиты дважды

### Кредиты и генерация
- Списание кредитов — серверное, в транзакции, с проверкой баланса (см. идемпотентность в разделе 14)
- Запуск генерации проверяет на сервере: пользователь верифицирован в TG, баланс достаточен, `videosToGenerate` не превышает лимит тарифа. Клиентские проверки — только для UX, итоговая защита на сервере

### Внешние токены и интеграции
- TikTok access/refresh токены в БД — шифровать at-rest (не хранить в открытом виде)
- TG-верификация: связка userId ↔ tgUserId должна проверяться так, чтобы нельзя было подделать чужую верификацию (использовать непредсказуемый verify-токен, не просто userId в открытом виде, если есть риск перебора)

### Общее
- Rate limiting на auth-роуты (защита от брутфорса логина и спама регистраций)
- Все секреты — только в `.env.local`, никогда в клиентский бандл (в Next.js переменные без префикса `NEXT_PUBLIC_` не утекают на клиент — проверять, что ключи TBank/S3/оркестратора не помечены публичными)
- Заголовки безопасности (CSP, X-Frame-Options и т.п.) через `next.config.js` / middleware
- Никаких персональных данных в URL/query-параметрах

---

## 17. План реализации (порядок кодинга)

Строй сайт фазами. Каждая фаза опирается на предыдущую — не перескакивай. Внутри фаз генерации сверяйся с ботом/оркестратором (раздел 1.1).

**Фаза 0 — Фундамент**
1. Инициализация Next.js + TypeScript, структура папок, токены дизайн-системы (раздел 2) в глобальный CSS, подключение шрифта Point
2. `prisma/schema.prisma` (раздел 4) + миграции + подключение к БД
3. Базовые компоненты из 2.3–2.4 (кнопки, инпуты, карточки, badge, toast, skeleton, modal-обёртка) как переиспользуемая библиотека — на них держится всё остальное

**Фаза 1 — Аутентификация и каркас**
4. NextAuth (Credentials + Google), middleware защиты `/app/*`
5. Страницы `/register` и `/login` (раздел 5)
6. TG-верификация: попап, polling, blocked-стейт, связка с ботом
7. App Shell: сайдбар, app-frame, вертикальное выравнивание (раздел 6), мобильный drawer

**Фаза 2 — Ядро без генерации**
8. Dashboard со всеми состояниями (empty / с проектами), flex-заполнение по высоте
9. Список проектов + modal создания (оба сценария: с подпиской / без), интеграция TBank-оплаты
10. Страница проекта, ЛК/профиль, страница тарифов
11. Глобальный контекст auth/подписки/кредитов + индикатор фоновых джобов в сайдбаре

**Фаза 3 — Визард генерации (сердце продукта)**
12. Каркас визарда: 3-панельный лейаут, Stepper/Track/Settings, Preview Panel с crossfade, сохранение сессии, навигация и инвалидация зависимостей этапов
13. Этап 1 (трек: upload, waveform, текст, тайминг) → подвязать к оркестратору, запустить фоновые analyze-track / rank-vibes
14. Этап 2 (фон: 3-way переключатель, вайбы мульти-селект, цветной фон, фото)
15. Этап 3 (хук: дроп-момент, F1–F5 с суб-опциями, pre-render превью)
16. Этап 4 (субтитры: 5 стилей, Remotion-превью)
17. Этап 5 (финал: цвета, количество роликов, итог, сабмит с идемпотентностью)

**Фаза 4 — Результат и замыкание**
18. Экран генерации `/processing/[id]`: per-ролик контейнеры, polling, скачивание
19. Карточка оценки (ненавязчивая), логика по баллам
20. Автопостинг в TikTok из проекта, toast-уведомления о готовности

**Фаза 5 — Полировка**
21. Заглушки: статистика (coming soon), 404/error
22. Адаптив-проход по всем страницам (мобайл/планшет)
23. Безопасность (раздел 16): аудит владения ресурсами, rate limiting, проверки загрузок, заголовки
24. Доступность, reduced-motion, error boundaries, финальный QA-проход

После каждой фазы — рабочее, проверяемое состояние. Не начинай фазу, пока предыдущая не работает.

---

## 18. Открытые вопросы для клиента

Перед тем как начинать — запросить у клиента:

1. **`.env` значения** — TBank Terminal Key + Webhook Secret, S3 credentials, Orchestrator URL
2. **TikTok App ID/Secret** — после регистрации приложения в TikTok Developer Portal
3. **Оркестратор API** — документацию или схему эндпоинтов бота для интеграции
4. **SVG zip** — иконки логотипов и навигации (шрифты Point уже есть, см. раздел 12)
5. **Remotion deploy** — использовать Remotion Lambda или self-hosted render сервер
6. **TG бот токен** — для верификации incoming webhook от бота

---

*Конец system prompt. Начинай с `prisma/schema.prisma` → настройка NextAuth → App Shell layout → Registration → Login → Dashboard → Wizard → остальные страницы.*
