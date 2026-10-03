/**
 * Per-аккаунт (не per-проект — не путать с blast-wizard-v4, который чистится
 * при каждом новом проекте) память о подсказках: раз показанная подсказка
 * больше не триггерится автоматически, а её idle-реактивация («завис 45с»)
 * срабатывает не чаще одного раза за подсказку НАВСЕГДА, а не за каждый
 * визит на этап.
 *
 * Ключ — по id пользователя: раньше ключ был общий на браузер и стирался на выходе,
 * чтобы новый аккаунт увидел онбординг, — но тогда и тот же человек после перелогина
 * проходил его заново. Теперь выход ничего не чистит, а у каждого аккаунта своя память.
 */
const LEGACY_STORAGE_KEY = 'blast-guides-seen-v1';

/** Ключ памяти подсказок конкретного аккаунта в localStorage. */
export function guidesStorageKey(userId: string): string {
  return `${LEGACY_STORAGE_KEY}:${userId}`;
}

/**
 * seen — подсказку показывали; idleUsed — разовая реактивация по простою истрачена;
 * acted — человек сделал действие, о котором она говорит (см. useGuideAction): следующая
 * подсказка цепочки ждёт именно его, а не только закрытия этой.
 */
type GuideRecord = { seen?: boolean; idleUsed?: boolean; acted?: boolean };
type GuideMemory = Record<string, GuideRecord>;

let guideUser: string | null = null;

/**
 * Привязать память к вошедшему аккаунту. Зовётся синхронно в рендере оболочки
 * (ProfileSetupGate в AppShell) — до страниц, которые читают подсказки в useState-инициализаторах.
 */
export function bindGuideUser(userId: string | null): void {
  if (userId === guideUser) return;
  guideUser = userId;
  if (userId) migrateLegacy(userId);
}

/*
 * Разовый перенос старого общего ключа. Старый код стирал его на выходе, значит лежащий
 * в браузере ключ накопил тот, кто вошёл после последнего выхода, — ему и отдаём.
 */
function migrateLegacy(userId: string) {
  try {
    const legacy = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy === null) return;
    const key = guidesStorageKey(userId);
    if (window.localStorage.getItem(key) === null) window.localStorage.setItem(key, legacy);
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* хранилище недоступно — переносить нечего */
  }
}

function readAll(): GuideMemory {
  // без аккаунта помнить не за кем; подсказки живут под AppShell, где он уже привязан
  if (!guideUser) return {};
  try {
    const raw = window.localStorage.getItem(guidesStorageKey(guideUser));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeAll(data: GuideMemory) {
  if (!guideUser) return;
  try {
    window.localStorage.setItem(guidesStorageKey(guideUser), JSON.stringify(data));
  } catch {
    /* приватный режим / квота — просто не запоминаем между сессиями */
  }
}

export function readGuideRecord(id: string): GuideRecord {
  return readAll()[id] ?? {};
}

const CHANGE_EVENT = 'blast-guide-record-changed';

export function writeGuideRecord(id: string, patch: GuideRecord) {
  const all = readAll();
  all[id] = { ...all[id], ...patch };
  writeAll(all);
  // Два гайда из разных, не связанных пропсами компонентов (например,
  // track-timing в StageOne и text-lyrics в соседнем TextPanel) могут зависеть
  // друг от друга по цепочке — без этого события вторая подсказка узнавала бы
  // о dismissed первой только на следующем маунте/перезагрузке.
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

export function subscribeGuideRecord(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}
