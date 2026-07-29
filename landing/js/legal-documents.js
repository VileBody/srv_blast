(() => {
  'use strict';

  const VERSION = '1.0-draft';
  const EFFECTIVE_RU = '29 июля 2026 г.';
  const EFFECTIVE_EN = '29 July 2026';
  const operatorRu = 'Индивидуальный предприниматель Чернов Никита Романович, ИНН 623013205426, ОГРНИП 324620000005644';
  const operatorEn = 'Individual Entrepreneur Nikita Romanovich Chernov, Tax ID 623013205426, Primary State Registration Number 324620000005644';
  const contact = '<a href="mailto:support@blast808.com">support@blast808.com</a>';

  const documents = {
    privacy: {
      ru: {
        title: 'Политика конфиденциальности и обработки персональных данных',
        body: `<p>Настоящая Политика описывает обработку персональных данных при посещении сайта blast808.com и использовании сервиса Blast через Telegram-бот.</p>
          <h2>1. Оператор и область действия</h2><p>Оператор: ${operatorRu}. Адрес: 390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60. Контакт по вопросам данных: ${contact}.</p>
          <h2>2. Какие данные обрабатываются</h2><ul><li>технические данные сайта: выбранный язык и настройки cookie, сохраняемые локально в браузере;</li><li>идентификатор и общедоступные данные профиля Telegram;</li><li>аудиозаписи, тексты, настройки и иные материалы, добровольно направленные для генерации;</li><li>сгенерированный контент и история выполнения заказа;</li><li>контактные данные, предоставленные при обращении в поддержку;</li><li>сведения о статусе и сумме платежа без получения полных реквизитов банковской карты.</li></ul>
          <h2>3. Цели обработки</h2><ul><li>оказание услуг и доставка результата;</li><li>идентификация пользователя и поддержка;</li><li>обработка оплаты, возвратов и бухгалтерский учёт;</li><li>защита сервиса от злоупотреблений и выполнение требований закона;</li><li>аналитика и маркетинг — только после отдельного согласия, если такие инструменты будут подключены.</li></ul>
          <h2>4. Правовые основания и согласие</h2><p>Данные обрабатываются для исполнения договора, выполнения обязанностей по закону и на основании согласия пользователя в случаях, где оно требуется. Отзыв согласия не влияет на законность обработки до его отзыва.</p>
          <h2>5. Получатели и инфраструктура</h2><p>Данные могут передаваться Telegram, платёжному партнёру АО «Т-Банк», а также подрядчикам, обеспечивающим хостинг, хранение и автоматизированную генерацию, только в объёме, необходимом для оказания сервиса.</p><p><span class="legal-placeholder">[Указать полный перечень обработчиков, страны размещения, основания и условия трансграничной передачи после подтверждения инфраструктуры.]</span></p>
          <h2>6. Сроки хранения</h2><p><span class="legal-placeholder">[Указать подтверждённые сроки хранения Telegram-профиля, исходных аудиофайлов, текстов, результатов генерации, логов, обращений и платёжной истории.]</span> После достижения целей данные удаляются или обезличиваются, кроме сведений, которые необходимо хранить по закону.</p>
          <h2>7. Права пользователя</h2><p>Пользователь вправе запросить сведения об обработке, исправление, блокирование или удаление данных, отозвать согласие и обжаловать обработку. Запрос направляется на ${contact}. Для защиты данных Оператор может запросить подтверждение личности.</p>
          <h2>8. Удаление данных и отключение TikTok</h2><p>Запрос на удаление данных или прекращение обработки данных, полученных через подключённую платформу, направляется на ${contact}. Если в сервис будет добавлена авторизация TikTok, пользователь также сможет отозвать доступ в настройках TikTok; фактически поддерживаемый порядок должен быть отражён после завершения интеграции.</p>
          <h2>9. Безопасность и изменения</h2><p>Оператор применяет организационные и технические меры защиты. Политика может обновляться; новая версия публикуется на этой странице с новой датой вступления в силу.</p>`
      },
      en: {
        title: 'Privacy and Personal Data Processing Policy',
        body: `<p>This Policy describes personal data processing when visiting blast808.com and using the Blast service through its Telegram bot.</p>
          <h2>1. Controller and scope</h2><p>Controller: ${operatorEn}. Address: 18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia. Data contact: ${contact}.</p>
          <h2>2. Data we process</h2><ul><li>website technical data: selected language and cookie choices stored locally in the browser;</li><li>Telegram identifier and public profile data;</li><li>audio, lyrics, settings and other materials voluntarily submitted for generation;</li><li>generated content and order execution history;</li><li>contact details provided to support;</li><li>payment status and amount without receiving full bank card details.</li></ul>
          <h2>3. Purposes</h2><ul><li>providing the service and delivering results;</li><li>user identification and support;</li><li>payment, refund and accounting operations;</li><li>abuse prevention and legal compliance;</li><li>analytics and marketing only after separate consent if such tools are introduced.</li></ul>
          <h2>4. Legal bases and consent</h2><p>Data is processed to perform a contract, comply with legal obligations and on the basis of consent where required. Withdrawal does not affect processing that occurred before withdrawal.</p>
          <h2>5. Recipients and infrastructure</h2><p>Data may be shared with Telegram, payment partner T-Bank, and providers supporting hosting, storage and automated generation, only to the extent necessary to operate the service.</p><p><span class="legal-placeholder">[Confirm and list processors, hosting countries, legal grounds and cross-border transfer arrangements.]</span></p>
          <h2>6. Retention</h2><p><span class="legal-placeholder">[Confirm retention periods for Telegram profile data, source audio, text, generated output, logs, support requests and payment history.]</span> Data is deleted or anonymized when no longer required, except where retention is required by law.</p>
          <h2>7. User rights</h2><p>Users may request access, correction, restriction or deletion, withdraw consent and object to processing by contacting ${contact}. Identity verification may be required to protect user data.</p>
          <h2>8. Data deletion and TikTok disconnection</h2><p>Requests to delete data or stop processing data obtained through a connected platform may be sent to ${contact}. If TikTok authorization is introduced, users will also be able to revoke access in TikTok settings; the supported flow must be updated after integration is complete.</p>
          <h2>9. Security and updates</h2><p>The Controller applies organizational and technical safeguards. Updates are published on this page with a revised effective date.</p>`
      }
    },
    terms: {
      ru: { title: 'Условия использования', body: `<p>Настоящие Условия регулируют использование сайта Blast и функций, доступных через Telegram-бот и будущие подключения сторонних платформ.</p>
        <h2>1. Принятие условий</h2><p>Используя сервис или оформляя заказ, пользователь подтверждает ознакомление с Условиями, Политикой конфиденциальности и применимой Публичной офертой. Если пользователь не согласен, он должен прекратить использование сервиса.</p>
        <h2>2. Сервис</h2><p>Blast автоматически анализирует предоставленные материалы и создаёт цифровой видеоконтент. Доступные функции, лимиты, стоимость и сроки показываются пользователю до заказа или в интерфейсе Telegram-бота.</p>
        <h2>3. Требования к пользователю</h2><p>Пользователь должен обладать необходимой дееспособностью и правами на материалы. Запрещено загружать незаконный контент, нарушать права третьих лиц, обходить ограничения, вмешиваться в работу сервиса или использовать результат незаконным способом.</p>
        <h2>4. Пользовательские материалы</h2><p>Права на исходные материалы сохраняются за их правообладателями. Пользователь предоставляет Оператору ограниченное право обрабатывать материалы только для выполнения заказа, поддержки и иных согласованных целей.</p>
        <h2>5. Сторонние платформы</h2><p>Telegram, TikTok и иные платформы действуют по собственным правилам. Blast не является их подразделением. При подключении аккаунта пользователю должны быть показаны запрашиваемые разрешения; доступ можно отозвать в настройках соответствующей платформы.</p>
        <h2>6. Оплата и возвраты</h2><p>Если пользователь приобретает платные услуги, применяются цена и условия, показанные до оплаты, а также Публичная оферта. Текущие условия оплаты и возврата требуют подтверждения согласно файлу LEGAL_DATA_REQUIRED.md.</p>
        <h2>7. Доступность и результат</h2><p>Оператор стремится обеспечивать стабильную работу, но не гарантирует непрерывность сторонних платформ или достижение конкретных показателей просмотров, охватов и продвижения.</p>
        <h2>8. Ограничение ответственности</h2><p>Ответственность определяется обязательными нормами применимого права. Ничто в Условиях не исключает ответственность, которую нельзя ограничить законом.</p>
        <h2>9. Прекращение доступа и изменения</h2><p>Доступ может быть ограничен при нарушении Условий или требований закона. Изменения публикуются на этой странице. Оператор: ${operatorRu}; контакт: ${contact}.</p>` },
      en: { title: 'Terms of Service', body: `<p>These Terms govern use of the Blast website, functions available through its Telegram bot and future third-party platform connections.</p>
        <h2>1. Acceptance</h2><p>By using the service or placing an order, the user acknowledges these Terms, the Privacy Policy and any applicable Public Offer. Users who disagree must stop using the service.</p>
        <h2>2. Service</h2><p>Blast automatically analyzes submitted materials and creates digital video content. Available functions, limits, prices and timing are displayed before ordering or in the Telegram bot.</p>
        <h2>3. User requirements</h2><p>Users must have legal capacity and all necessary rights to submitted materials. Illegal content, infringement, circumvention of restrictions, interference with the service and unlawful use of output are prohibited.</p>
        <h2>4. User materials</h2><p>Rights in source materials remain with their owners. The user grants the Controller a limited right to process materials only to fulfill an order, provide support and carry out other agreed purposes.</p>
        <h2>5. Third-party platforms</h2><p>Telegram, TikTok and other platforms operate under their own terms. Blast is not affiliated with them. Requested permissions must be disclosed before an account connection, and access may be revoked through the relevant platform settings.</p>
        <h2>6. Payments and refunds</h2><p>Paid services are governed by the price and conditions shown before payment and the applicable Public Offer. Current payment and refund terms require confirmation as listed in LEGAL_DATA_REQUIRED.md.</p>
        <h2>7. Availability and outcomes</h2><p>The Controller aims to maintain the service but does not guarantee uninterrupted third-party platforms or particular view, reach or promotion outcomes.</p>
        <h2>8. Liability</h2><p>Liability is governed by mandatory applicable law. Nothing excludes liability that cannot legally be limited.</p>
        <h2>9. Suspension and changes</h2><p>Access may be restricted for violations or legal compliance. Updates are published here. Controller: ${operatorEn}; contact: ${contact}.</p>` }
    },
    cookies: {
      ru: { title: 'Политика cookie', body: `<p>Эта Политика объясняет локальное хранение данных и управление необязательными технологиями на blast808.com.</p>
        <h2>1. Что используется сейчас</h2><p>Сайт сохраняет в localStorage выбранный язык (<code>blast_language</code>) и версионированное решение о cookie (<code>blast_cookie_consent</code>). Эти значения не отправляются сайтом третьим лицам и нужны для сохранения настроек.</p>
        <h2>2. Категории</h2><ul><li><strong>Необходимые:</strong> язык, состояние согласия и базовые функции;</li><li><strong>Аналитические:</strong> измерение использования сайта;</li><li><strong>Маркетинговые:</strong> измерение рекламы и кампаний.</li></ul><p>На дату этой версии аналитические и маркетинговые скрипты в лендинге не настроены. Если они будут добавлены, модуль consent не запустит их до разрешения соответствующей категории.</p>
        <h2>3. Выбор пользователя</h2><p>Можно принять все категории, отклонить необязательные или настроить их раздельно. Решение сохраняется в браузере и может быть изменено через ссылку «Настроить cookie» в footer главной страницы.</p>
        <h2>4. Управление браузером</h2><p>Удаление данных сайта в настройках браузера сбросит сохранённый выбор. Блокировка localStorage может помешать сохранению языка и решения.</p>
        <h2>5. Контакт</h2><p>Вопросы можно направить на ${contact}.</p>` },
      en: { title: 'Cookie Policy', body: `<p>This Policy explains local storage and optional technology controls on blast808.com.</p>
        <h2>1. Current use</h2><p>The site stores the selected language (<code>blast_language</code>) and versioned consent choice (<code>blast_cookie_consent</code>) in localStorage. The site does not send these values to third parties; they preserve user settings.</p>
        <h2>2. Categories</h2><ul><li><strong>Necessary:</strong> language, consent state and core functions;</li><li><strong>Analytics:</strong> site usage measurement;</li><li><strong>Marketing:</strong> advertising and campaign measurement.</li></ul><p>As of this version, no analytics or marketing scripts are configured in the landing page. If introduced, the consent module will not activate them until the relevant category is allowed.</p>
        <h2>3. User choices</h2><p>Users may accept all, reject optional categories or configure each separately. The choice is stored in the browser and may be changed using “Cookie settings” in the home page footer.</p>
        <h2>4. Browser controls</h2><p>Clearing site data resets the saved choice. Blocking localStorage may prevent the language and consent choices from being saved.</p>
        <h2>5. Contact</h2><p>Questions may be sent to ${contact}.</p>` }
    },
    consent: {
      ru: { title: 'Согласие на обработку персональных данных', body: `<p>Настоящий текст является формой согласия, которую необходимо связать с явным действием пользователя в интерфейсе Telegram-бота или ином месте сбора данных.</p>
        <h2>1. Кому предоставляется согласие</h2><p>${operatorRu}, адрес: 390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60.</p>
        <h2>2. Данные и цели</h2><p>Пользователь соглашается на обработку Telegram ID и данных профиля, контактных данных, аудио, текста, настроек, созданных результатов, технических и платёжных метаданных для предоставления сервиса, поддержки, расчётов, безопасности и исполнения закона.</p>
        <h2>3. Действия с данными</h2><p>Согласие охватывает сбор, запись, систематизацию, накопление, хранение, уточнение, извлечение, использование, передачу уполномоченным обработчикам, блокирование, удаление и уничтожение — в пределах заявленных целей.</p>
        <h2>4. Срок и отзыв</h2><p>Согласие действует до достижения целей или его отзыва, если дальнейшая обработка не требуется по закону или договору. Отзыв направляется на ${contact}.</p>
        <h2>5. Подтверждение</h2><p><span class="legal-placeholder">[До запуска добавить в фактическую точку сбора отдельное непредустановленное действие согласия, ссылку на Политику и запись версии/времени согласия.]</span></p>` },
      en: { title: 'Consent to Personal Data Processing', body: `<p>This is a consent form that must be connected to an explicit user action in the Telegram bot or another point where data is collected.</p>
        <h2>1. Controller</h2><p>${operatorEn}, address: 18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia.</p>
        <h2>2. Data and purposes</h2><p>The user consents to processing Telegram ID and profile data, contact details, audio, text, settings, generated results, technical and payment metadata to provide the service, support users, process payments, maintain security and comply with law.</p>
        <h2>3. Processing operations</h2><p>Consent covers collection, recording, organization, storage, updating, retrieval, use, disclosure to authorized processors, restriction, deletion and destruction within the stated purposes.</p>
        <h2>4. Duration and withdrawal</h2><p>Consent remains effective until its purposes are achieved or it is withdrawn, unless continued processing is required by law or contract. Withdrawal requests may be sent to ${contact}.</p>
        <h2>5. Confirmation</h2><p><span class="legal-placeholder">[Before launch, add a separate unticked consent action at the actual collection point, link the Policy, and record consent version and time.]</span></p>` }
    },
    offer: {
      ru: { title: 'Публичная оферта на оказание услуг', body: `<p>${operatorRu}, адрес: 390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60, предлагает заключить договор оказания услуг по автоматизированной генерации видеоконтента.</p>
        <h2>1. Предмет</h2><p>Заказчик направляет аудиоматериал и настройки через Telegram-бот. Исполнитель анализирует материалы и передаёт сгенерированные видеоролики в электронной форме.</p>
        <h2>2. Тарифы, зафиксированные в текущей версии проекта</h2><ul><li>Blast Trial — 990 ₽, 5 видеороликов;</li><li>Blast — 1 990 ₽/месяц, 15 видеороликов;</li><li>Glow — 7 990 ₽, 30 видеороликов и подбор двух блогеров;</li><li>Impulse — 29 990 ₽, маркетинговая концепция, контент-план, 50 видеороликов и 10–12 размещений.</li></ul><p><span class="legal-placeholder">[Подтвердить актуальность тарифов и устранить расхождение с обещанием 3 бесплатных видео на лендинге.]</span></p>
        <h2>3. Оплата</h2><p>В текущих документах проекта указана оплата картами Visa, Mastercard, МИР и T‑Pay через АО «Т-Банк». Условие должно быть подтверждено до публикации финальной версии.</p>
        <h2>4. Исполнение и права на материалы</h2><p>Заказчик гарантирует права на загружаемые материалы. Результат передаётся через Telegram-бот. Исполнитель не гарантирует конкретные показатели продвижения.</p>
        <h2>5. Отмена и возврат</h2><p>В текущей версии проекта указан полный возврат до начала генерации и отсутствие возврата после её начала. Запрос направляется на ${contact}. <span class="legal-placeholder">[Проверить формулировку, срок возврата и соответствие обязательным нормам о защите прав потребителей.]</span></p>
        <h2>6. Акцепт и реквизиты</h2><p>Акцептом является оплата после ознакомления с условиями. Исполнитель: ${operatorRu}; контакт: ${contact}; телефон: +7 (910) 572‑49‑67.</p>` },
      en: { title: 'Public Offer for Services', body: `<p>${operatorEn}, address: 18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia, offers automated video content generation services under this public offer.</p>
        <h2>1. Service</h2><p>The Customer submits audio and settings through the Telegram bot. The Contractor analyzes the materials and delivers generated videos electronically.</p>
        <h2>2. Plans recorded in the current project</h2><ul><li>Blast Trial — RUB 990, 5 videos;</li><li>Blast — RUB 1,990/month, 15 videos;</li><li>Glow — RUB 7,990, 30 videos and selection of two bloggers;</li><li>Impulse — RUB 29,990, marketing concept, content plan, 50 videos and 10–12 placements.</li></ul><p><span class="legal-placeholder">[Confirm current plans and resolve the discrepancy with the landing page promise of 3 free videos.]</span></p>
        <h2>3. Payment</h2><p>Current project documents state that Visa, Mastercard, MIR and T‑Pay payments are accepted through T-Bank. This must be confirmed before the final version is published.</p>
        <h2>4. Performance and material rights</h2><p>The Customer warrants the necessary rights to submitted materials. Results are delivered through the Telegram bot. No particular promotion metrics are guaranteed.</p>
        <h2>5. Cancellation and refunds</h2><p>The current project states that a full refund is available before generation starts and no refund is available after it starts. Requests are sent to ${contact}. <span class="legal-placeholder">[Review wording, timing and mandatory consumer protection requirements.]</span></p>
        <h2>6. Acceptance and details</h2><p>Payment after reviewing the terms constitutes acceptance. Contractor: ${operatorEn}; contact: ${contact}; phone: +7 (910) 572‑49‑67.</p>` }
    },
    contacts: {
      ru: { title: 'Контакты и реквизиты', body: `<h2>Служба поддержки</h2><p>Email: ${contact}<br>Телефон: <a href="tel:+79105724967">+7 (910) 572‑49‑67</a><br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a></p><h2>Оператор и исполнитель</h2><p>${operatorRu}<br>Адрес: 390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60</p><h2>Платёжный партнёр</h2><p>В текущей версии проекта указан АО «Т-Банк». <span class="legal-placeholder">[Подтвердить договор, способы оплаты и актуальные реквизиты до финальной юридической публикации.]</span></p>` },
      en: { title: 'Contacts and Legal Details', body: `<h2>Support</h2><p>Email: ${contact}<br>Phone: <a href="tel:+79105724967">+7 (910) 572‑49‑67</a><br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a></p><h2>Controller and contractor</h2><p>${operatorEn}<br>Address: 18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia</p><h2>Payment partner</h2><p>The current project names T-Bank. <span class="legal-placeholder">[Confirm the agreement, payment methods and details before final legal publication.]</span></p>` }
    }
  };

  const content = document.querySelector('[data-legal-content]');
  const key = document.body.dataset.legalDocument;
  if (!content || !documents[key]) throw new Error(`[landing] unknown legal document: ${key}`);

  function render() {
    const language = window.BLAST_I18N?.getLanguage() || 'en';
    const documentData = documents[key][language] || documents[key].en;
    const effective = language === 'ru' ? EFFECTIVE_RU : EFFECTIVE_EN;
    const draft = language === 'ru' ? 'Рабочий черновик. Требуется финальная проверка профильным юристом.' : 'Working draft. Final review by qualified legal counsel is required.';
    const versionLabel = language === 'ru' ? 'Версия' : 'Version';
    const dateLabel = language === 'ru' ? 'Дата вступления в силу' : 'Effective date';
    content.innerHTML = `<h1>${documentData.title}</h1><p class="legal-draft">${draft}</p><div class="legal-meta"><span>${versionLabel}: ${VERSION}</span><span>${dateLabel}: ${effective}</span></div>${documentData.body}`;
  }

  document.addEventListener('blast:languagechange', render);
  render();
})();