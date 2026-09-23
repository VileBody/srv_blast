/**
 * Per-браузер (не per-проект — не путать с blast-wizard-v4, который чистится
 * при каждом новом проекте) память о подсказках: раз показанная подсказка
 * больше не триггерится автоматически, а её idle-реактивация («завис 45с»)
 * срабатывает не чаще одного раза за подсказку НАВСЕГДА, а не за каждый
 * визит на этап.
 */
const STORAGE_KEY = 'blast-guides-seen-v1';

type GuideRecord = { seen?: boolean; idleUsed?: boolean };
type GuideMemory = Record<string, GuideRecord>;

function readAll(): GuideMemory {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeAll(data: GuideMemory) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* приватный режим / квота — просто не запоминаем между сессиями */
  }
}

export function readGuideRecord(id: string): GuideRecord {
  return readAll()[id] ?? {};
}

export function writeGuideRecord(id: string, patch: GuideRecord) {
  const all = readAll();
  all[id] = { ...all[id], ...patch };
  writeAll(all);
}
