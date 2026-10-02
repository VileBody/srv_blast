import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import ru from './locales/ru.json';

const STORAGE_KEY = 'blast-lang';
export type Lang = 'ru' | 'en';

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'ru' || saved === 'en') return saved;
  } catch {
    /* localStorage недоступен — ru по умолчанию */
  }
  return 'ru';
}

/*
 * В основном куске только ru (язык по умолчанию и запасной). en — отдельный кусок ~60 КБ,
 * качается, только когда он нужен: сохранённый выбор при входе или переключатель языка.
 */
let enLoading: Promise<void> | null = null;
function ensureLang(lng: Lang): Promise<void> {
  if (lng === 'ru' || i18n.hasResourceBundle(lng, 'translation')) return Promise.resolve();
  enLoading ??= import('./locales/en.json')
    .then((m) => { i18n.addResourceBundle('en', 'translation', m.default, true, true); })
    .catch((error: unknown) => { enLoading = null; throw error; });
  return enLoading;
}

const startLang = initialLang();
i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru } },
  partialBundledLanguages: true,
  lng: 'ru',
  fallbackLng: 'ru',
  interpolation: { escapeValue: false } // React сам экранирует
});
/** Готов язык из сохранённого выбора — до этого не рисуем, чтобы не мигнуть русским текстом. */
export const i18nReady: Promise<unknown> =
  startLang === 'ru' ? Promise.resolve() : ensureLang(startLang).then(() => i18n.changeLanguage(startLang)).catch(() => undefined);

export function setLanguage(lng: Lang) {
  void ensureLang(lng).then(() => i18n.changeLanguage(lng)).catch(() => undefined);
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    /* no-op */
  }
}

export default i18n;
