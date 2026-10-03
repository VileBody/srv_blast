/**
 * Путь внутри приложения, куда вернуть человека: после входа (`/login?next=…`) и после
 * оплаты трипваера (`returnPath`). Та же проверка, что `security.APP_RETURN_PATH_RE` на
 * бэке: только `/app…` с query, сегменты непустые (без `//`), без точек, схем, хостов и `#`.
 * Такой путь не уведёт с сайта — открытого редиректа нет.
 */
const APP_PATH_RE = /^\/app(?:\/[A-Za-z0-9_-]+)*\/?(?:\?[A-Za-z0-9_\-=&%]*)?$/;

export function safeAppPath(value: string | null | undefined): string | null {
  return value && value.length <= 300 && APP_PATH_RE.test(value) ? value : null;
}

/** Текущая страница приложения (путь + query) — если её можно передать как путь возврата. */
export function currentAppPath(): string | null {
  return safeAppPath(window.location.pathname + window.location.search);
}
