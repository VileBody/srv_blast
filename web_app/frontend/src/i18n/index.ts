import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import ru from './locales/ru.json';
import en from './locales/en.json';

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

i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, en: { translation: en } },
  lng: initialLang(),
  fallbackLng: 'ru',
  interpolation: { escapeValue: false } // React сам экранирует
});

export function setLanguage(lng: Lang) {
  i18n.changeLanguage(lng);
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    /* no-op */
  }
}

export default i18n;
