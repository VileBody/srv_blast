/**
 * Тексты юридических документов — политика конфиденциальности и оферта.
 *
 * Живут в коде, а не в i18n-локалях, специально: это длинные связные документы,
 * их правят целиком и сверяют глазами, а не по ключам. В локалях лежит только
 * «обвязка» страницы (кнопка назад, подпись даты и т.п.).
 *
 * ВАЖНО: обе страницы должны быть публично доступны без входа — их URL
 * прикрепляются в consent screen Google и в кабинет разработчика TikTok.
 *
 * Реквизиты подставляются из окружения (`frontend/.env`), см. LEGAL_ENTITY ниже.
 * Пока они не заданы, страница показывает предупреждение — документ с
 * незаполненными реквизитами модерацию не пройдёт.
 */

export type LegalBlock = string | string[]; // строка — абзац, массив — список
export type LegalSection = { title: string; body: LegalBlock[] };
export type LegalDoc = { title: string; intro: string; sections: LegalSection[] };
export type LegalKind = 'policy' | 'offer';

/** Дата последней редакции документов. Менять при каждой правке текста. */
export const LEGAL_UPDATED = '2026-07-30';

/**
 * Реквизиты оператора — те же, что опубликованы на лендинге
 * (`srv_blast/landing/contacts.html`, `offer.html`). Это публичные данные, поэтому лежат
 * в коде: два источника с расходящимися реквизитами — прямой путь к отказу модерации.
 * Через `frontend/.env` (`VITE_LEGAL_*`) их можно переопределить, не трогая код.
 */
export const LEGAL_ENTITY = {
  name: (import.meta.env.VITE_LEGAL_ENTITY as string | undefined) ?? 'ИП Чернов Никита Романович',
  inn: (import.meta.env.VITE_LEGAL_INN as string | undefined) ?? '623013205426',
  ogrnip: (import.meta.env.VITE_LEGAL_OGRNIP as string | undefined) ?? '324620000005644',
  address: (import.meta.env.VITE_LEGAL_ADDRESS as string | undefined)
    ?? '390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60',
  email: (import.meta.env.VITE_LEGAL_EMAIL as string | undefined) ?? 'support@blast808.com',
  phone: (import.meta.env.VITE_LEGAL_PHONE as string | undefined) ?? '+7 (910) 572-49-67'
};

/** Платёжный партнёр — с лендинга: карты и приём платежей идут через него. */
export const PAYMENT_PARTNER = 'АО «Т-Банк» (tbank.ru)';

/** Незаполненные реквизиты — страница про них честно предупреждает. */
export function missingRequisites(): string[] {
  return Object.entries(LEGAL_ENTITY)
    .filter(([, value]) => !String(value).trim())
    .map(([key]) => key);
}

const PLACEHOLDER = '[не заполнено]';

/** Подстановка реквизитов, платёжного партнёра и домена в текст документа. */
export function fillPlaceholders(text: string): string {
  const site = typeof location === 'undefined' ? 'blast' : location.host;
  return text
    .replace(/\{\{entity\}\}/g, LEGAL_ENTITY.name || PLACEHOLDER)
    .replace(/\{\{inn\}\}/g, LEGAL_ENTITY.inn || PLACEHOLDER)
    .replace(/\{\{ogrnip\}\}/g, LEGAL_ENTITY.ogrnip || PLACEHOLDER)
    .replace(/\{\{address\}\}/g, LEGAL_ENTITY.address || PLACEHOLDER)
    .replace(/\{\{email\}\}/g, LEGAL_ENTITY.email || PLACEHOLDER)
    .replace(/\{\{phone\}\}/g, LEGAL_ENTITY.phone || PLACEHOLDER)
    .replace(/\{\{bank\}\}/g, PAYMENT_PARTNER)
    .replace(/\{\{site\}\}/g, site);
}

// ---------------------------------------------------------------------------
// Политика конфиденциальности — RU
// ---------------------------------------------------------------------------

const POLICY_RU: LegalDoc = {
  title: 'Политика конфиденциальности',
  intro:
    'Документ объясняет, какие данные сервис Blast собирает, зачем они нужны, кому передаются ' +
    'и как их удалить. Написан человеческим языком: если что-то осталось непонятным — спросите, ' +
    'мы ответим тем же языком.',
  sections: [
    {
      title: '1. Кто обрабатывает данные',
      body: [
        'Оператор персональных данных — {{entity}}, ИНН {{inn}}, ОГРНИП {{ogrnip}}, ' +
          'адрес: {{address}}. Далее — «мы», «сервис», «Blast». Сервис расположен на домене {{site}}.',
        'По любым вопросам об обработке данных пишите на {{email}} или звоните {{phone}} — ' +
          'это же адрес для отзыва согласия и требования удалить данные.',
        'Политика составлена в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ ' +
          '«О персональных данных». Для пользователей из ЕЭЗ и Великобритании применяются также ' +
          'правила раздела 8.'
      ]
    },
    {
      title: '2. Какие данные мы собираем',
      body: [
        'Мы собираем только то, без чего сервис не работает. Отдельной рекламной или ' +
          'поведенческой слежки в Blast нет.',
        'При входе через Telegram:',
        [
          'идентификатор чата Telegram (chat_id) — это и есть ваша учётная запись у нас;',
          'имя пользователя Telegram (@username), если оно указано;',
          'имя и фамилия, которые вы вписали в форму регистрации.'
        ],
        'При входе через Google:',
        [
          'адрес электронной почты и подтверждение того, что она принадлежит вам;',
          'имя и фамилия из профиля Google;',
          'ссылка на фотографию профиля.'
        ],
        'Мы запрашиваем у Google минимальный набор доступов (openid, email, profile). ' +
          'К вашей почте, диску, контактам и другим сервисам Google у нас доступа нет.',
        'В профиле сервиса:',
        [
          'ник артиста и загруженный аватар;',
          'выбранный тариф, история оплат и остаток лимитов.'
        ],
        'Материалы, которые вы загружаете для генерации: аудиофайл трека, текст песни, ' +
          'изображения (обложки, фото), а также настройки генерации — выбранный футаж, стиль ' +
          'субтитров, эффекты. Готовые видео мы храним, чтобы вы могли их скачать и опубликовать.',
        'При подключении TikTok:',
        [
          'идентификатор аккаунта TikTok (open_id) и отображаемое имя;',
          'ссылка на аватар;',
          'токены доступа TikTok — они хранятся в зашифрованном виде и никогда не отдаются ' +
            'в браузер;',
          'статистика опубликованных через сервис роликов: просмотры, лайки, комментарии, ' +
            'репосты, дата публикации.'
        ],
        'Технические данные: IP-адрес, дата и время запросов, тип браузера, идентификатор ' +
          'сессии, служебные записи о действиях в интерфейсе (какой шаг мастера открыт, ' +
          'какая генерация запущена) — они нужны для защиты от подбора, диагностики сбоев ' +
          'и продуктовой статистики в обезличенном виде.',
        'Данные банковской карты мы не собираем, не видим и не храним. При оплате вы ' +
          'переходите на защищённую платёжную страницу {{bank}}: номер карты, срок действия и ' +
          'CVV вводятся на стороне банка. Платёжный шлюз соответствует стандарту PCI DSS ' +
          'Level 1. К нам приходит только идентификатор заказа и его статус.'
      ]
    },
    {
      title: '3. Зачем нам эти данные',
      body: [
        [
          'дать вам войти в аккаунт и узнать вас при следующем входе;',
          'сгенерировать видео из вашего трека и текста — это основная услуга;',
          'опубликовать готовый ролик в вашем аккаунте TikTok по вашей команде и показать ' +
            'его статистику;',
          'считать лимиты тарифа и проводить оплату;',
          'присылать в Telegram уведомление о том, что генерация закончилась;',
          'отвечать на обращения в поддержку;',
          'защищать сервис: ограничивать частоту запросов, выявлять повторное использование ' +
            'одного и того же аккаунта TikTok для получения бесплатных лимитов;',
          'улучшать продукт по обезличенной статистике (сколько людей дошло до конца мастера, ' +
            'где чаще возвращаются назад).'
        ],
        'Правовые основания: исполнение договора с вами (оферта), ваше согласие — там, где ' +
          'вы его даёте отдельным действием, и наш законный интерес в защите сервиса от ' +
          'злоупотреблений.'
      ]
    },
    {
      title: '4. Cookies и аналогичные технологии',
      body: [
        'Мы используем только технически необходимые cookies:',
        [
          'cookie сессии — чтобы вы оставались в аккаунте между страницами;',
          'cookie защиты от подделки запросов (CSRF-токен);',
          'локальное хранилище браузера — выбранный язык интерфейса и черновик мастера ' +
            'генерации, чтобы не терять его при обновлении страницы.'
        ],
        'Рекламных и трекинговых cookies сторонних сетей мы не ставим. Отключить необходимые ' +
          'cookies нельзя — без них вход не работает.'
      ]
    },
    {
      title: '5. Кому мы передаём данные',
      body: [
        'Мы не продаём данные и не передаём их для рекламы. Передача происходит только тем, ' +
          'без кого услуга невозможна:',
        [
          'Google LLC — при входе через Google (подтверждение почты). Политика: ' +
            'policies.google.com/privacy;',
          'TikTok — при подключении аккаунта и публикации ролика. Мы отправляем видео, ' +
            'описание и настройки приватности, которые вы выбрали, и получаем статистику. ' +
            'Политика: tiktok.com/legal/privacy-policy;',
          'Telegram — доставка уведомлений и подтверждение входа через бота;',
          'хостинг и объектное хранилище — размещение сервиса и файлов (серверы на территории ' +
            'Российской Федерации);',
          '{{bank}} — приём и обработка платежей (данные карты обрабатывает банк, не мы);',
          'государственные органы — только по законному и мотивированному запросу.'
        ],
        'Публикация ролика в TikTok — это ваше действие: без нажатия кнопки «Выложить» ' +
          'мы ничего никуда не отправляем.'
      ]
    },
    {
      title: '6. Где и сколько хранятся данные',
      body: [
        'Данные пользователей хранятся на серверах на территории Российской Федерации.',
        [
          'аккаунт, проекты и настройки — пока существует ваш аккаунт;',
          'загруженные треки, изображения и готовые видео — пока вы не удалите проект; ' +
            'при удалении проекта файлы удаляются вместе с ним;',
          'токены доступа TikTok — до отключения аккаунта TikTok в профиле, после чего ' +
            'удаляются немедленно;',
          'технические журналы и записи о действиях в интерфейсе — до 12 месяцев;',
          'сведения об оплатах — в течение срока, установленного налоговым и бухгалтерским ' +
            'законодательством.'
        ],
        'Сведения об аккаунтах TikTok, использованных для получения бесплатных лимитов, ' +
          'сохраняются и после удаления аккаунта — иначе правило «один аккаунт TikTok — ' +
          'один бесплатный лимит» обойти было бы одним нажатием кнопки. Хранится только ' +
          'идентификатор аккаунта, без содержимого профиля.'
      ]
    },
    {
      title: '7. Ваши права',
      body: [
        [
          'узнать, какие ваши данные у нас есть, и получить их копию;',
          'исправить неточные данные — имя, ник и аватар меняются прямо в профиле;',
          'отключить Google или TikTok от аккаунта в любой момент — кнопки в профиле;',
          'удалить аккаунт вместе с проектами и файлами;',
          'отозвать согласие на обработку;',
          'подать жалобу в Роскомнадзор, если считаете, что мы нарушаем закон.'
        ],
        'Запрос на любое из этих действий отправьте на {{email}} с адреса или аккаунта, ' +
          'привязанного к профилю. Мы отвечаем в течение 30 дней, обычно быстрее.',
        'Отзыв согласия и удаление аккаунта означают, что услуга больше не может быть оказана: ' +
          'сгенерированные видео и проекты будут удалены. Скачайте нужное заранее.'
      ]
    },
    {
      title: '8. Пользователи из ЕЭЗ и Великобритании',
      body: [
        'Если вы находитесь в Европейской экономической зоне или Великобритании, к обработке ' +
          'применяется GDPR (UK GDPR). Правовые основания перечислены в разделе 3: исполнение ' +
          'договора (ст. 6(1)(b)), согласие (ст. 6(1)(a)) и законный интерес (ст. 6(1)(f)).',
        'Помимо прав из раздела 7 вы вправе требовать переноса данных в машиночитаемом виде, ' +
          'ограничения обработки и возражать против обработки по законному интересу. ' +
          'Жалобу можно подать в надзорный орган по месту жительства.',
        'Данные обрабатываются на серверах в Российской Федерации, то есть за пределами ЕЭЗ. ' +
          'Передача происходит на основании вашего явного согласия при регистрации и в объёме, ' +
          'необходимом для исполнения договора с вами.'
      ]
    },
    {
      title: '9. Как мы защищаем данные',
      body: [
        [
          'весь обмен с сервисом идёт по HTTPS;',
          'токены доступа TikTok хранятся зашифрованными, ключ шифрования лежит отдельно ' +
            'от данных;',
          'пароля у сервиса нет вообще — вход подтверждается Telegram или Google, поэтому ' +
            'украсть у нас пароль невозможно;',
          'запросы к API ограничены по частоте, загружаемые файлы проверяются по содержимому, ' +
            'а не по расширению;',
          'доступ к продакшн-данным есть у ограниченного круга лиц и только для эксплуатации.'
        ],
        'Абсолютной защиты не существует. Если произойдёт утечка, затрагивающая ваши данные, ' +
          'мы сообщим об этом и уведомим уполномоченный орган в установленные законом сроки.'
      ]
    },
    {
      title: '10. Несовершеннолетние',
      body: [
        'Сервис не предназначен для лиц младше 14 лет. Платные тарифы доступны с 18 лет либо ' +
          'с согласия законного представителя. Если вы узнали, что ребёнок пользуется сервисом ' +
          'без такого согласия, напишите на {{email}} — мы удалим аккаунт и данные.'
      ]
    },
    {
      title: '11. Изменения политики',
      body: [
        'Мы можем менять политику — например, когда появится новая интеграция. Актуальная ' +
          'редакция всегда лежит по этому адресу, дата редакции указана в начале страницы. ' +
          'О существенных изменениях мы предупредим в интерфейсе или в Telegram-боте до того, ' +
          'как они вступят в силу.'
      ]
    }
  ]
};

// ---------------------------------------------------------------------------
// Оферта — RU
// ---------------------------------------------------------------------------

const OFFER_RU: LegalDoc = {
  title: 'Пользовательское соглашение (публичная оферта)',
  intro:
    'Это условия, на которых вы пользуетесь сервисом Blast. Начав пользоваться сервисом, ' +
    'вы соглашаетесь с ними целиком. Если что-то не устраивает — не начинайте, ' +
    'а лучше напишите нам: часть вопросов решается быстрее, чем кажется.',
  sections: [
    {
      title: '1. Термины',
      body: [
        [
          '«Исполнитель», «мы» — {{entity}}, ИНН {{inn}}, ОГРНИП {{ogrnip}}, адрес: {{address}};',
          '«Сервис», «Blast» — веб-приложение по адресу {{site}} и связанный Telegram-бот;',
          '«Пользователь», «вы» — лицо, зарегистрировавшееся в Сервисе;',
          '«Материалы» — аудиозапись, текст песни, изображения и другие файлы, которые вы ' +
            'загружаете;',
          '«Результат» — видеоролик, сгенерированный Сервисом из ваших Материалов;',
          '«Аккаунт» — ваша учётная запись в Сервисе;',
          '«Подписка» — платный доступ к Сервису на срок.'
        ]
      ]
    },
    {
      title: '2. Предмет соглашения',
      body: [
        'Мы предоставляем вам доступ к Сервису, который автоматически собирает из ваших ' +
          'Материалов вертикальные видеоролики с субтитрами и эффектами, и, по вашей команде, ' +
          'публикует их в подключённом аккаунте TikTok.',
        'Сервис — программное средство. Мы отвечаем за то, что он работает и выдаёт ролики; ' +
          'мы не отвечаем за то, сколько просмотров эти ролики соберут. Никакие цифры охватов, ' +
          'подписчиков или дохода мы не гарантируем и не обещаем.'
      ]
    },
    {
      title: '3. Как принимается соглашение',
      body: [
        'Соглашение считается принятым (акцепт публичной оферты по ст. 437, 438 ГК РФ) ' +
          'с момента, когда вы завершили регистрацию — подтвердили вход через Telegram или ' +
          'Google. Отдельно подписывать документ не нужно.',
        'Оплата тарифа означает, что вы согласны и с условиями оплаты из раздела 6.'
      ]
    },
    {
      title: '4. Аккаунт',
      body: [
        'Пароля в Сервисе нет: вход подтверждается вашим аккаунтом Telegram или Google. ' +
          'Поэтому доступ к Сервису равен доступу к этим аккаунтам — берегите их.',
        [
          'один человек — один Аккаунт;',
          'передавать доступ третьим лицам нельзя;',
          'данные при регистрации должны быть настоящими: имя из профиля попадает в документы ' +
            'об оплате;',
          'если вы потеряли доступ к Telegram или Google, напишите на {{email}} — восстановим ' +
            'по признакам владения аккаунтом.'
        ]
      ]
    },
    {
      title: '5. Бесплатный доступ и лимиты',
      body: [
        'Новый Пользователь получает ограниченное число бесплатных роликов, чтобы попробовать ' +
          'Сервис. Точное число указано в интерфейсе на странице тарифов и может меняться.',
        'Расширенный лимит в рамках одного трека открывается подключением аккаунта TikTok — ' +
          'так мы проверяем, что человек реальный, и одновременно даём вам возможность ' +
          'публиковать ролики в один клик.',
        'Каждый аккаунт TikTok даёт бесплатный лимит только один раз. Если подключаемый ' +
          'аккаунт TikTok уже использовался в Сервисе — в том числе на другом Аккаунте, ' +
          'в том числе удалённом — это считается попыткой получить бесплатный доступ повторно ' +
          'и влечёт последствия из раздела 9.'
      ]
    },
    {
      title: '6. Тарифы, оплата и возврат',
      body: [
        'Актуальные составы и цены — на странице тарифов Сервиса. На момент этой редакции:',
        [
          '«Blast» — 1 990 ₽ в месяц, подписка: 100 роликов, до 4 треков, каждый третий месяц ' +
            'без лимита роликов;',
          '«Glow» — 7 990 ₽, разовая покупка: 400 роликов, до 10 треков, шаблон CapCut ' +
            'под ваш трек;',
          '«Impulse» — 29 990 ₽, разовая покупка на год: ролики без лимита, до 24 треков, ' +
            'персональный менеджмент релиза;',
          'бесплатный доступ — ограниченное число роликов на один трек (точное число указано ' +
            'на странице тарифов), расширяется подключением аккаунта TikTok.'
        ],
        'Цена может меняться, но не для уже оплаченного периода.',
        'Оплата проводится дистанционно банковскими картами Visa, Mastercard, МИР и через ' +
          'сервис T-Pay. Приём платежей осуществляет {{bank}}. Данные карты вводятся на ' +
          'защищённой странице банка, к нам они не попадают. Момент оплаты — зачисление денег ' +
          'на наш счёт; доступ по тарифу открывается автоматически после подтверждения оплаты.',
        'Подписка продлевается на следующий период, если вы её не отменили. Отменить можно ' +
          'в любой момент в профиле: доступ сохраняется до конца оплаченного периода, ' +
          'следующее списание не происходит.',
        'Возврат. До первой генерации по оплаченному тарифу деньги возвращаются полностью. ' +
          'После — услуга считается оказанной по каждому уже сгенерированному ролику, поэтому ' +
          'возвращается оплаченный и неиспользованный остаток: неизрасходованные ролики и ' +
          'неистёкший период за вычетом стоимости выполненных генераций (ст. 32 Закона РФ ' +
          '«О защите прав потребителей»). Заявление отправьте на {{email}}, срок рассмотрения — ' +
          '10 рабочих дней.',
        'Если генерация не удалась по нашей вине, потраченный лимит возвращается автоматически, ' +
          'а при невозможности выполнить услугу — возвращаются деньги за неё.'
      ]
    },
    {
      title: '7. Права на Материалы и Результат',
      body: [
        'Материалы остаются вашими. Загружая их, вы даёте нам ограниченную лицензию: ' +
          'хранить, обрабатывать и передавать их в TikTok — ровно в объёме, необходимом ' +
          'для оказания услуги, и только по вашей команде.',
        'Вы подтверждаете, что имеете права на всё, что загружаете: на запись, на текст, ' +
          'на изображения и на использование чужих голосов и лиц, если они там есть. ' +
          'Претензии правообладателей по вашим Материалам разбираете вы.',
        'Исключительные права на Результат — ваши. Публиковать, монетизировать и использовать ' +
          'ролики можно как угодно, отдельного разрешения от нас не нужно.',
        'Мы не используем ваши Материалы и Результаты в рекламе и в примерах работ без ' +
          'вашего отдельного письменного согласия.',
        'Права на сам Сервис, интерфейс, шаблоны и библиотеку футажа принадлежат нам. ' +
          'Футаж, который Сервис подставляет в ролики, лицензирован нами для использования ' +
          'внутри Результатов; выгружать его отдельно и использовать вне Сервиса нельзя.'
      ]
    },
    {
      title: '8. Публикация в TikTok',
      body: [
        'Подключая TikTok, вы разрешаете Сервису публиковать ролики от вашего имени. ' +
          'Публикация каждый раз запускается только вашим действием — мы ничего не выкладываем ' +
          'по собственной инициативе.',
        'Опубликованный ролик подчиняется правилам TikTok, а не нашим. Блокировки, снятие ' +
          'ролика и ограничения показов — решение TikTok, влиять на них мы не можем.',
        'Отключить TikTok от Аккаунта можно в любой момент в профиле; токены доступа при этом ' +
          'удаляются, уже опубликованные ролики остаются у вас в TikTok.'
      ]
    },
    {
      title: '9. Запрещённое использование и блокировка',
      body: [
        'Нельзя:',
        [
          'загружать Материалы, права на которые вам не принадлежат;',
          'создавать ролики с чужим голосом или лицом без согласия человека;',
          'создавать материалы, запрещённые законом: экстремистские, порнографические, ' +
            'призывающие к насилию, вводящие в заблуждение о личности другого человека;',
          'заводить несколько Аккаунтов на одного человека, чтобы получать бесплатные лимиты ' +
            'повторно;',
          'подключать аккаунт TikTok, который уже использовался в Сервисе на другом Аккаунте;',
          'обходить лимиты и защиты, автоматизировать интерфейс, нагружать Сервис запросами, ' +
            'исследовать его на уязвимости без нашего разрешения;',
          'перепродавать доступ к Сервису.'
        ],
        'Последствия. При нарушении мы вправе приостановить или заблокировать доступ. ' +
          'Повторное использование аккаунта TikTok — отдельный случай: блокируются ВСЕ Аккаунты ' +
          'этого человека, включая те, где нарушения не было, потому что нарушение состоит ' +
          'именно в наличии нескольких Аккаунтов у одного человека.',
        'Блокировка за попытку получить бесплатный доступ повторно не влечёт возврата денег ' +
          'за бесплатные лимиты — их и не было. Оплаченный и неиспользованный период ' +
          'возвращается по правилам раздела 6.',
        'Считаете блокировку ошибкой — напишите на {{email}}. Мы разберёмся и, если ошиблись, ' +
          'снимем её.'
      ]
    },
    {
      title: '10. Работа Сервиса и ответственность',
      body: [
        'Сервис предоставляется «как есть». Мы стараемся, чтобы он работал круглосуточно, ' +
          'но не обещаем этого: возможны технические работы, сбои у TikTok, Telegram, Google, ' +
          'хостинга и платёжного провайдера.',
        'Мы не отвечаем за: результаты продвижения и охваты; решения TikTok по вашим роликам; ' +
          'ваши убытки от использования Результатов; недоступность сторонних сервисов.',
        'Наша ответственность в любом случае ограничена суммой, которую вы заплатили за ' +
          'последний оплаченный период.',
        'Мы не отвечаем за содержание ваших Материалов и роликов — за него отвечаете вы.'
      ]
    },
    {
      title: '11. Персональные данные',
      body: [
        'Как мы обрабатываем персональные данные, описано в Политике конфиденциальности — ' +
          'она часть этого соглашения. Регистрируясь, вы даёте согласие на обработку данных ' +
          'на условиях Политики.'
      ]
    },
    {
      title: '12. Изменение условий',
      body: [
        'Мы можем менять соглашение. Новая редакция публикуется по этому адресу и вступает ' +
          'в силу через 5 календарных дней после публикации, если в ней не указан более ' +
          'поздний срок. О существенных изменениях предупреждаем в интерфейсе или в ' +
          'Telegram-боте. Продолжая пользоваться Сервисом после вступления в силу, вы ' +
          'соглашаетесь с новой редакцией.',
        'На уже оплаченный период изменения цены не распространяются.'
      ]
    },
    {
      title: '13. Споры и применимое право',
      body: [
        'Применяется право Российской Федерации. Спор сначала решаем перепиской: напишите ' +
          'на {{email}}, срок ответа на претензию — 30 календарных дней. Если договориться ' +
          'не удалось, спор рассматривается судом по месту нахождения Исполнителя, ' +
          'а для потребителей — по правилам, установленным законодательством о защите прав ' +
          'потребителей.'
      ]
    },
    {
      title: '14. Реквизиты',
      body: [
        [
          'Исполнитель: {{entity}}',
          'ИНН: {{inn}}',
          'ОГРНИП: {{ogrnip}}',
          'Адрес: {{address}}',
          'Электронная почта: {{email}}',
          'Телефон: {{phone}}',
          'Приём платежей: {{bank}}',
          'Сайт: {{site}}'
        ]
      ]
    }
  ]
};

// ---------------------------------------------------------------------------
// Privacy policy — EN
// ---------------------------------------------------------------------------

const POLICY_EN: LegalDoc = {
  title: 'Privacy Policy',
  intro:
    'This document explains what data Blast collects, why we need it, who we share it with ' +
    'and how to have it deleted. It is written in plain language — if anything is still ' +
    'unclear, ask us and we will answer the same way.',
  sections: [
    {
      title: '1. Who processes your data',
      body: [
        'The data controller is {{entity}}, tax ID (INN) {{inn}}, registration number (OGRNIP) ' +
          '{{ogrnip}}, address: {{address}} (“we”, “us”, “Blast”). The service runs at {{site}}.',
        'For any question about your data write to {{email}} or call {{phone}}. The same ' +
          'address works for withdrawing consent and requesting deletion.',
        'This policy follows Russian Federal Law No. 152-FZ on Personal Data. For users in ' +
          'the EEA and the United Kingdom, section 8 applies in addition.'
      ]
    },
    {
      title: '2. What we collect',
      body: [
        'We collect only what the service cannot run without. Blast does no advertising or ' +
          'behavioural tracking.',
        'When you sign in with Telegram:',
        [
          'your Telegram chat ID — this is your account with us;',
          'your Telegram @username, if you have one;',
          'the first and last name you typed into the sign-up form.'
        ],
        'When you sign in with Google:',
        [
          'your email address and the confirmation that it is yours;',
          'first and last name from your Google profile;',
          'a link to your profile picture.'
        ],
        'We request the minimum Google scopes (openid, email, profile). We have no access to ' +
          'your Gmail, Drive, contacts or any other Google service.',
        'In your service profile:',
        [
          'artist nickname and the avatar you upload;',
          'your plan, payment history and remaining limits.'
        ],
        'The material you upload for generation: the audio track, song lyrics, images, and ' +
          'generation settings — chosen footage, subtitle style, effects. We store the finished ' +
          'videos so you can download and publish them.',
        'When you connect TikTok:',
        [
          'your TikTok account identifier (open_id) and display name;',
          'avatar link;',
          'TikTok access tokens — stored encrypted and never sent to the browser;',
          'statistics for videos published through the service: views, likes, comments, ' +
            'shares and publication date.'
        ],
        'Technical data: IP address, request timestamps, browser type, session identifier and ' +
          'service records of interface actions (which wizard step is open, which generation ' +
          'was started). We need these to prevent abuse, diagnose failures and build ' +
          'anonymised product statistics.',
        'We never collect, see or store your card details. At checkout you are taken to the ' +
          'secure payment page of {{bank}}: the card number, expiry date and CVV are entered on ' +
          'the bank’s side. Their gateway is PCI DSS Level 1 certified. We only receive the ' +
          'order identifier and its status.'
      ]
    },
    {
      title: '3. Why we need it',
      body: [
        [
          'to sign you in and recognise you next time;',
          'to generate videos from your track and lyrics — the core service;',
          'to publish a finished video to your connected TikTok account on your command and ' +
            'show its statistics;',
          'to count plan limits and process payments;',
          'to send you a Telegram notification when a generation finishes;',
          'to answer support requests;',
          'to protect the service: rate limiting, and detecting reuse of the same TikTok ' +
            'account to claim free limits again;',
          'to improve the product using anonymised statistics (how many people finish the ' +
            'wizard, where they go back most often).'
        ],
        'Legal bases: performance of our contract with you (the Terms), your consent where you ' +
          'give it by a separate action, and our legitimate interest in protecting the service ' +
          'from abuse.'
      ]
    },
    {
      title: '4. Cookies and similar technologies',
      body: [
        'We use strictly necessary cookies only:',
        [
          'a session cookie so you stay signed in across pages;',
          'a CSRF protection cookie;',
          'browser local storage — your interface language and the draft of the generation ' +
            'wizard, so a page reload does not lose it.'
        ],
        'We set no advertising or third-party tracking cookies. Necessary cookies cannot be ' +
          'switched off — sign-in does not work without them.'
      ]
    },
    {
      title: '5. Who we share data with',
      body: [
        'We do not sell your data and do not share it for advertising. We share only with ' +
          'parties the service cannot work without:',
        [
          'Google LLC — when you sign in with Google (policies.google.com/privacy);',
          'TikTok — when you connect an account and publish a video. We send the video, the ' +
            'description and the privacy setting you chose, and receive statistics ' +
            '(tiktok.com/legal/privacy-policy);',
          'Telegram — notifications and sign-in confirmation through our bot;',
          'hosting and object storage providers — running the service and storing files ' +
            '(servers located in the Russian Federation);',
          '{{bank}} — accepting and processing payments (the bank handles card data, not us);',
          'public authorities — only on a lawful and substantiated request.'
        ],
        'Publishing to TikTok is your action: nothing leaves the service until you press ' +
          '“Publish”.'
      ]
    },
    {
      title: '6. Where data is stored and for how long',
      body: [
        'User data is stored on servers located in the Russian Federation.',
        [
          'account, projects and settings — while your account exists;',
          'uploaded tracks, images and finished videos — until you delete the project; ' +
            'deleting a project deletes its files;',
          'TikTok access tokens — until you disconnect TikTok in your profile, then deleted ' +
            'immediately;',
          'technical logs and interface action records — up to 12 months;',
          'payment records — for the period required by tax and accounting law.'
        ],
        'Identifiers of TikTok accounts that were used to claim free limits are kept even ' +
          'after an account is deleted — otherwise the rule “one TikTok account, one free ' +
          'allowance” could be bypassed with a single click. We keep the identifier only, ' +
          'not profile content.'
      ]
    },
    {
      title: '7. Your rights',
      body: [
        [
          'find out what data we hold about you and get a copy;',
          'correct inaccurate data — name, nickname and avatar are editable in your profile;',
          'disconnect Google or TikTok from your account at any time — buttons in the profile;',
          'delete your account together with projects and files;',
          'withdraw your consent to processing;',
          'complain to the supervisory authority if you believe we are breaking the law.'
        ],
        'Send any such request to {{email}} from the address or account linked to your ' +
          'profile. We reply within 30 days, usually sooner.',
        'Withdrawing consent or deleting your account means the service can no longer be ' +
          'provided: generated videos and projects will be deleted. Download what you need ' +
          'first.'
      ]
    },
    {
      title: '8. Users in the EEA and the United Kingdom',
      body: [
        'If you are in the European Economic Area or the United Kingdom, the GDPR (UK GDPR) ' +
          'applies. The legal bases are listed in section 3: performance of a contract ' +
          '(Art. 6(1)(b)), consent (Art. 6(1)(a)) and legitimate interest (Art. 6(1)(f)).',
        'In addition to the rights in section 7 you may request data portability in a ' +
          'machine-readable format, restriction of processing, and you may object to ' +
          'processing based on legitimate interest. You may lodge a complaint with your local ' +
          'supervisory authority.',
        'Data is processed on servers in the Russian Federation, that is outside the EEA. ' +
          'The transfer is based on your explicit consent given at sign-up and is limited to ' +
          'what is necessary to perform our contract with you.'
      ]
    },
    {
      title: '9. How we protect data',
      body: [
        [
          'all traffic to the service goes over HTTPS;',
          'TikTok access tokens are stored encrypted, with the key kept apart from the data;',
          'the service has no passwords at all — sign-in is confirmed by Telegram or Google, ' +
            'so there is no password here to steal;',
          'API requests are rate-limited and uploaded files are validated by content, not by ' +
            'file extension;',
          'access to production data is limited to a small number of people and only for ' +
            'operating the service.'
        ],
        'No protection is absolute. If a breach affects your data, we will tell you and ' +
          'notify the competent authority within the statutory deadlines.'
      ]
    },
    {
      title: '10. Minors',
      body: [
        'The service is not intended for anyone under 14. Paid plans are available from the ' +
          'age of 18, or with the consent of a legal guardian. If you learn that a child is ' +
          'using the service without such consent, write to {{email}} and we will delete the ' +
          'account and its data.'
      ]
    },
    {
      title: '11. Changes to this policy',
      body: [
        'We may update this policy — for example when a new integration appears. The current ' +
          'version always lives at this address, and the revision date is shown at the top of ' +
          'the page. We announce material changes in the interface or via the Telegram bot ' +
          'before they take effect.'
      ]
    }
  ]
};

// ---------------------------------------------------------------------------
// Terms of Service — EN
// ---------------------------------------------------------------------------

const OFFER_EN: LegalDoc = {
  title: 'Terms of Service',
  intro:
    'These are the terms on which you use Blast. By using the service you accept them in ' +
    'full. If something does not work for you, do not start — or better, write to us: ' +
    'some questions are settled faster than you would expect.',
  sections: [
    {
      title: '1. Definitions',
      body: [
        [
          '“we”, “us” — {{entity}}, tax ID (INN) {{inn}}, registration number (OGRNIP) ' +
            '{{ogrnip}}, address: {{address}};',
          '“Service”, “Blast” — the web application at {{site}} and the related Telegram bot;',
          '“you”, “User” — a person who has registered in the Service;',
          '“Material” — the audio recording, lyrics, images and other files you upload;',
          '“Result” — a video generated by the Service from your Material;',
          '“Account” — your user account in the Service;',
          '“Subscription” — paid access to the Service for a period of time.'
        ]
      ]
    },
    {
      title: '2. What we provide',
      body: [
        'We give you access to the Service, which automatically assembles vertical videos ' +
          'with subtitles and effects out of your Material and, on your command, publishes ' +
          'them to your connected TikTok account.',
        'The Service is a tool. We are responsible for it working and producing videos; ' +
          'we are not responsible for how many views those videos get. We do not guarantee or ' +
          'promise any figures for reach, followers or income.'
      ]
    },
    {
      title: '3. Accepting these terms',
      body: [
        'These terms are accepted the moment you complete registration — that is, confirm ' +
          'sign-in through Telegram or Google. No separate signature is needed.',
        'Paying for a plan also means you accept the payment terms in section 6.'
      ]
    },
    {
      title: '4. Your account',
      body: [
        'The Service has no password: sign-in is confirmed by your Telegram or Google ' +
          'account. Access to the Service therefore equals access to those accounts — keep ' +
          'them safe.',
        [
          'one person — one Account;',
          'you may not give access to third parties;',
          'registration details must be real: the name from your profile appears on payment ' +
            'documents;',
          'if you lose access to Telegram or Google, write to {{email}} and we will restore ' +
            'access based on proof of ownership.'
        ]
      ]
    },
    {
      title: '5. Free access and limits',
      body: [
        'A new User gets a limited number of free videos to try the Service. The exact number ' +
          'is shown on the pricing page and may change.',
        'The extended per-track allowance is unlocked by connecting a TikTok account: this is ' +
          'how we check that a real person is behind the Account, and it also lets you publish ' +
          'in one click.',
        'Each TikTok account unlocks a free allowance only once. Connecting a TikTok account ' +
          'that has already been used in the Service — including on another Account, including ' +
          'a deleted one — counts as claiming free access twice and leads to the consequences ' +
          'in section 9.'
      ]
    },
    {
      title: '6. Plans, payment and refunds',
      body: [
        'Current contents and prices are on the pricing page. As of this revision:',
        [
          '“Blast” — RUB 1,990 per month, subscription: 100 clips, up to 4 tracks, every third ' +
            'month without a clip limit;',
          '“Glow” — RUB 7,990, one-off purchase: 400 clips, up to 10 tracks, a CapCut template ' +
            'for your track;',
          '“Impulse” — RUB 29,990, one-off purchase for a year: unlimited clips, up to 24 ' +
            'tracks, personal release management;',
          'free access — a limited number of clips for one track (the exact number is on the ' +
            'pricing page), extended by connecting a TikTok account.'
        ],
        'Prices may change, but never for a period you have already paid for.',
        'Payments are made remotely with Visa, Mastercard and MIR cards or via T-Pay, and are ' +
          'processed by {{bank}}. Card details are entered on the bank’s secure page and never ' +
          'reach us. Payment is complete when the money reaches our account; plan access opens ' +
          'automatically after confirmation.',
        'A Subscription renews for the next period unless you cancel it. You can cancel any ' +
          'time in your profile: access remains until the end of the paid period and no ' +
          'further charge is made.',
        'Refunds. Before the first generation on a paid plan we refund in full. After that, ' +
          'each generated clip counts as delivered, so we refund the paid and unused remainder: ' +
          'unspent clips and the unexpired period, less the cost of generations already ' +
          'performed. Send refund requests to {{email}}; we process them within 10 business ' +
          'days.',
        'If a generation fails through our fault, the spent limit is returned automatically; ' +
          'if we cannot deliver the service at all, we refund what you paid for it.'
      ]
    },
    {
      title: '7. Rights to Material and Results',
      body: [
        'Your Material stays yours. By uploading it you grant us a limited licence to store, ' +
          'process and transmit it to TikTok — strictly as needed to provide the service and ' +
          'only on your command.',
        'You confirm that you hold the rights to everything you upload: the recording, the ' +
          'lyrics, the images, and the right to use any other person’s voice or face that ' +
          'appears in them. Rights-holder claims about your Material are yours to handle.',
        'Exclusive rights to the Result are yours. You may publish, monetise and use the ' +
          'videos however you like; no separate permission from us is required.',
        'We do not use your Material or Results in advertising or portfolio examples without ' +
          'your separate written consent.',
        'Rights to the Service itself, its interface, templates and footage library belong to ' +
          'us. The footage the Service puts into videos is licensed for use inside Results; ' +
          'you may not extract it or use it outside the Service.'
      ]
    },
    {
      title: '8. Publishing to TikTok',
      body: [
        'By connecting TikTok you authorise the Service to publish videos on your behalf. ' +
          'Each publication is triggered by your action only — we never post on our own ' +
          'initiative.',
        'A published video is subject to TikTok’s rules, not ours. Bans, takedowns and reach ' +
          'restrictions are TikTok’s decisions and we cannot influence them.',
        'You can disconnect TikTok in your profile at any time; access tokens are deleted, ' +
          'and videos already published stay in your TikTok account.'
      ]
    },
    {
      title: '9. Prohibited use and suspension',
      body: [
        'You may not:',
        [
          'upload Material you do not hold the rights to;',
          'create videos using another person’s voice or face without their consent;',
          'create material prohibited by law: extremist, pornographic, inciting violence, or ' +
            'misleading about another person’s identity;',
          'create several Accounts for one person in order to claim free limits again;',
          'connect a TikTok account that has already been used in the Service on another ' +
            'Account;',
          'circumvent limits and protections, automate the interface, flood the Service with ' +
            'requests, or probe it for vulnerabilities without our permission;',
          'resell access to the Service.'
        ],
        'Consequences. We may suspend or block access for any breach. Reusing a TikTok account ' +
          'is a special case: ALL Accounts belonging to that person are blocked, including ' +
          'those where nothing else was wrong — because the breach is precisely that one ' +
          'person holds several Accounts.',
        'A block for claiming free access twice does not entitle you to a refund of free ' +
          'limits — there was nothing to refund. A paid, unused period is refunded under ' +
          'section 6.',
        'If you believe a block is a mistake, write to {{email}}. We will look into it and ' +
          'lift it if we got it wrong.'
      ]
    },
    {
      title: '10. Availability and liability',
      body: [
        'The Service is provided “as is”. We aim for round-the-clock availability but do not ' +
          'promise it: maintenance happens, and TikTok, Telegram, Google, hosting and the ' +
          'payment provider can all fail.',
        'We are not liable for: promotion results and reach; TikTok’s decisions about your ' +
          'videos; your losses from using the Results; outages of third-party services.',
        'In any case our liability is limited to the amount you paid for the most recent paid ' +
          'period.',
        'We are not responsible for the content of your Material or videos — you are.'
      ]
    },
    {
      title: '11. Personal data',
      body: [
        'How we handle personal data is described in the Privacy Policy, which forms part of ' +
          'these terms. By registering you consent to processing on the terms of that policy.'
      ]
    },
    {
      title: '12. Changes to these terms',
      body: [
        'We may change these terms. A new version is published at this address and takes ' +
          'effect 5 calendar days after publication unless a later date is stated. We announce ' +
          'material changes in the interface or via the Telegram bot. Continuing to use the ' +
          'Service after a change takes effect means you accept the new version.',
        'Price changes never apply to a period already paid for.'
      ]
    },
    {
      title: '13. Governing law and disputes',
      body: [
        'These terms are governed by the law of the Russian Federation. Disputes are first ' +
          'settled in writing: send your claim to {{email}} and we will answer within 30 ' +
          'calendar days. If no agreement is reached, the dispute is heard by the court at our ' +
          'place of establishment; for consumers, the rules of consumer protection law apply.'
      ]
    },
    {
      title: '14. Details',
      body: [
        [
          'Provider: {{entity}}',
          'Tax ID (INN): {{inn}}',
          'Registration number (OGRNIP): {{ogrnip}}',
          'Address: {{address}}',
          'Email: {{email}}',
          'Phone: {{phone}}',
          'Payments accepted by: {{bank}}',
          'Website: {{site}}'
        ]
      ]
    }
  ]
};

export const LEGAL_DOCS: Record<'ru' | 'en', Record<LegalKind, LegalDoc>> = {
  ru: { policy: POLICY_RU, offer: OFFER_RU },
  en: { policy: POLICY_EN, offer: OFFER_EN }
};
