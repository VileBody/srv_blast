# Blast Landing — Style Guide & Design Tokens

Источник истины: `landing/` (HTML/CSS/JS). Последнее обновление: 2026-06-28.

---

## 1. Цветовая палитра

### CSS-переменные (`:root`)

| Переменная | Hex / rgba | Описание |
|---|---|---|
| `--bg` | `#05010f` | Фон страницы |
| `--nav-bg` | `#150F25` | Фон navbar |
| `--hero-r-bg` | `#150f25` | Фон hero-right карточки |
| `--card-bg` | `#060114` | Объявлена, используется отдельно |
| `--text` | `#f6f5fd` | Основной белый текст |
| `--text-80` | `rgba(246,245,253,.8)` | Приглушённый текст |
| `--text-60` | `rgba(246,245,253,.6)` | Объявлена, резерв |
| `--text-40` | `rgba(246,245,253,.4)` | Неактивные шаги |
| `--g-start` | `#8b6fe6` | Начало градиента (светло-фиолетовый) |
| `--g-end` | `#5f42b9` | Конец градиента (тёмно-фиолетовый) |
| `--accent` | `#5f42b9` | Акцентный цвет |
| `--accent-80` | `rgba(95,66,185,.8)` | Объявлена, резерв |
| `--accent-20` | `rgba(95,66,185,.2)` | Объявлена, резерв |
| `--pill-border` | `#8b6fe6` | Граница пилюлей и тегов |

### Дополнительные цвета (без переменных)

| Hex / rgba | Где используется |
|---|---|
| `#0d0828` | Начало градиента карточек (steps, feat, cta) |
| `#0a0520` | Средний тон карточек |
| `#080318` | Конец градиента карточек |
| `rgba(95,66,185,.25)` | Граница steps-card, cta-card |
| `rgba(95,66,185,.4)` | Граница stat-card; scrollbar-thumb |
| `rgba(246,245,253,.15)` | Разделители feat-col |
| `rgba(246,245,253,.2)` | Граница feat-card |
| `rgba(217,217,217,.2)` | Фон feat-tag (dim и accent) |
| `rgba(6,1,20,0.85)` | Фон stat-card и feat-pill (тёмное стекло) |
| `#6c6c6c` | Пунктирная граница «Агентство» в сравнении |
| `#1a1528` | Фон legal-popup |
| `rgba(0,0,0,.75)` | Backdrop legal-popup |
| `rgba(5,1,15,0.85)` | Backdrop video-modal |
| `#9B7AFF` | Ссылки внутри legal-popup |
| `#A080FF` | Active nav link (устанавливается через JS) |

### Градиенты — шпаргалка

```css
/* Основной (текст, иконки) */
linear-gradient(175deg, #8b6fe6 0%, #5f42b9 100%)

/* Кнопки */
linear-gradient(90deg, #8b6fe6 0%, #5f42b9 100%)

/* Карточки (тёмный фон) */
linear-gradient(135deg, #0d0828 0%, #0a0520 50%, #080318 100%)

/* Оверлей hero-right снизу */
linear-gradient(0deg, rgba(21,15,37,1) 0%, rgba(21,15,37,0) 100%)

/* Glare (шаг 3 в steps) */
linear-gradient(to right, transparent, rgba(246,245,253,0.35), transparent)
```

---

## 2. Типографика

### Шрифтовые семейства

```css
--font:      'Point', -apple-system, sans-serif;
--font-deco: 'DriveHearts', cursive;
```

**Point** — единственный рабочий шрифт для всего интерфейса.
**DriveHearts** — декоративный, используется только в `.cta-deco` (420px, opacity 0.07, невидим на mobile).

### @font-face — Point

| weight | style | Файл |
|---|---|---|
| `400` | normal | `Point-Regular.ttf` |
| `350` | normal | `Point-Book.ttf` |
| `500` | italic | `Point-MediumItalic.ttf` |
| `700` | normal | `PointBold.ttf` |

Все с `font-display: swap`.

### Размеры (desktop → mobile ≤768px)

| Элемент | Desktop | Mobile | Weight |
|---|---|---|---|
| `body` | `24px` | `14px` | 400 |
| `h1` (hero) | `64px` | `32px` | 400 |
| `h1` italic span | `64px` | `32px` | 500 italic |
| `h2` (section) | `64px → 52px → 44px` | `32px` | 400 |
| `steps-text` | `64px → 52px → 44px` | `32px` | 400 |
| eyebrow | `24px` | `14px` | 400 (gradient) |
| nav links | `24px` | `14px` | 350 |
| `.btn` | `24px` | `14–16px` | 400 |
| social / feat / example | `24px` | `14px` | 350 |
| stat-label | `20px` | `18px` | 400 |
| stat-num / stat-lbl | `16px` | `17px / 14px` | 400 / 350 |
| feat-compare | `16px` | `14px` | 350 |
| legal popup h2 | `28px` | `22px` | 600 |
| legal popup h3 | `18px` | `16px` | 600 |
| legal popup body | `15px` | `13px` | 350 |

`line-height: normal` везде, кроме `steps-text → 1.2`. `letter-spacing: 0` у h1/h2.

---

## 3. Геометрия и пространство

### Контейнер

| Переменная | Значение | Применяется |
|---|---|---|
| `--container` | `1440px` | max-width |
| `--side` | `120px` | padding боков (desktop) |
| — | `40px` | padding при ≤1280px |
| — | `20–30px` | padding при ≤768px |

### Радиусы (CSS-переменные)

| Переменная | Значение | Элементы |
|---|---|---|
| `--r40` | `40px` | `.btn` |
| `--r20` | `20px` | Navbar, hero-right, steps-card, feat-card, cta-card, modal, stat-card |
| `--r15` | `15px` | example-slide, feat-compare |
| `--r12` | `12px` | hero-badge |

Дополнительно: `9px` (feat-tag, feat-pill), `24px` (legal-popup-body), `50%` (аватары).

### Отступы секций (margin-top)

| Секция | Desktop | Mobile |
|---|---|---|
| `.social` | `100px` | `0` |
| `.section-block` (общий) | `163px` | `120px` |
| `#how-it-works` | `165px` | `120px` |
| `.cta-section` | `163px` | `60px` |
| `.footer` | `160px` | `60px` |

### Layout-паттерны

- **Navbar:** `flex; align-items: center; height: 80px`. Nav-ссылки — `position: absolute; left: 50%; translateX(-50%)`
- **Hero:** `flex; gap: clamp(40px, 9.8%, 118px); align-items: flex-start`. ≤1024px → `flex-direction: column`
- **feat-card:** `grid-template-columns: repeat(3, 1fr)`. ≤1024px → `1fr`
- **Footer:** `flex; justify-content: space-between; gap: 40px`. Mobile → `flex-direction: column`
- **Steps:** `height: 420vh` wrapper, `position: sticky; top: 50px` viewport-sticky (mobile: `350vh; top: 20px`)
- **Examples:** горизонтальный `flex; overflow-x: auto; scroll-snap-type: x mandatory`

---

## 4. Ассеты

### PNG (растровые)

| Файл | Назначение | Интеграция |
|---|---|---|
| `backforheader.png` | Декоративный фон navbar | `<img>` position:absolute, inset 0 |
| `containerblur.png` | Blur поверх телефона hero (desktop) | `<img>` position:absolute, z-index 3 |
| `herophone.png` | iPhone 16 Pro рамка | `<img>` 456×945px (desk), 315×652px (mob) |
| `heroblur.png` | Blur-текстура mobile hero | `<img>` position:absolute, bottom -165px |
| `backforbutton.png` | Текстура поверх кнопки | `<img>` внутри `.btn`, mix-blend-mode: plus-lighter |
| `biglogo.png` | Полупрозрачный лого в фоне steps | `<img>` position:absolute, w:42%, z-index 0 |
| `howworkblur.png` | Blur-фон секции How it works | `<img>` position:absolute, w:52% |
| `workstage1.png` | Скриншот шага 1 (desktop) | `<img>` position:absolute, top:-48px |
| `workstage2.png` | Скриншот шага 2 (desktop) | `<img>` position:absolute |
| `workstage3.png` | Скриншот шага 3 (desktop) | `<img>` position:absolute, bottom:-48px |
| `workstage1-mob.png` | Шаг 1 (mobile) | `<img>` h:316px |
| `workstage2-mob.png` | Шаг 2 (mobile) | `<img>` h:316px |
| `workstage3-mob.png` | Шаг 3 (mobile) | `<img>` h:300px |
| `featblur.png` | Blur за features (left:70px, w:460px, opacity:0.55) | `<img>` position:absolute |
| `backlineblur.png` | Blur под backline.svg (scale:2.5) | `<img>` position:absolute, left:-80px |
| `cta.png` | Иллюстрация в CTA-карточке | `<img>` position:absolute, left:50px, h:350px |
| `marketingblur.png` | Blur справа от CTA-заголовка (w:55%) | `<img>` position:absolute |
| `footer.png` | Логотип/иллюстрация в footer-brand | `<img>` w:100% |
| `person0–5.svg` | Аватары артистов (social proof) | `<img class="avatar-circle">` 100×100px, border-radius:50% |
| `labelA/Y/R.svg` | Логотипы лейблов | `<img class="label-circle">` 50×50px |

### SVG (векторные)

| Файл | viewBox | Назначение | Интеграция | Размер |
|---|---|---|---|---|
| `impulselogo.svg` | `0 0 42 40` | Логотип Blast в navbar | `<img>` | 42×40 |
| `blastmainlogo.svg` | `0 0 16 12` | Иконка-молния Blast | `<img>` | 24×18 (hero), 14×14 (feat) |
| `sublogoblast.svg` | `0 0 20 20` | Иконка в social-header | `<img>` | 20×20 |
| `arrow1.svg` | `0 0 9 15` | Стрелка в CTA-кнопке | `<img>` | 16×16 |
| `tgvector2.svg` | — | Telegram в hero-кнопке | `<img>` | 17×15 |
| `tgvector.svg` | — | Telegram в footer | `<img>` | 17×15 |
| `igvector.svg` | — | Instagram в footer | `<img>` | 20×20 |
| `vkvector.svg` | — | VK в footer | `<img>` | 20×20 |
| `cut.svg` | `0 0 23 21` | Ножницы в examples-header | `<img>` | 24×24 |
| `backline.svg` | `0 0 776 794` | Органическая кривая в features | `<img>` position:absolute, scale(1.5) | ~full-height |
| `formatlines.svg` | `0 0 161 100` | Mindmap-развилка (5 ветвей) в feat-col | `<img>` | 195×150 |
| `logo.svg` | — | Favicon | `<link rel="icon">` | — |

Все SVG интегрированы как `<img src="...svg">`, не inline и не sprite.

Не используются в текущем `index.html` (заготовки): `logo-badge.svg`, `ig-icon.svg`, `tg-icon.svg`, `vk-icon.svg`, `avatar-1…5.svg`, `label-1…3.svg`.

---

## 5. Секции

### Navbar
**Класс:** `.navbar-wrap > header.navbar`

- `height: 80px; background: #150F25; border-radius: 20px`
- `backdrop-filter: blur(50px); padding: 0 48px 0 30px`
- `position: relative; top: 20px` (плавает над страницей)
- Содержит: `impulselogo.svg`, 3 nav-ссылки (Как работает / Примеры / Преимущества), CTA «Попробовать» (gradient text)
- Mobile: `height: 50px; border-radius: 10px`; nav-links и бургер скрыты; остаётся только CTA

### Hero
**Класс:** `section.hero#hero`

- `padding-top: 100px`
- **hero-inner:** flex, `gap: clamp(40px, 9.8%, 118px)`
- **hero-right:** `width: 600px; height: 390px; background: #150f25; border-radius: 20px`
- **hero-phone-wrap:** position:absolute, `left: -228px; top: 40px; width: 456px; height: 945px`
- Ассеты: `herophone.png`, `hero_phone.mp4` (S3), `containerblur.png`, `heroblur.png` (mobile), `backforbutton.png`, `blastmainlogo.svg`, `tgvector2.svg`
- Анимация: typing effect на italic h1 (75ms/символ); fadeUp (0.7s) для navbar и всех блоков hero

### Social Proof
**Класс:** `section.social#social`

- `margin-top: 100px`
- Аватары: `width/height: 100px; border-radius: 50%; border: 2px solid #05010f; margin-left: -45px`
- Счётчик: анимируется через JS (easeOutCubic, 1400ms) при попадании в viewport
- Ассеты: `sublogoblast.svg`, `person0–5.svg`, `labelA/Y/R.svg`

### How It Works
**Класс:** `section.section-block#how-it-works`

- Wrapper `height: 420vh` (mobile: `350vh`); карточка sticky `top: 50px`
- steps-card: `background: linear-gradient(135deg,#0d0828,#0a0520,#080318); border: 1px solid rgba(95,66,185,.25); padding: 48px 56px; min-height: 400px`
- Scroll-progress разделяет на 3 части (0–33% → 33–66% → 66–100%); иллюстрации — `opacity: 0/1; transition: 0.45s`
- Шаг 3: два stat-card, `rotate(3deg) / rotate(-4deg)`, `backdrop-filter: blur(12px)`, glare-полоса
- Ассеты: `howworkblur.png`, `biglogo.png`, `workstage1–3.png`, `workstage1–3-mob.png`

### Examples
**Класс:** `section.section-block#examples`

- Горизонтальный scroll-snap flex, `gap: 24px`
- `.example-col`: `width: 220px` (desktop) / `260px` (mobile)
- `.example-slide`: `aspect-ratio: 9/16; background: #0a0520; border-radius: 15px`
- Play-button: `::after` псевдоэлемент, круг 48px, `backdrop-filter: blur(6px)`
- Видео: lazy-load через IntersectionObserver (`threshold: 0.35; rootMargin: 180px`)
- Mobile carousel: center-snap, центральная карточка `scale(1) opacity:1`, остальные `scale(0.85) opacity:0.5`
- Video modal: `#videoModal`, `background: rgba(5,1,15,0.85); backdrop-filter: blur(12px)`, плеер `width: min(360px,90vw)`
- Ассеты: 5 mp4 с S3 (`/landing/blast808/media/v1/`), `cut.svg`

### Features
**Класс:** `section.section-block#features`

- feat-card: `grid-template-columns: repeat(3,1fr); border: 1px solid rgba(246,245,253,.2); border-radius: 20px`
- Колонка 1 — До/После: теги dim (`background: rgba(217,217,217,.2)`) и accent (`border: 1px solid #8b6fe6`)
- Колонка 2 — Mindmap: `formatlines.svg` + 5 pill-тегов (абсолютно позиционированы по ветвям)
- Колонка 3 — Сравнение: Blast `border: 1px solid #8b6fe6; border-radius: 15px 0 0 15px` | Агентство `border: 1px dashed #6c6c6c; border-radius: 0 15px 15px 0`
- Ассеты: `backline.svg` (scale:1.5), `featblur.png`, `backlineblur.png`, `formatlines.svg`, `blastmainlogo.svg`

### CTA
**Класс:** `section.cta-section#cta`

- cta-card: `height: 400px; border-radius: 20px; border: 1px solid rgba(95,66,185,.25)`
- Декоративный текст `.cta-deco`: DriveHearts, `font-size: 420px; opacity: 0.07; gradient`; скрыт на mobile
- Ассеты: `cta.png` (left:50px, bottom:0, h:350px), `marketingblur.png`, `arrow1.svg`

### Footer
**Класс:** `footer.footer`

- `margin-top: 160px; padding: 60px 0`
- footer-brand: `margin-left: -120px` (выезжает за контейнер), `max-width: 55%`
- 3 legal popup встроены в HTML (не iframe): `position: fixed; inset: 0; z-index: 9999`
  - Backdrop: `rgba(0,0,0,.75); backdrop-filter: blur(6px)`
  - Body: `background: #1a1528; border-radius: 24px; padding: 48px; max-height: 85vh`
- Ассеты: `footer.png`, `tgvector.svg`, `igvector.svg`, `vkvector.svg`

---

## 6. Компоненты UI

### Кнопки

**Primary `.btn`:**
```css
height: 70px;
padding: 0 40px;
border-radius: 40px; /* --r40 */
background: linear-gradient(90deg, #8b6fe6, #5f42b9);
font-size: 24px;
font-weight: 400;
color: #f6f5fd;
/* + backforbutton.png overlay (mix-blend-mode: plus-lighter) */
/* + ::before glow: radial-gradient(ellipse, rgba(139,111,230,.5), transparent 60%) */
```
Hover: `opacity: .88; translateY(-1px)` (0.2s / 0.15s)

Mobile: `height: 50px; font-size: 16px; border-radius: 30px`
Hero mobile: `height: 46px; font-size: 14px; border-radius: 23px`

**Navbar CTA** — gradient text (не кнопка, `<a>`):
```css
background: linear-gradient(175deg, #8b6fe6, #5f42b9);
-webkit-background-clip: text;
-webkit-text-fill-color: transparent;
font-size: 24px; font-weight: 400;
```

### Теги / пилюли

**feat-tag dim:**
```css
height: 37px; padding: 0 12px; border-radius: 9px;
background: rgba(217,217,217,.2);
color: rgba(246,245,253,.6);
```

**feat-tag accent:**
```css
/* то же + */
border: 1px solid #8b6fe6;
color: gradient (#8b6fe6 → #5f42b9);
```

**feat-pill (mindmap):**
```css
height: 37px; padding: 0 14px; border-radius: 9px;
background: rgba(6,1,20,0.85);
border: 1.5px solid #8b6fe6;
backdrop-filter: blur(5px);
font-size: 24px; font-weight: 250;
```

**hero-badge:**
```css
height: 45px; padding: 0 15px; border-radius: 12px;
background: #05010f;
border: 1px solid rgba(95,66,185,.8);
/* текст: gradient 175deg */
```

**stat-card:**
```css
width: 172px;
background: rgba(6,1,20,0.85);
backdrop-filter: blur(12px);
border: 1px solid rgba(95,66,185,.4);
border-radius: 20px;
padding: 12px 14px 14px;
rotate: 3deg; /* или -4deg для второй карточки */
```

### Скроллбар

```css
/* examples-scroll */
scrollbar-width: thin;
scrollbar-color: rgba(95,66,185,.4) transparent;
::-webkit-scrollbar { height: 4px; }
::-webkit-scrollbar-thumb { background: rgba(95,66,185,.4); border-radius: 4px; }
```

---

## 7. Анимации и интерактив

### CSS-анимации

| Имя | Параметры | Что делает |
|---|---|---|
| `@keyframes fadeUp` | `opacity:0→1; translateY(24px→0); 0.7s ease` | Load-анимация navbar и hero |
| `.reveal` / `.revealed` | `opacity:0→1; translateY(28px→0); 0.65s ease` | Scroll-reveal секций |
| `@keyframes blink` | `step-end infinite; 0.75s` | Мигающий курсор typing |
| `.example-slide` hover | `translateY(-5px); 0.25s ease` | Hover-lift карточек |
| `.steps-line` | `color transition 0.4s ease` | Переключение шагов |
| `.steps-stage` | `opacity transition 0.45s ease` | Смена иллюстрации |
| `.btn` hover | `opacity:.88; translateY(-1px); 0.2s` | Hover кнопки |
| `.video-modal` | `opacity:0→1; 0.25s` | Открытие модала |
| `.legal-popup` | `opacity + visibility; 0.3s` | Открытие попапа |

Delays `.load-fade`: `0.05s / 0.15s / 0.25s / 0.35s / 0.45s` (каскадное появление).

### JavaScript (main.js) — сторонние библиотеки отсутствуют

| Функция | Описание |
|---|---|
| Hero typing effect | Typewriter на `.hero-h1-italic`: 75ms/символ (40ms пробел), cursor blink → удаляется через 1200ms |
| Sticky steps | `scroll` listener: `progress = scrolled/total`, делит на 3 части → обновляет active-классы шагов |
| Scroll reveal | `IntersectionObserver(threshold:0.12)` → `section-head, feat-col, example-col, steps-card, cta-*` |
| Counter animation | `IntersectionObserver(threshold:0.5)`, easeOutCubic, 1400ms — для `.social-count` |
| Example videos | Lazy src из S3, `IntersectionObserver(threshold:0.35, rootMargin:180px)` — play/pause |
| Mobile carousel | Center-snap, JS центрирует 3-ю карточку при загрузке |
| Active nav link | `IntersectionObserver(rootMargin:'-30% 0px -60% 0px')`, цвет `#A080FF` |
| Hero video | Src из `BLAST_MEDIA_CONFIG.baseUrl + /hero_phone.mp4` → `load(); play()` |
| Video modal | Клик на slide → `#videoModal`, portrait по `data-portrait="true"` |
| Legal popups | `data-popup` атрибут → `classList.add('is-open')`, close на backdrop/Escape |

S3 base URL медиа: `https://s3.twcstorage.ru/f7cef916-asset-storage/landing/blast808/media/v1/`

---

## 8. Адаптив

### Breakpoints

| Breakpoint | Ключевые изменения |
|---|---|
| `≤1280px` | container padding → 40px; hero gap → 60px; hero-left → 420px; hero-right → 520px; h2/steps → 52px; feat-col padding → 32px |
| `≤1024px` | hero-inner → column; hero-right height → 360px; feat-card → 1 колонка; h2/steps → 44px |
| `≤768px` | Полный mobile layout (см. ниже) |

### Mobile ≤768px

- **Типографика:** body 14px, h1/h2 32px, eyebrow 14px, все UI-тексты 14px
- **Navbar:** height 50px, radius 10px, nav-links скрыты, CTA остаётся
- **Hero:** телефон центрируется `left:50%; translateX(-50%)`, 315×652px; `containerblur.png` скрыт, `heroblur.png` виден; CTA-блок `background:rgba(21,15,37,0.92); border-radius:15px; backdrop-filter:blur(8px); width:250px`
- **Social:** аватары 50×50px, первый скрыт; layout column
- **Steps:** wrapper 350vh; sticky top:20px; desktop bg-blur/logo скрыты; mobile-версии иллюстраций `workstage*-mob.png`; stat overlay абсолютно поверх карточки
- **Examples:** width 260px, wrapper 100vw с margin-left:-30px; scrollbar скрыт; carousel center-snap
- **Features:** feat-card grid 1fr, border/background убраны; каждая feat-col → `border-radius:20px; background:gradient; border:1px solid rgba(95,66,185,.2)` самостоятельно; декоративный фон скрыт
- **CTA:** margin-top 60px; cta-card height:auto, flex-direction:column; `cta.png` и `cta-deco` скрыты; появляется `cta-action-label`
- **Footer:** margin-top 60px; padding 40px 0; inner → column

---

## 9. CSS-токены (копируй в новый проект)

```css
:root {
  /* Colors */
  --bg:         #05010f;
  --nav-bg:     #150F25;
  --text:       #f6f5fd;
  --text-80:    rgba(246,245,253,.8);
  --text-40:    rgba(246,245,253,.4);
  --g-start:    #8b6fe6;
  --g-end:      #5f42b9;
  --accent:     #5f42b9;
  --pill-border:#8b6fe6;

  /* Card dark bg */
  --card-dark-start: #0d0828;
  --card-dark-mid:   #0a0520;
  --card-dark-end:   #080318;

  /* Gradients */
  --grad-main:   linear-gradient(175deg, #8b6fe6 0%, #5f42b9 100%);
  --grad-btn:    linear-gradient(90deg,  #8b6fe6 0%, #5f42b9 100%);
  --grad-card:   linear-gradient(135deg, #0d0828 0%, #0a0520 50%, #080318 100%);

  /* Typography */
  --font:      'Point', -apple-system, sans-serif;
  --font-deco: 'DriveHearts', cursive;

  /* Radii */
  --r40: 40px;
  --r20: 20px;
  --r15: 15px;
  --r12: 12px;

  /* Layout */
  --container: 1440px;
  --side:      120px;
}
```
