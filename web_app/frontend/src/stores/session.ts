import { useWizardStore } from './wizardStore';
import { bindFunnelUser, useFunnelUi } from './funnelUi';

/*
 * Память о подсказках (components/guidance/guideMemory.ts) тут не чистим: она хранится по id
 * аккаунта, поэтому новый аккаунт и так видит онбординг, а вернувшийся — не проходит его заново.
 */
/* Ключи идемпотентности заказов (PricingPage, трипваер) — принадлежат прежнему аккаунту */
const PAYMENT_ATTEMPT_PREFIXES = ['blast:payment-attempt:', 'blast:tripwire-attempt:'];

/*
 * Чей черновик лежит в этом браузере. Ссылка из бота (/go) может войти другим аккаунтом,
 * а спросить /api/me до входа нельзя: без сессии запрос уводит на /login. Поэтому помним
 * последний аккаунт, открывавший приложение (AppShell), и сверяем с вошедшим по ссылке.
 */
const SESSION_USER_KEY = 'blast:session-user';

export function rememberSessionUser(userId: string): void {
  try {
    window.localStorage.setItem(SESSION_USER_KEY, userId);
  } catch {
    /* приватный режим — сверять будет не с чем, черновик считаем своим */
  }
}

export function sessionUser(): string | null {
  try {
    return window.localStorage.getItem(SESSION_USER_KEY);
  } catch {
    return null;
  }
}

/**
 * Стереть на клиенте всё, что принадлежит вошедшему аккаунту: черновик визарда (persist в
 * localStorage) и окна воронки. Зовётся на выходе, удалении аккаунта и при 401 auth_required —
 * иначе следующий аккаунт в том же браузере открывал визард с чужим треком и текстом.
 * Кеш react-query чистит вызывающий (`queryClient.clear()`), при 401 его снимает перезагрузка.
 */
export function clearUserState(): void {
  // сначала сброс в памяти (persist тут же запишет пустой черновик), потом само хранилище
  useWizardStore.getState().reset(null);
  useWizardStore.persist.clearStorage();
  bindFunnelUser(null);
  useFunnelUi.setState({ user: null, open: null, queued: null, later: null, badge: null });
  try {
    window.localStorage.removeItem(SESSION_USER_KEY);
  } catch {
    /* см. rememberSessionUser */
  }
  try {
    for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = window.sessionStorage.key(i);
      if (key && PAYMENT_ATTEMPT_PREFIXES.some((prefix) => key.startsWith(prefix))) window.sessionStorage.removeItem(key);
    }
  } catch {
    /* хранилище недоступно (приватный режим) — там и нечего чистить */
  }
}
