import { useWizardStore } from './wizardStore';
import { bindFunnelUser, useFunnelUi } from './funnelUi';

/*
 * Память о подсказках визарда (components/guidance/guideMemory.ts) — per-браузер, но новому
 * аккаунту онбординг нужен заново. Ключ продублирован: модуль подсказок его не экспортирует.
 */
const GUIDES_KEY = 'blast-guides-seen-v1';
/* Ключи идемпотентности заказов (PricingPage, трипваер) — принадлежат прежнему аккаунту */
const PAYMENT_ATTEMPT_PREFIXES = ['blast:payment-attempt:', 'blast:tripwire-attempt:'];

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
    window.localStorage.removeItem(GUIDES_KEY);
    for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
      const key = window.sessionStorage.key(i);
      if (key && PAYMENT_ATTEMPT_PREFIXES.some((prefix) => key.startsWith(prefix))) window.sessionStorage.removeItem(key);
    }
  } catch {
    /* хранилище недоступно (приватный режим) — там и нечего чистить */
  }
}
