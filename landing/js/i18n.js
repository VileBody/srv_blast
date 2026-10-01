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
    ['Попробуй бесплатно:', 'Try it for free:'], ['Загрузи трек на сайте', 'Upload your track on the website'], ['— получи 5 видео!', '— get 5 videos!'], ['Загрузить трек', 'Upload a track'],
    ['Нам доверяют:', 'Trusted by:'], ['артистов', 'artists'], ['лейблов, студий и партнёров', 'labels, studios and partners'], ['лейблов и партнёров', 'labels and partners'],
    ['Создавай видео — без съёмки', 'Create videos — without filming'], ['Как работает генерация?', 'How does generation work?'], ['Загрузи трек.', 'Upload your track.'], ['Настрой видео.', 'Set up your video.'], ['Получи контент.', 'Get your content.'],
    ['Шаг 1', 'Step 1'], ['Шаг 2', 'Step 2'], ['Шаг 3', 'Step 3'], ['Видео А', 'Video A'], ['Видео Б', 'Video B'], ['Просмотры', 'Views'], ['Лайков', 'Likes'], ['Удержание, %', 'Retention, %'],
    ['Примеры роликов:', 'Video examples:'], ['Смотреть', 'Watch'],
    ['Новый виток в «продвижении» музыки', 'A new era in music promotion'], ['Больше никаких', 'No more'], ['препятствий', 'roadblocks'],
    ['До:', 'Before:'], ['1 час', '1 hour'], ['После:', 'After:'], ['3 клика', '3 clicks'], ['ролик', 'video'],
    ['Ты — делаешь музыку,', 'You make music,'], ['Бласт — делает контент', 'Blast makes content'], ['Без камеры, монтажа,', 'No camera, editing,'], ['навыков и напряга', 'special skills or stress'],
    ['Не один ролик —', 'Not just one video —'], ['а система форматов', 'a system of formats'], ['Комбинации из видео', 'Video combinations'], ['повышают виральность', 'increase viral potential'],
    ['Авто', 'Automatic'], ['60 секунд', '60 seconds'], ['0₽ старт', 'Start for ₽0'], ['Агентство', 'Agency'], ['Менеджер', 'Manager'], ['14–21 дней', '14–21 days'], ['20–50 тыс ₽', '₽20–50K'],
    ['Контент — без', 'Content without'], ['конских бюджетов', 'massive budgets'], ['В 10 раз дешевле,', '10× more affordable,'], ['в сотни раз быстрее', 'hundreds of times faster'],
    ['Сгенерируй видео — прямо сейчас', 'Generate a video — right now'], ['Затести Бласт', 'Try Blast'], ['на своём треке', 'on your own track'],
    ['Соц. сети', 'Social media'], ['«Импульс Промо» 2026', 'Impulse Promo 2026'], ['Условия использования', 'Terms of Service'], ['Политика конфиденциальности', 'Privacy Policy'], ['Политика cookie', 'Cookie Policy'], ['Согласие на обработку данных', 'Personal Data Consent'], ['Публичная оферта', 'Public Offer'], ['Оферта', 'Offer'], ['Контакты', 'Contacts'], ['Настроить cookie', 'Cookie settings'],
    ['Настройки cookie', 'Cookie settings'], ['Мы используем необходимые cookie для работы сайта. Аналитические и маркетинговые cookie включаются только с вашего согласия.', 'We use necessary cookies for site operation. Analytics and marketing cookies are enabled only with your consent.'], ['Подробнее', 'Learn more'],
    ['Отклонить необязательные', 'Reject optional'], ['Настройки', 'Settings'], ['Принять все', 'Accept all'], ['Закрыть настройки cookie', 'Close cookie settings'],
    ['Выберите, какие необязательные категории можно использовать. Решение можно изменить в footer.', 'Choose which optional categories may be used. You can change your decision in the footer.'],
    ['Необходимые', 'Necessary'], ['Нужны для языка, согласия и базовой работы сайта.', 'Required for language, consent and core site functionality.'], ['Необходимые cookie всегда включены', 'Necessary cookies are always enabled'],
    ['Аналитические', 'Analytics'], ['Помогают понять использование сайта и улучшать его.', 'Help us understand site usage and improve it.'], ['Маркетинговые', 'Marketing'], ['Используются для измерения рекламных кампаний.', 'Used to measure advertising campaigns.'], ['Сохранить выбор', 'Save choices'],
    ['Закрыть', 'Close'], ['На главную', 'Back to home'], ['Рабочий черновик. Требуется финальная проверка профильным юристом.', 'Working draft. Final review by qualified legal counsel is required.'], ['Версия', 'Version'], ['Дата вступления в силу', 'Effective date'],
    /* ── новый лендинг и фрагменты веб-приложения (строки из его ru.json / en.json) ── */
    ["#ночнойгород #музыка #новыйтрек", "#nightcity #music #newtrack"],
    ["+ #город", "+ #city"],
    ["+ #новинки", "+ #newmusic"],
    ["+ #ночной", "+ #night"],
    ["+ #рек", "+ #fyp"],
    ["+ #рекомендации", "+ #foryou"],
    ["+ #эффекты", "+ #effects"],
    ["0:41.7 – 0:56.7 · 15,0 с", "0:41.7 – 0:56.7 · 15.0 s"],
    ["1 вар.", "1 var."],
    ["14-21 день", "14-21 days"],
    ["20-50 тыс ₽", "₽20-50K"],
    ["5 видео бесплатно", "5 free videos"],
    ["ALYX - сниппет", "ALYX - snippet"],
    ["Blast делает лирик-видео под твой трек.", "Blast makes lyric videos for your track."],
    ["Blast, на главную", "Blast, home"],
    ["Co-pilot в продвижении музыки", "Your music promotion co-pilot"],
    ["«Импульс Промо» 2026", "Impulse Promo 2026"],
    ["Агентство берёт", "An agency charges"],
    ["Без склейки", "No glue"],
    ["Без стилизации", "No stylization"],
    ["Без хука", "No hook"],
    ["Больше", "More"],
    ["Больше никаких", "No more"],
    ["Бэкстейдж", "Backstage"],
    ["Вайб — Неон", "Vibe — Neon"],
    ["Вайб — Ночной город", "Vibe — Night city"],
    ["Вариант", "Variant"],
    ["Вводные", "Inputs"],
    ["Вертикаль для TikTok и Reels, квадрат и 4:3 для ленты. Нажми на ролик, чтобы включить звук.", "Vertical for TikTok and Reels, square and 4:3 for the feed. Tap a video to turn the sound on."],
    ["Видео №1", "Video #1"],
    ["Видео №2", "Video #2"],
    ["Видео №3", "Video #3"],
    ["Видео №4", "Video #4"],
    ["Визуальный язык", "An artist’s"],
    ["Все", "Everyone"],
    ["Вставлять кусок твоего ролика в свой", "Use a clip of your video in theirs"],
    ["Выбери, кто увидит ролик", "Choose who can view the video"],
    ["Выбираешь стиль, субтитры и хук", "Pick the style, subtitles and hook"],
    ["Выкладка", "Publishing"],
    ["Выложить в TikTok", "Post to TikTok"],
    ["Готово", "Done"],
    ["Друзья", "Friends"],
    ["Дуэты", "Duet"],
    ["Загружаешь трек, текст, тайминг", "Upload the track, lyrics and timing"],
    ["Загрузи трек", "Upload a track"],
    ["Загрузи трек на сайте и получи 5 видео бесплатно.", "Upload a track on the website and get 5 free videos."],
    ["Загрузить трек", "Upload a track"],
    ["Закат", "Sunset"],
    ["Заменить", "Replace"],
    ["Затести Blast", "Try Blast"],
    ["Звук", "Sound"],
    ["Зум волны", "Waveform zoom"],
    ["Играть", "Play"],
    ["Играть отрывок", "Play clip"],
    ["Идёт рендер", "Rendering"],
    ["Как работает", "How it works"],
    ["Как это работает", "How it works"],
    ["Комбинации из видео повышают виральность.", "Combining videos boosts virality."],
    ["Комментарии", "Comment"],
    ["Конец", "End"],
    ["Конец отрывка", "Segment end"],
    ["Контакты", "Contacts"],
    ["Кто увидит", "Who can view"],
    ["Листать влево", "Scroll left"],
    ["Листать вправо", "Scroll right"],
    ["Любой", "Any"],
    ["Масштаб дорожки", "Timeline zoom"],
    ["Меньше", "Less"],
    ["Молния", "Lightning"],
    ["Молния · Без склейки · Без стилизации", "Lightning · No glue · No stylization"],
    ["На 2 видео", "For 2 videos"],
    ["На 2 видео с футажами", "For 2 videos with footage"],
    ["Нам доверяют", "Trusted by"],
    ["Настроить cookie", "Cookie settings"],
    ["Настрой", "Set it up"],
    ["Настройка", "Setup"],
    ["Настройка варианта", "Variant settings"],
    ["Начало", "Start"],
    ["Начало отрывка", "Segment start"],
    ["Не один ролик, а система форматов", "Not one video, a system of formats"],
    ["Недоступно для этого аккаунта", "Not available for this account"],
    ["Неон", "Neon"],
    ["Ночной город", "Night city"],
    ["О чём этот ролик?", "What is this video about?"],
    ["Обложка", "Cover"],
    ["Объект", "Object"],
    ["Один трек.", "One track."],
    ["Описание", "Caption"],
    ["Осталось 9 минут", "9 minutes left"],
    ["Отдалить", "Zoom out"],
    ["Отрывок до 30 секунд и строки текста", "A clip up to 30 seconds and its lyrics"],
    ["Подписчики", "Followers"],
    ["Подсказки хештегов", "Hashtag suggestions"],
    ["Пока", "While"],
    ["Политика cookie", "Cookie Policy"],
    ["Политика конфиденциальности", "Privacy Policy"],
    ["Постишь в TikTok и анализируешь", "Post to TikTok and track the results"],
    ["Преимущества", "Benefits"],
    ["Приблизить", "Zoom in"],
    ["Примеры", "Examples"],
    ["Проверка субтитров", "Subtitle fitting"],
    ["Прогрев", "Warm-up"],
    ["Прогресс: 1/4 видео", "Progress: 1/4 videos"],
    ["Прослушать отрывок", "Play fragment"],
    ["Публичная оферта", "Public Offer"],
    ["Пул", "Pool"],
    ["Разделы", "Sections"],
    ["Разрешить зрителям", "Allow viewers to"],
    ["Раньше это была съёмка, монтаж и субтитры вручную. Теперь три шага.", "It used to mean filming, editing and hand-made subtitles. Now it takes three steps."],
    ["Ролик за 60 секунд", "A video in 60 seconds"],
    ["Ролик и обложка", "Video and cover"],
    ["Ролики придут в проект и в Telegram", "Videos land in your project and in Telegram"],
    ["Ролики, сделанные в Blast", "Videos made with Blast"],
    ["Сбросить правки", "Reset edits"],
    ["Свой вариант", "Custom"],
    ["Сгенерируй", "Generate"],
    ["Сделай трек", "Make your track"],
    ["Скачать", "Download"],
    ["Склейка", "Transition"],
    ["Слабо легли на трек: «не», «нас». Проверь, так ли поётся в тексте отрывка, и тайминги окна — иначе субтитры здесь могут разъехаться.", "Poorly aligned: «never», «us». Check the fragment text and the clip window — subtitles may drift here otherwise."],
    ["Слабые слова", "Weak words"],
    ["Слушать трек", "Play track"],
    ["Снимать видео рядом с твоим", "Record a video side by side with yours"],
    ["Согласие на обработку данных", "Personal Data Consent"],
    ["Старт бесплатно", "Start for free"],
    ["Стиль", "Style"],
    ["Стичи", "Stitch"],
    ["Субтитры", "Subtitles"],
    ["Субтитры в такт, футаж по смыслу текста, хук на дропе.", "Subtitles on the beat, footage that fits the lyrics, a hook on the drop."],
    ["Текст", "Text"],
    ["Тип фона", "Background type"],
    ["Только я", "Only me"],
    ["Трек", "Track"],
    ["Ты делаешь музыку, Blast делает контент", "You make music, Blast makes content"],
    ["Ты делаешь музыку, Blast делает контент. Без камеры, монтажа, навыков и напряга.", "You make music, Blast makes content. No camera, no editing, no skills, no stress."],
    ["Тяни слово, чтобы сдвинуть тайминг, края — чтобы изменить длительность. Клик выбирает слово, двойной клик или кнопка «Фокус» делает его акцентом в субтитрах. При переносе старт слова прилипает к биту.", "Drag a word to shift its timing, drag its edges to change duration. Click selects a word; double-click or the Focus button makes it the accent in subtitles. While dragging, the word start snaps to the beat."],
    ["У агентства", "An agency takes"],
    ["Удалить вариант", "Remove variant"],
    ["Улица", "Street"],
    ["Условия использования", "Terms of Service"],
    ["Фокус", "Make focus word"],
    ["Фон", "Background"],
    ["Фон, субтитры, FX и сколько роликов", "Background, subtitles, FX and how many videos"],
    ["Фото", "Photo"],
    ["Футажей и фото на 2 видео", "Footage and photos for 2 videos"],
    ["Футажи", "Footage"],
    ["Цвет", "Color"],
    ["Что такое FX «Без хука»", "What is the «No hook» FX"],
    ["Что такое FX «Объект»", "What is the «Object» FX"],
    ["Что такое FX «Прогрев»", "What is the «Warm-up» FX"],
    ["Что такое FX «Эффекты»", "What is the «Effects» FX"],
    ["Шаги", "Steps"],
    ["Этапы генерации", "Generation stages"],
    ["Эффект", "Effect"],
    ["Эффекты", "Effects"],
    ["Я", "I"],
    ["Язык", "Language"],
    ["артиста", "visual language"],
    ["в очереди", "queued"],
    ["ведёт", "leads"],
    ["вирусным", "go viral"],
    ["вперёд", "on"],
    ["город", "city"],
    ["за 60 секунд", "in 60 seconds"],
    ["за пару кликов", "in a couple of clicks"],
    ["знаю", "know"],
    ["и менеджер", "and a manager"],
    ["копия текущего", "copy of the current one"],
    ["м:сс", "m:ss"],
    ["музыка", "the music"],
    ["на первом треке", "on your first track"],
    ["на своём", "on your own"],
    ["нас", "us"],
    ["не", "never"],
    ["препятствий", "roadblocks"],
    ["проведи по цифрам", "swipe over the numbers"],
    ["треке", "track"],
    ["уснёт", "sleeps"],
    ["формат.", "format."],
    ["этот", "this"]
  ];

  const pages = {
    home: {
      ru: ['Blast — AI-агент для музыкантов', 'Blast — AI-агент для артистов: создаёт вирусный контент под трек с нуля за 60 секунд.'],
      en: ['Blast — AI agent for musicians', 'Blast makes lyric videos for your track: subtitles on the beat, footage that fits the lyrics, a hook on the drop.']
    },
    privacy: { ru: ['Политика конфиденциальности — Blast', 'Политика конфиденциальности и обработки персональных данных сервиса Blast.'], en: ['Privacy Policy — Blast', 'Privacy and personal data processing policy for the Blast service.'] },
    terms: { ru: ['Условия использования — Blast', 'Условия использования сервиса Blast.'], en: ['Terms of Service — Blast', 'Terms governing use of the Blast service.'] },
    cookies: { ru: ['Политика cookie — Blast', 'Информация об использовании cookie на сайте Blast.'], en: ['Cookie Policy — Blast', 'Information about the use of cookies on the Blast website.'] },
    offer: { ru: ['Публичная оферта — Blast', 'Публичная оферта сервиса Blast: тарифы, оплата, возврат и права на материалы.'], en: ['Public Offer — Blast', 'The Blast public offer: plans, payment, refunds and rights to materials.'] },
    contacts: { ru: ['Контакты — Blast', 'Контакты, поддержка и реквизиты сервиса Blast.'], en: ['Contacts — Blast', 'Blast service contacts, support details and legal information.'] },
    consent: { ru: ['Согласие на обработку персональных данных — Blast', 'Согласие на обработку персональных данных сервиса Blast.'], en: ['Personal Data Consent — Blast', 'Consent to personal data processing for the Blast service.'] }
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

  const originals = new WeakMap();
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
    nodes.forEach(node => { if (!originals.has(node)) originals.set(node, node.nodeValue); node.nodeValue = translateValue(originals.get(node), currentLanguage); });
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