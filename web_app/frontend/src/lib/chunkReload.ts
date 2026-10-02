/*
 * Кусок JS не загрузился — чаще всего это вкладка, открытая до деплоя: в её index.html
 * имена кусков старые, а на сервере их уже нет. Перезагрузка страницы подтягивает новый
 * index.html и всё открывается. Раньше вместо этого вылезало «Один из блоков не загрузился»,
 * а «Повторить» не помогал: React.lazy запоминает упавший импорт.
 *
 * Перезагружаем один раз; если и после неё кусок не грузится (сеть, а не деплой) — не
 * зацикливаемся, ошибка уходит в ErrorBoundary как есть.
 */
const KEY = 'blast:chunk-reload-at';
const WINDOW_MS = 30_000;

/** true — страница уходит на перезагрузку (ждать нечего). */
export function reloadForFreshChunks(): boolean {
  try {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last < WINDOW_MS) return false;
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    return false; // без sessionStorage не можем гарантировать отсутствие цикла
  }
  window.location.reload();
  return true;
}

/** Обёртка для lazy-импортов: упал — одна перезагрузка за свежими кусками. */
export function importWithReload<T>(load: () => Promise<T>): Promise<T> {
  return load().catch((error: unknown) => {
    if (reloadForFreshChunks()) return new Promise<T>(() => undefined);
    throw error;
  });
}

/** Vite сообщает о неудачной подгрузке зависимостей куска отдельным событием. */
export function installChunkReloadGuard(): void {
  window.addEventListener('vite:preloadError', (event) => {
    if (reloadForFreshChunks()) event.preventDefault();
  });
}
