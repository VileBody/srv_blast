# Blast React + Tailwind + FastAPI Mock

Нормальная двухчастная версия мок-приложения Blast:

- `frontend/` — Vite + React 18 + TypeScript + Tailwind + React Router + TanStack Query + Zustand.
- `backend/` — FastAPI mock API с in-memory store и теми же моковыми ручками из ТЗ.
- `frontend/public/assets/` — текущие SVG-ассеты: лого, glow, navigation icons, note, placeholders.

## Запуск локально

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Проверка:

```bash
curl http://127.0.0.1:8000/healthz
```

### 2. Frontend

В другом терминале:

```bash
cd frontend
npm install
npm run dev
```

Открыть:

```text
http://127.0.0.1:5173/login
```

Vite проксирует `/api/*` и `/static/*` на FastAPI `http://127.0.0.1:8000`, поэтому CORS в обычном dev-flow не нужен. CORS для `localhost:5173` также добавлен в FastAPI для прямых вызовов.

Демо-вход:

```text
demo@blast808.com
пароль любой
```

## Что реализовано во frontend

- Auth pages `/login`, `/register` с TG verification modal/polling.
- App Shell с sidebar, mobile drawer, active job polling.
- Dashboard `/app`.
- Projects `/app/projects` + modal создания проекта с двумя сценариями.
- Project detail `/app/projects/:id`.
- Profile `/app/profile`, avatar upload mock, TikTok connect/disconnect mock.
- Pricing `/app/pricing`.
- Stats placeholder `/app/stats`.
- Wizard `/app/generate` на Zustand: трек → фон → хук → субтитры → финал.
- Processing `/app/processing/:jobId` с polling, progress per video, rating card.
- Reusable UI-kit: Button, Input/Textarea, Card, StatusBadge, Modal, Toast, Skeleton, ProgressBar.

## Проверки

Из корня архива были выполнены:

```bash
cd frontend
npm run typecheck
npm run build
npm audit --omit=dev

cd ../backend
python -m compileall app
python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
for path in ['/healthz','/api/me','/api/projects','/api/wizard/vibes','/api/jobs/active']:
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
assert client.post('/api/auth/login', json={'email':'demo@blast808.com','password':'x'}).status_code == 200
PY
```

## Tailwind/design tokens

Все базовые токены из ТЗ лежат в `frontend/src/index.css` в `:root`. Tailwind config прокидывает их как классы (`bg-bg`, `bg-grad-card`, `text-text-60`, `p-space-5`, `rounded-r20` и т.д.).

Шрифты Point не вложены в архив: в CSS оставлены `@font-face` пути `/fonts/Point-*.ttf`, чтобы подкинуть реальные TTF в `frontend/public/fonts/` без изменения кода.

## API

Клиентская прослойка лежит в `frontend/src/lib/api.ts`. Основные mock endpoints:

- `/api/auth/login`, `/api/auth/register`, `/api/auth/tg-verify`
- `/api/me`
- `/api/projects`, `/api/projects/{id}`
- `/api/wizard/upload-track`, `/api/wizard/vibes`, `/api/wizard/drops`, `/api/wizard/session`, `/api/wizard/submit`
- `/api/jobs/active`, `/api/jobs/{id}`, `/api/jobs/{id}/rate`
- `/api/payments/create-order`
- `/api/tiktok/auth`, `/api/tiktok/post`, `/api/tiktok/disconnect`
- `/api/profile`, `/api/profile/avatar`

## Production notes

Это всё ещё mock: БД, TBank, S3, TikTok, TG bot и оркестратор не подключены. Контракты вынесены в `api.ts`, поэтому заменить моковый FastAPI на реальные ручки можно без переписывания UI.
