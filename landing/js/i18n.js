(() => {
  'use strict';

  const STORAGE_KEY = 'blast_language';
  const SUPPORTED = new Set(['ru', 'en']);
  const FALLBACK = 'en';

  const pairs = [
    ['Язык', 'Language'], ['Выбрать русский язык', 'Select Russian'], ['Выбрать английский язык', 'Select English'],
    ['Как работает', 'How it works'], ['Примеры', 'Examples'], ['Преимущества', 'Benefits'], ['Попробовать', 'Try it'], ['Меню', 'Menu'],
    ['Сделай трек', 'Make your track'], ['— вирусным', '— go viral'], ['за 60 секунд!', 'in 60 seconds!'],
    ['Co-pilot в продвижении музыки', 'Your music promotion co-pilot'], ['Blast — AI-агент для артистов:', 'Blast is an AI agent for artists:'], ['создаёт контент под трек с нуля', 'it creates track-ready content from scratch'],
    ['Попробуй бесплатно:', 'Try it for free:'], ['Пришли трек в тг-бот', 'Send your track to the Telegram bot'], ['— получи 3 видео!', '— get 3 videos!'], ['Загрузить трек', 'Upload a track'],
    ['Нам доверяют:', 'Trusted by:'], ['артистов', 'artists'], ['лейблов, студий и партнёров', 'labels, studios and partners'], ['лейблов и партнёров', 'labels and partners'],
    ['Создавай видео — без съёмки', 'Create videos — without filming'], ['Как работает генерация?', 'How does generation work?'], ['Загрузи трек.', 'Upload your track.'], ['Настрой видео.', 'Set up your video.'], ['Получи контент.', 'Get your content.'],
    ['Шаг 1', 'Step 1'], ['Шаг 2', 'Step 2'], ['Шаг 3', 'Step 3'], ['Видео А', 'Video A'], ['Видео Б', 'Video B'], ['Просмотры', 'Views'], ['Лайков', 'Likes'], ['Удержание, %', 'Retention, %'],
    ['Примеры роликов:', 'Video examples:'], ['Смотреть', 'Watch'],
    ['Новый виток в «продвижении» музыки', 'A new era in music promotion'], ['Больше никаких', 'No more'], ['препятствий', 'roadblocks'],
    ['До:', 'Before:'], ['1 час', '1 hour'], ['После:', 'After:'], ['3 клика', '3 clicks'], ['ролик', 'video'],
    ['Ты — делаешь музыку,', 'You make music,'], ['Бласт — делает контент', 'Blast makes content'], ['Без камеры, монтажа,', 'No camera, editing,'], ['навыков и напряга', 'special skills or stress'],
    ['Не один ролик —', 'Not just one video —'], ['а система форматов', 'a system of formats'], ['Комбинации из видео', 'Video combinations'], ['повышают виральность', 'increase viral potential'],
    ['Бласт', 'Blast'], ['Авто', 'Automatic'], ['60 секунд', '60 seconds'], ['0₽ старт', 'Start for ₽0'], ['Агентство', 'Agency'], ['Менеджер', 'Manager'], ['14–21 дней', '14–21 days'], ['20–50 тыс ₽', '₽20–50K'],
    ['Контент — без', 'Content without'], ['конских бюджетов', 'massive budgets'], ['В 10 раз дешевле,', '10× more affordable,'], ['в сотни раз быстрее', 'hundreds of times faster'],
    ['Сгенерируй видео — прямо сейчас', 'Generate a video — right now'], ['Затести Бласт', 'Try Blast'], ['на своём треке', 'on your own track'],
    ['Соц. сети', 'Social media'], ['«Импульс Промо» 2026', 'Impulse Promo 2026'], ['Условия использования', 'Terms of Service'], ['Политика конфиденциальности', 'Privacy Policy'], ['Политика cookie', 'Cookie Policy'], ['Согласие на обработку данных', 'Personal Data Consent'], ['Публичная оферта', 'Public Offer'], ['Оферта', 'Offer'], ['Контакты', 'Contacts'], ['Настроить cookie', 'Cookie settings'],
    ['Настройки cookie', 'Cookie settings'], ['Мы используем необходимые cookie для работы сайта. Аналитические и маркетинговые cookie включаются только с вашего согласия.', 'We use necessary cookies for site operation. Analytics and marketing cookies are enabled only with your consent.'], ['Подробнее', 'Learn more'],
    ['Отклонить необязательные', 'Reject optional'], ['Настройки', 'Settings'], ['Принять все', 'Accept all'], ['Закрыть настройки cookie', 'Close cookie settings'],
    ['Выберите, какие необязательные категории можно использовать. Решение можно изменить в footer.', 'Choose which optional categories may be used. You can change your decision in the footer.'],
    ['Необходимые', 'Necessary'], ['Нужны для языка, согласия и базовой работы сайта.', 'Required for language, consent and core site functionality.'], ['Необходимые cookie всегда включены', 'Necessary cookies are always enabled'],
    ['Аналитические', 'Analytics'], ['Помогают понять использование сайта и улучшать его.', 'Help us understand site usage and improve it.'], ['Маркетинговые', 'Marketing'], ['Используются для измерения рекламных кампаний.', 'Used to measure advertising campaigns.'], ['Сохранить выбор', 'Save choices'],
    ['Закрыть', 'Close'], ['На главную', 'Back to home'], ['Рабочий черновик. Требуется финальная проверка профильным юристом.', 'Working draft. Final review by qualified legal counsel is required.'], ['Версия', 'Version'], ['Дата вступления в силу', 'Effective date']
  ];

  const pages = {
    home: {
      ru: ['Blast — AI-агент для музыкантов', 'Blast — AI-агент для артистов: создаёт вирусный контент под трек с нуля за 60 секунд.'],
      en: ['Blast — AI Agent for Musicians', 'Blast is an AI agent for artists that creates track-ready viral content from scratch in 60 seconds.']
    },
    privacy: { ru: ['Политика конфиденциальности — Blast', 'Политика конфиденциальности и обработки персональных данных сервиса Blast.'], en: ['Privacy Policy — Blast', 'Privacy and personal data processing policy for the Blast service.'] },
    terms: { ru: ['Условия использования — Blast', 'Условия использования сервиса Blast.'], en: ['Terms of Service — Blast', 'Terms governing use of the Blast service.'] },
    cookies: { ru: ['Политика cookie — Blast', 'Информация об использовании cookie на сайте Blast.'], en: ['Cookie Policy — Blast', 'Information about the use of cookies on the Blast website.'] },
    offer: { ru: ['Публичная оферта — Blast', 'Рабочий черновик публичной оферты сервиса Blast.'], en: ['Public Offer — Blast', 'Working draft of the Blast public offer.'] },
    contacts: { ru: ['Контакты — Blast', 'Контакты, поддержка и реквизиты сервиса Blast.'], en: ['Contacts — Blast', 'Blast service contacts, support details and legal information.'] },
    consent: { ru: ['Согласие на обработку персональных данных — Blast', 'Рабочий черновик согласия на обработку персональных данных.'], en: ['Personal Data Consent — Blast', 'Working draft of consent to personal data processing.'] }
  };

  const byText = new Map();
  pairs.forEach(([ru, en]) => { byText.set(ru, { ru, en }); byText.set(en, { ru, en }); });

  function storedLanguage() {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return SUPPORTED.has(value) ? value : null;
    } catch (error) {
      console.warn('[landing] language preference unavailable', error);
      return null;
    }
  }

  function initialLanguage() {
    const requested = new URLSearchParams(location.search).get('lang');
    if (SUPPORTED.has(requested)) return requested;
    const stored = storedLanguage();
    if (stored) return stored;
    const browserLanguage = String(navigator.language || '').toLowerCase();
    return browserLanguage === 'ru' || browserLanguage.startsWith('ru-') ? 'ru' : FALLBACK;
  }

  let currentLanguage = initialLanguage();

  function translateValue(value, language) {
    const leading = value.match(/^\s*/)?.[0] || '';
    const trailing = value.match(/\s*$/)?.[0] || '';
    const core = value.trim();
    const pair = byText.get(core);
    return pair ? leading + pair[language] + trailing : value;
  }

  function translateSubtree(root = document.body) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.matches('script, style, noscript')) return NodeFilter.FILTER_REJECT;
        return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => { node.nodeValue = translateValue(node.nodeValue, currentLanguage); });
    root.querySelectorAll?.('[aria-label], [title], [alt], [placeholder]').forEach(element => {
      ['aria-label', 'title', 'alt', 'placeholder'].forEach(attribute => {
        if (element.hasAttribute(attribute)) element.setAttribute(attribute, translateValue(element.getAttribute(attribute), currentLanguage));
      });
    });
  }

  function updateMetadata() {
    const page = document.body?.dataset.page || 'home';
    const meta = pages[page]?.[currentLanguage] || pages.home[currentLanguage];
    document.title = meta[0];
    document.querySelector('meta[name="description"]')?.setAttribute('content', meta[1]);
    document.querySelector('meta[property="og:title"]')?.setAttribute('content', meta[0]);
    document.querySelector('meta[property="og:description"]')?.setAttribute('content', meta[1]);
  }

  function updateLanguageLinks() {
    document.querySelectorAll('a[data-keep-language]').forEach(link => {
      if (!link.dataset.baseHref) link.dataset.baseHref = link.getAttribute('href');
      const url = new URL(link.dataset.baseHref, location.href);
      url.searchParams.set('lang', currentLanguage);
      link.setAttribute('href', url.pathname.split('/').pop() + url.search + url.hash);
    });
  }

  function updateControls() {
    document.querySelectorAll('[data-language]').forEach(button => {
      const active = button.dataset.language === currentLanguage;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }

  function setLanguage(language, options = {}) {
    if (!SUPPORTED.has(language)) throw new Error(`[landing] unsupported language: ${language}`);
    currentLanguage = language;
    document.documentElement.lang = language;
    if (options.persist !== false) {
      try { localStorage.setItem(STORAGE_KEY, language); }
      catch (error) { console.warn('[landing] language preference could not be saved', error); }
    }
    translateSubtree(document.body);
    updateMetadata();
    updateLanguageLinks();
    updateControls();
    document.dispatchEvent(new CustomEvent('blast:languagechange', { detail: { language } }));
  }

  window.BLAST_I18N = { getLanguage: () => currentLanguage, setLanguage, translateSubtree };
  document.documentElement.lang = currentLanguage;
  translateSubtree(document.body);
  updateMetadata();
  updateLanguageLinks();
  updateControls();
  document.querySelectorAll('[data-language]').forEach(button => button.addEventListener('click', () => setLanguage(button.dataset.language)));
})();