(() => {
  'use strict';

  /*
   * Юридические тексты лендинга.
   *
   * ИСТОЧНИК ПРАВДЫ — документы веб-приложения:
   *   web_app/frontend/src/data/legal-docs.ts  (POLICY_RU/EN, OFFER_RU/EN)
   * Там же лежат реквизиты (LEGAL_ENTITY) и платёжный партнёр (PAYMENT_PARTNER).
   * Тексты ниже пересказывают те же факты для лендинга и добавляют документы,
   * которых в приложении нет (cookie-политика лендинга, форма согласия, контакты).
   *
   * ПРАВИЛО: любая правка фактов (данные, сроки хранения, тарифы, возврат,
   * получатели) вносится СНАЧАЛА в legal-docs.ts, затем зеркалится сюда, и
   * VERSION/EFFECTIVE поднимаются в обоих местах. Два расходящихся набора
   * документов на одном бренде — прямой путь к отказу модерации Google/TikTok.
   */

  const VERSION = '2.0';
  const EFFECTIVE_RU = '8 сентября 2026 г.';
  const EFFECTIVE_EN = '8 September 2026';
  const operatorRu = 'Индивидуальный предприниматель Чернов Никита Романович, ИНН 623013205426, ОГРНИП 324620000005644';
  const operatorEn = 'Individual Entrepreneur Nikita Romanovich Chernov, Tax ID 623013205426, Primary State Registration Number 324620000005644';
  const addressRu = '390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60';
  const addressEn = '18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia';
  const contact = '<a href="mailto:support@blast808.com">support@blast808.com</a>';
  const phoneRu = '<a href="tel:+79105724967">+7 (910) 572‑49‑67</a>';
  const bankRu = 'АО «Т-Банк» (tbank.ru)';
  const bankEn = 'T-Bank JSC (tbank.ru)';
  const appRu = '<a href="https://app.blast808.com" rel="noopener">app.blast808.com</a>';
  const appEn = appRu;

  const documents = {
    privacy: {
      ru: {
        title: 'Политика конфиденциальности и обработки персональных данных',
        body: `<p>Политика описывает обработку персональных данных на сайте blast808.com, в веб-приложении ${appRu} и в Telegram-боте Blast. Это один сервис одного оператора: аккаунт, материалы и оплаты общие независимо от того, откуда вы вошли.</p>
          <h2>1. Оператор и область действия</h2><p>Оператор: ${operatorRu}. Адрес: ${addressRu}. Вопросы об обработке данных, отзыв согласия и требование удалить данные — ${contact}, телефон ${phoneRu}.</p><p>Политика составлена в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных». Для пользователей из ЕЭЗ и Великобритании дополнительно применяются правила раздела 8 <a href="https://app.blast808.com/legal/policy" rel="noopener">политики приложения</a>.</p>
          <h2>2. Какие данные обрабатываются</h2><p>Мы собираем только то, без чего сервис не работает. Отдельной рекламной или поведенческой слежки в Blast нет.</p><ul>
            <li>технические данные лендинга: выбранный язык и решение по cookie, которые хранятся локально в браузере;</li>
            <li>при входе через Telegram: идентификатор чата (chat_id), имя пользователя (@username), если оно указано, имя и фамилия из формы регистрации;</li>
            <li>при входе через Google: адрес электронной почты и подтверждение владения ею, имя и фамилия, ссылка на фото профиля (запрашиваются минимальные доступы openid, email, profile — к почте, диску и контактам доступа нет);</li>
            <li>профиль сервиса: ник артиста, аватар, выбранный тариф, история оплат и остаток лимитов;</li>
            <li>материалы для генерации: аудиофайл трека, текст песни, изображения, настройки генерации — и готовые видео, чтобы вы могли их скачать;</li>
            <li>при подключении TikTok: идентификатор аккаунта (open_id), отображаемое имя, ссылка на аватар и токены доступа (хранятся в зашифрованном виде);</li>
            <li>обращения в поддержку и сведения о статусе и сумме платежа — полные реквизиты банковской карты к нам не попадают.</li></ul>
          <h2>3. Цели обработки</h2><ul><li>вход в аккаунт и узнавание вас при следующем входе;</li><li>генерация видео из вашего трека и текста — основная услуга;</li><li>публикация ролика в вашем аккаунте TikTok по вашей команде и показ статистики;</li><li>учёт лимитов тарифа и проведение оплаты;</li><li>уведомления о завершении генерации;</li><li>ответы на обращения в поддержку;</li><li>защита сервиса от злоупотреблений: ограничение частоты запросов, выявление повторного использования одного аккаунта TikTok ради бесплатных лимитов;</li><li>улучшение продукта по обезличенной статистике.</li></ul>
          <h2>4. Правовые основания</h2><p>Исполнение договора с вами (публичная оферта), ваше согласие — там, где вы даёте его отдельным действием, и наш законный интерес в защите сервиса от злоупотреблений. Отзыв согласия не влияет на законность обработки до его отзыва.</p>
          <h2>5. Cookie и локальное хранилище</h2><p>Приложение использует только технически необходимые cookie: cookie сессии, cookie защиты от подделки запросов (CSRF) и локальное хранилище браузера — язык интерфейса и черновик мастера генерации. Рекламных и трекинговых cookie сторонних сетей мы не ставим. Правила лендинга описаны отдельно в <a href="cookies.html" data-keep-language>Политике cookie</a>.</p>
          <h2>6. Кому передаются данные</h2><p>Мы не продаём данные и не передаём их для рекламы. Передача происходит только тем, без кого услуга невозможна:</p><ul><li>Google LLC — при входе через Google (подтверждение почты);</li><li>TikTok — при подключении аккаунта и публикации ролика: видео, описание и выбранные вами настройки приватности; обратно приходит статистика;</li><li>Telegram — доставка уведомлений и подтверждение входа через бота;</li><li>хостинг и объектное хранилище — размещение сервиса и файлов, серверы на территории Российской Федерации;</li><li>${bankRu} — приём и обработка платежей: данные карты обрабатывает банк, не мы;</li><li>государственные органы — только по законному и мотивированному запросу.</li></ul><p>Публикация ролика в TikTok — ваше действие: без нажатия кнопки «Выложить» мы ничего никуда не отправляем.</p>
          <h2>7. Где и сколько хранятся данные</h2><p>Данные пользователей хранятся на серверах на территории Российской Федерации.</p><ul><li>аккаунт, проекты и настройки — пока существует ваш аккаунт;</li><li>загруженные треки, изображения и готовые видео — пока вы не удалите проект;</li><li>токены доступа TikTok — до отключения аккаунта TikTok в профиле, затем удаляются немедленно;</li><li>технические журналы и записи о действиях в интерфейсе — до 12 месяцев;</li><li>сведения об оплатах — в течение срока, установленного налоговым и бухгалтерским законодательством.</li></ul><p>Идентификаторы аккаунтов TikTok, использованных для получения бесплатных лимитов, сохраняются и после удаления аккаунта — без этого правило «один аккаунт TikTok — один бесплатный лимит» обходилось бы одним нажатием кнопки. Хранится только идентификатор, без содержимого профиля.</p>
          <h2>8. Права пользователя</h2><ul><li>узнать, какие ваши данные у нас есть, и получить их копию;</li><li>исправить неточные данные — имя, ник и аватар меняются прямо в профиле;</li><li>отключить Google или TikTok от аккаунта в любой момент — кнопки в профиле;</li><li>удалить аккаунт вместе с проектами и файлами;</li><li>отозвать согласие на обработку;</li><li>подать жалобу в Роскомнадзор.</li></ul><p>Запрос отправьте на ${contact} с адреса или аккаунта, привязанного к профилю. Ответ — в течение 30 дней, обычно быстрее. Отзыв согласия и удаление аккаунта означают, что услуга больше не может быть оказана: проекты и сгенерированные видео будут удалены, скачайте нужное заранее.</p>
          <h2>9. Удаление данных и отключение TikTok</h2><p>Отключить TikTok можно кнопкой в профиле приложения — токены доступа удаляются немедленно; доступ можно также отозвать в настройках TikTok. Запрос на удаление остальных данных направляется на ${contact}.</p>
          <h2>10. Несовершеннолетние и безопасность</h2><p>Сервис не предназначен для лиц младше 14 лет. Оператор применяет организационные и технические меры защиты: шифрование токенов, ограничение доступа к данным и журналирование действий.</p>
          <h2>11. Изменения</h2><p>Политика может обновляться. Новая версия публикуется на этой странице и в приложении одновременно, с новой датой вступления в силу.</p>`
      },
      en: {
        title: 'Privacy and Personal Data Processing Policy',
        body: `<p>This Policy covers personal data processing on blast808.com, in the web application at ${appEn} and in the Blast Telegram bot. It is one service run by one controller: your account, materials and payments are shared no matter where you signed in.</p>
          <h2>1. Controller and scope</h2><p>Controller: ${operatorEn}. Address: ${addressEn}. Data questions, consent withdrawal and deletion requests: ${contact}, phone +7 (910) 572‑49‑67.</p><p>The Policy follows Federal Law No. 152-FZ of 27 July 2006 on Personal Data. Users in the EEA and the United Kingdom are additionally covered by section 8 of the <a href="https://app.blast808.com/legal/policy" rel="noopener">application policy</a>.</p>
          <h2>2. Data we process</h2><p>We collect only what the service needs. Blast runs no advertising or behavioural tracking.</p><ul>
            <li>landing page technical data: selected language and cookie choice, stored locally in the browser;</li>
            <li>Telegram sign-in: chat identifier (chat_id), @username if set, first and last name from the sign-up form;</li>
            <li>Google sign-in: email address and confirmation that it is yours, first and last name, profile photo link (we request the minimum scopes openid, email, profile — no access to mail, drive or contacts);</li>
            <li>service profile: artist name, avatar, selected plan, payment history and remaining limits;</li>
            <li>generation materials: track audio, lyrics, images and generation settings — and the finished videos so that you can download them;</li>
            <li>TikTok connection: account identifier (open_id), display name, avatar link and access tokens (stored encrypted);</li>
            <li>support requests, and payment status and amount — we never receive full bank card details.</li></ul>
          <h2>3. Purposes</h2><ul><li>signing you in and recognising you next time;</li><li>generating videos from your track and lyrics — the core service;</li><li>publishing a video to your TikTok account on your command and showing its statistics;</li><li>counting plan limits and processing payments;</li><li>notifying you when generation is finished;</li><li>answering support requests;</li><li>protecting the service from abuse: rate limits and detection of one TikTok account reused for free limits;</li><li>improving the product using anonymised statistics.</li></ul>
          <h2>4. Legal bases</h2><p>Performance of the contract with you (the public offer), your consent where you give it by a separate action, and our legitimate interest in protecting the service from abuse. Withdrawal does not affect processing carried out before withdrawal.</p>
          <h2>5. Cookies and local storage</h2><p>The application uses strictly necessary cookies only: a session cookie, a CSRF protection cookie, and browser local storage for the interface language and the generation wizard draft. We set no third-party advertising or tracking cookies. Landing page rules are described in the <a href="cookies.html" data-keep-language>Cookie Policy</a>.</p>
          <h2>6. Recipients</h2><p>We do not sell data and do not share it for advertising. Data is shared only with parties without whom the service cannot run:</p><ul><li>Google LLC — for Google sign-in (email verification);</li><li>TikTok — when you connect an account and publish a video: the video, description and privacy settings you chose; statistics come back;</li><li>Telegram — notification delivery and bot sign-in confirmation;</li><li>hosting and object storage — running the service and storing files, servers located in the Russian Federation;</li><li>${bankEn} — accepting and processing payments: card data is handled by the bank, not by us;</li><li>state authorities — only on a lawful and reasoned request.</li></ul><p>Publishing to TikTok is your action: nothing is sent anywhere until you press “Publish”.</p>
          <h2>7. Storage and retention</h2><p>User data is stored on servers located in the Russian Federation.</p><ul><li>account, projects and settings — for as long as your account exists;</li><li>uploaded tracks, images and finished videos — until you delete the project;</li><li>TikTok access tokens — until you disconnect TikTok in your profile, then deleted immediately;</li><li>technical logs and interface activity records — up to 12 months;</li><li>payment records — for the period required by tax and accounting law.</li></ul><p>Identifiers of TikTok accounts used to claim free limits are kept after account deletion — otherwise the “one TikTok account, one free limit” rule could be bypassed with a single click. Only the identifier is kept, with no profile content.</p>
          <h2>8. Your rights</h2><ul><li>learn what data we hold and receive a copy;</li><li>correct inaccurate data — name, artist name and avatar are editable in the profile;</li><li>disconnect Google or TikTok at any time using the profile buttons;</li><li>delete the account together with projects and files;</li><li>withdraw consent;</li><li>lodge a complaint with Roskomnadzor.</li></ul><p>Send requests to ${contact} from the address or account linked to your profile. We reply within 30 days, usually sooner. Withdrawing consent or deleting the account means the service can no longer be provided: projects and generated videos are deleted, so download what you need in advance.</p>
          <h2>9. Data deletion and TikTok disconnection</h2><p>TikTok can be disconnected with a button in the application profile — access tokens are deleted immediately; access can also be revoked in TikTok settings. Requests to delete other data go to ${contact}.</p>
          <h2>10. Minors and security</h2><p>The service is not intended for people under 14. The Controller applies organisational and technical safeguards: token encryption, restricted data access and activity logging.</p>
          <h2>11. Changes</h2><p>The Policy may be updated. A new version is published on this page and in the application at the same time, with a new effective date.</p>`
      }
    },
    terms: {
      ru: {
        title: 'Условия использования',
        body: `<p>Условия регулируют использование сайта blast808.com, веб-приложения ${appRu} и Telegram-бота Blast. Полные договорные условия — в <a href="offer.html" data-keep-language>Публичной оферте</a>, она же <a href="https://app.blast808.com/legal/offer" rel="noopener">пользовательское соглашение приложения</a>.</p>
          <h2>1. Принятие условий</h2><p>Регистрируя аккаунт, запуская генерацию или оплачивая тариф, пользователь подтверждает ознакомление с Условиями, Политикой конфиденциальности и Публичной офертой. Если пользователь не согласен, он должен прекратить использование сервиса.</p>
          <h2>2. Сервис</h2><p>Blast анализирует загруженные материалы и автоматически создаёт видеоконтент. Работа идёт в веб-приложении ${appRu}; Telegram-бот используется для входа, уведомлений и части сценариев генерации. Доступные функции, лимиты, стоимость и сроки показываются в интерфейсе до заказа.</p>
          <h2>3. Аккаунт</h2><p>Аккаунт создаётся через Telegram или Google. Один человек — один аккаунт; передача доступа третьим лицам запрещена. Пользователь отвечает за сохранность доступа к привязанным Telegram и Google.</p>
          <h2>4. Требования к пользователю</h2><p>Пользователь должен обладать необходимой дееспособностью и правами на загружаемые материалы, включая права на запись, текст, изображения, чужие голоса и лица. Запрещено загружать незаконный контент, нарушать права третьих лиц, обходить лимиты и защитные механизмы, вмешиваться в работу сервиса.</p>
          <h2>5. Пользовательские материалы и результат</h2><p>Права на исходные материалы остаются у их правообладателей. Пользователь предоставляет Оператору ограниченную лицензию хранить, обрабатывать и передавать материалы в TikTok — в объёме, необходимом для оказания услуги, и только по команде пользователя. Исключительные права на готовые ролики принадлежат пользователю; отдельного разрешения на публикацию и монетизацию не требуется. Права на сам сервис, интерфейс, шаблоны и библиотеку футажа принадлежат Оператору.</p>
          <h2>6. Сторонние платформы</h2><p>Telegram, Google и TikTok действуют по собственным правилам, Blast не является их подразделением. При подключении аккаунта пользователю показываются запрашиваемые разрешения; доступ отзывается кнопкой в профиле или в настройках соответствующей платформы.</p>
          <h2>7. Оплата и возврат</h2><p>Применяются цена и условия, показанные до оплаты, и Публичная оферта. Оплата — картами Visa, Mastercard, МИР и через T‑Pay, приём платежей осуществляет ${bankRu}. До первой генерации по оплаченному тарифу деньги возвращаются полностью; после — возвращается оплаченный и неиспользованный остаток. Подробности — в разделе 5 <a href="offer.html" data-keep-language>оферты</a>.</p>
          <h2>8. Доступность и результат</h2><p>Оператор стремится обеспечивать стабильную работу, но не гарантирует непрерывность сторонних платформ и не гарантирует конкретные показатели просмотров, охватов и продвижения.</p>
          <h2>9. Ограничение ответственности</h2><p>Ответственность определяется обязательными нормами применимого права. Ничто в Условиях не исключает ответственность, которую нельзя ограничить законом.</p>
          <h2>10. Прекращение доступа и изменения</h2><p>Доступ может быть ограничен при нарушении Условий или требований закона. Изменения публикуются на этой странице и в приложении. Оператор: ${operatorRu}; контакт: ${contact}.</p>`
      },
      en: {
        title: 'Terms of Service',
        body: `<p>These Terms govern use of blast808.com, the web application at ${appEn} and the Blast Telegram bot. Full contractual terms are in the <a href="offer.html" data-keep-language>Public Offer</a>, which is also the <a href="https://app.blast808.com/legal/offer" rel="noopener">application user agreement</a>.</p>
          <h2>1. Acceptance</h2><p>By registering an account, starting a generation or paying for a plan, the user accepts these Terms, the Privacy Policy and the Public Offer. Users who disagree must stop using the service.</p>
          <h2>2. Service</h2><p>Blast analyses submitted materials and automatically creates video content. Work happens in the web application at ${appEn}; the Telegram bot is used for sign-in, notifications and part of the generation flows. Available functions, limits, prices and timing are shown in the interface before ordering.</p>
          <h2>3. Account</h2><p>Accounts are created through Telegram or Google. One person, one account; sharing access with third parties is prohibited. The user is responsible for keeping the linked Telegram and Google accounts secure.</p>
          <h2>4. User requirements</h2><p>Users must have legal capacity and all rights to uploaded materials, including rights to the recording, lyrics, images and to any third-party voices or faces. Illegal content, infringement, circumvention of limits and safeguards, and interference with the service are prohibited.</p>
          <h2>5. User materials and results</h2><p>Rights in source materials stay with their owners. The user grants the Controller a limited licence to store, process and transfer materials to TikTok — only to the extent needed to provide the service and only on the user's command. Exclusive rights to finished videos belong to the user; no separate permission is needed to publish or monetise them. Rights to the service itself, its interface, templates and footage library belong to the Controller.</p>
          <h2>6. Third-party platforms</h2><p>Telegram, Google and TikTok operate under their own terms; Blast is not affiliated with them. Requested permissions are shown before an account is connected; access is revoked with a profile button or in the platform's own settings.</p>
          <h2>7. Payments and refunds</h2><p>The price and conditions shown before payment apply, together with the Public Offer. Payment is by Visa, Mastercard, MIR cards and T‑Pay; payments are accepted by ${bankEn}. Before the first generation on a paid plan a full refund is available; afterwards the paid and unused remainder is refunded. Details are in section 5 of the <a href="offer.html" data-keep-language>offer</a>.</p>
          <h2>8. Availability and outcomes</h2><p>The Controller aims to keep the service running but does not guarantee uninterrupted third-party platforms or particular view, reach or promotion outcomes.</p>
          <h2>9. Liability</h2><p>Liability is governed by mandatory applicable law. Nothing excludes liability that cannot legally be limited.</p>
          <h2>10. Suspension and changes</h2><p>Access may be restricted for violations or legal compliance. Updates are published on this page and in the application. Controller: ${operatorEn}; contact: ${contact}.</p>`
      }
    },
    cookies: {
      ru: {
        title: 'Политика cookie',
        body: `<p>Политика объясняет локальное хранение данных на лендинге blast808.com и в веб-приложении ${appRu}.</p>
          <h2>1. Лендинг</h2><p>Сайт сохраняет в localStorage выбранный язык (<code>blast_language</code>) и версионированное решение о cookie (<code>blast_cookie_consent</code>). Эти значения не отправляются третьим лицам и нужны только для сохранения настроек.</p>
          <h2>2. Веб-приложение</h2><p>Приложение использует технически необходимые cookie: cookie сессии (вы остаётесь в аккаунте между страницами) и cookie защиты от подделки запросов (CSRF-токен). В localStorage хранятся язык интерфейса и черновик мастера генерации. Отключить необходимые cookie нельзя — без них вход не работает.</p>
          <h2>3. Категории и выбор</h2><ul><li><strong>Необходимые:</strong> язык, состояние согласия, сессия и базовые функции;</li><li><strong>Аналитические:</strong> измерение использования сайта;</li><li><strong>Маркетинговые:</strong> измерение рекламы и кампаний.</li></ul><p>Рекламных и трекинговых cookie сторонних сетей ни на лендинге, ни в приложении сейчас нет. Если они появятся, модуль согласия не запустит их до разрешения соответствующей категории. Принять всё, отклонить необязательное или настроить категории раздельно можно через ссылку «Настроить cookie» в footer главной страницы.</p>
          <h2>4. Управление браузером</h2><p>Удаление данных сайта в настройках браузера сбросит сохранённый выбор. Блокировка localStorage может помешать сохранению языка и решения по cookie.</p>
          <h2>5. Контакт</h2><p>Вопросы — на ${contact}.</p>`
      },
      en: {
        title: 'Cookie Policy',
        body: `<p>This Policy explains local storage on the blast808.com landing page and in the web application at ${appEn}.</p>
          <h2>1. Landing page</h2><p>The site stores the selected language (<code>blast_language</code>) and the versioned consent choice (<code>blast_cookie_consent</code>) in localStorage. These values are not sent to third parties and only preserve your settings.</p>
          <h2>2. Web application</h2><p>The application uses strictly necessary cookies: a session cookie (keeping you signed in between pages) and a CSRF protection cookie. Local storage holds the interface language and the generation wizard draft. Necessary cookies cannot be disabled — sign-in does not work without them.</p>
          <h2>3. Categories and choices</h2><ul><li><strong>Necessary:</strong> language, consent state, session and core functions;</li><li><strong>Analytics:</strong> site usage measurement;</li><li><strong>Marketing:</strong> advertising and campaign measurement.</li></ul><p>Neither the landing page nor the application currently sets third-party advertising or tracking cookies. If they are introduced, the consent module will not activate them until the relevant category is allowed. You can accept all, reject optional categories or configure them separately using “Cookie settings” in the home page footer.</p>
          <h2>4. Browser controls</h2><p>Clearing site data resets the saved choice. Blocking localStorage may prevent the language and cookie choices from being saved.</p>
          <h2>5. Contact</h2><p>Questions: ${contact}.</p>`
      }
    },
    consent: {
      ru: {
        title: 'Согласие на обработку персональных данных',
        body: `<p>Текст согласия, которое пользователь даёт отдельным действием при регистрации в веб-приложении ${appRu} или при первом обращении к Telegram-боту Blast.</p>
          <h2>1. Кому предоставляется согласие</h2><p>${operatorRu}, адрес: ${addressRu}.</p>
          <h2>2. Данные и цели</h2><p>Пользователь соглашается на обработку данных аккаунта (Telegram chat_id и @username либо адрес электронной почты и профиль Google), ника и аватара, загруженных аудио, текстов и изображений, настроек и результатов генерации, идентификатора и токенов подключённого аккаунта TikTok, технических журналов и платёжных метаданных — для оказания услуги, публикации роликов по команде пользователя, поддержки, расчётов, защиты сервиса и исполнения требований закона.</p>
          <h2>3. Действия с данными</h2><p>Согласие охватывает сбор, запись, систематизацию, накопление, хранение, уточнение, извлечение, использование, передачу получателям, перечисленным в разделе 6 <a href="privacy.html" data-keep-language>Политики конфиденциальности</a>, блокирование, удаление и уничтожение — в пределах заявленных целей.</p>
          <h2>4. Срок и отзыв</h2><p>Согласие действует до достижения целей или его отзыва, если дальнейшая обработка не требуется по закону или договору. Отзыв направляется на ${contact}; отключить TikTok или Google и удалить аккаунт можно кнопками в профиле приложения. Отзыв согласия означает прекращение оказания услуги и удаление проектов и роликов.</p>
          <h2>5. Фиксация согласия</h2><p>Согласие даётся непредустановленным действием (отдельный чекбокс при регистрации либо подтверждение в боте); сервис сохраняет версию документа и время согласия.</p>`
      },
      en: {
        title: 'Consent to Personal Data Processing',
        body: `<p>The consent a user gives by a separate action when registering in the web application at ${appEn} or on first contact with the Blast Telegram bot.</p>
          <h2>1. Controller</h2><p>${operatorEn}, address: ${addressEn}.</p>
          <h2>2. Data and purposes</h2><p>The user consents to processing of account data (Telegram chat_id and @username, or email address and Google profile), artist name and avatar, uploaded audio, lyrics and images, generation settings and results, the identifier and access tokens of a connected TikTok account, technical logs and payment metadata — to provide the service, publish videos on the user's command, support users, process payments, protect the service and comply with law.</p>
          <h2>3. Processing operations</h2><p>Consent covers collection, recording, organisation, accumulation, storage, updating, retrieval, use, disclosure to the recipients listed in section 6 of the <a href="privacy.html" data-keep-language>Privacy Policy</a>, restriction, deletion and destruction, within the stated purposes.</p>
          <h2>4. Duration and withdrawal</h2><p>Consent lasts until its purposes are achieved or it is withdrawn, unless continued processing is required by law or contract. Withdrawal requests go to ${contact}; TikTok and Google can be disconnected and the account deleted using profile buttons. Withdrawal means the service stops and projects and videos are deleted.</p>
          <h2>5. Recording consent</h2><p>Consent is given by an unticked, separate action (a dedicated checkbox at registration or a confirmation in the bot); the service records the document version and the time of consent.</p>`
      }
    },
    offer: {
      ru: {
        title: 'Публичная оферта на оказание услуг',
        body: `<p>${operatorRu}, адрес: ${addressRu}, предлагает заключить договор оказания услуг по автоматизированной генерации видеоконтента. Тот же текст опубликован в приложении как <a href="https://app.blast808.com/legal/offer" rel="noopener">пользовательское соглашение</a>.</p>
          <h2>1. Предмет</h2><p>Заказчик загружает аудиоматериал, текст и настройки в веб-приложении ${appRu} или через Telegram-бот. Исполнитель анализирует материалы и передаёт сгенерированные видеоролики в электронной форме — в личном кабинете и, по команде Заказчика, публикацией в его аккаунте TikTok.</p>
          <h2>2. Акцепт</h2><p>Акцептом является регистрация аккаунта и запуск генерации либо оплата тарифа после ознакомления с условиями.</p>
          <h2>3. Бесплатный доступ и лимиты</h2><p>Новый Заказчик получает ограниченное число бесплатных роликов; точное число указано на странице тарифов и может меняться. Расширенный лимит в рамках одного трека открывается подключением аккаунта TikTok. Каждый аккаунт TikTok даёт бесплатный лимит только один раз: повторное использование того же аккаунта — в том числе на другом или удалённом аккаунте сервиса — считается попыткой получить бесплатный доступ повторно и влечёт ограничение доступа.</p>
          <h2>4. Тарифы</h2><p>Актуальные составы и цены — на странице тарифов сервиса. На момент этой редакции:</p><ul>
            <li>«Blast» — 1 990 ₽ в месяц, подписка: 100 роликов, до 4 треков, каждый третий месяц без лимита роликов;</li>
            <li>«Glow» — 7 990 ₽, разовая покупка: 400 роликов, до 10 треков, шаблон CapCut под ваш трек;</li>
            <li>«Impulse» — 29 990 ₽, разовая покупка на год: ролики без лимита, до 24 треков, персональный менеджмент релиза;</li>
            <li>бесплатный доступ — ограниченное число роликов на один трек, расширяется подключением аккаунта TikTok.</li></ul><p>Цена может меняться, но не для уже оплаченного периода.</p>
          <h2>5. Оплата и возврат</h2><p>Оплата проводится дистанционно картами Visa, Mastercard, МИР и через сервис T‑Pay; приём платежей осуществляет ${bankRu}. Данные карты вводятся на защищённой странице банка и к Исполнителю не попадают. Момент оплаты — зачисление денег на счёт Исполнителя; доступ по тарифу открывается автоматически после подтверждения оплаты. Подписка продлевается на следующий период, если её не отменили; отмена доступна в профиле, доступ сохраняется до конца оплаченного периода.</p><p>Возврат: до первой генерации по оплаченному тарифу деньги возвращаются полностью. После — услуга считается оказанной по каждому уже сгенерированному ролику, поэтому возвращается оплаченный и неиспользованный остаток: неизрасходованные ролики и неистёкший период за вычетом стоимости выполненных генераций (ст. 32 Закона РФ «О защите прав потребителей»). Заявление — на ${contact}, срок рассмотрения 10 рабочих дней. Если генерация не удалась по вине Исполнителя, потраченный лимит возвращается автоматически, а при невозможности оказать услугу возвращаются деньги.</p>
          <h2>6. Права на материалы и результат</h2><p>Материалы остаются собственностью Заказчика; он подтверждает наличие всех прав на них. Исполнителю предоставляется ограниченная лицензия хранить, обрабатывать и передавать материалы в TikTok в объёме, необходимом для оказания услуги. Исключительные права на готовые ролики принадлежат Заказчику. Исполнитель не использует материалы и результаты в рекламе без отдельного письменного согласия Заказчика.</p>
          <h2>7. Ответственность</h2><p>Исполнитель не гарантирует конкретных показателей просмотров, охватов и продвижения и не отвечает за работу сторонних платформ. Ответственность ограничивается обязательными нормами применимого права.</p>
          <h2>8. Реквизиты</h2><p>Исполнитель: ${operatorRu}. Адрес: ${addressRu}. Контакт: ${contact}, телефон ${phoneRu}.</p>`
      },
      en: {
        title: 'Public Offer for Services',
        body: `<p>${operatorEn}, address: ${addressEn}, offers to conclude a contract for automated video content generation services. The same text is published in the application as the <a href="https://app.blast808.com/legal/offer" rel="noopener">user agreement</a>.</p>
          <h2>1. Subject</h2><p>The Customer uploads audio, lyrics and settings in the web application at ${appEn} or through the Telegram bot. The Contractor analyses the materials and delivers generated videos electronically — in the account and, on the Customer's command, by publishing to their TikTok account.</p>
          <h2>2. Acceptance</h2><p>Acceptance is registering an account and starting a generation, or paying for a plan after reviewing these terms.</p>
          <h2>3. Free access and limits</h2><p>A new Customer receives a limited number of free videos; the exact number is shown on the pricing page and may change. An extended limit within a single track is unlocked by connecting a TikTok account. Each TikTok account grants a free limit only once: reusing the same account — including on another or a deleted service account — counts as claiming free access twice and leads to access restrictions.</p>
          <h2>4. Plans</h2><p>Current contents and prices are on the service pricing page. As of this revision:</p><ul>
            <li>“Blast” — RUB 1,990 per month, subscription: 100 videos, up to 4 tracks, every third month without a video limit;</li>
            <li>“Glow” — RUB 7,990, one-off: 400 videos, up to 10 tracks, a CapCut template for your track;</li>
            <li>“Impulse” — RUB 29,990, one-off for a year: unlimited videos, up to 24 tracks, personal release management;</li>
            <li>free access — a limited number of videos for one track, extended by connecting a TikTok account.</li></ul><p>Prices may change, but not for a period already paid for.</p>
          <h2>5. Payment and refunds</h2><p>Payment is made remotely by Visa, Mastercard and MIR cards and through T‑Pay; payments are accepted by ${bankEn}. Card details are entered on the bank's secure page and never reach the Contractor. Payment occurs when funds are credited to the Contractor's account; plan access opens automatically once payment is confirmed. A subscription renews for the next period unless cancelled; cancellation is available in the profile and access remains until the end of the paid period.</p><p>Refunds: before the first generation on a paid plan the payment is refunded in full. Afterwards the service counts as rendered for each generated video, so the paid and unused remainder is refunded: unused videos and the unexpired period minus the cost of completed generations (Article 32 of the Russian Consumer Protection Act). Send requests to ${contact}; they are reviewed within 10 business days. If a generation fails through the Contractor's fault the spent limit is restored automatically, and where the service cannot be provided the payment is returned.</p>
          <h2>6. Rights to materials and results</h2><p>Materials remain the Customer's property, and the Customer warrants holding all rights to them. The Contractor receives a limited licence to store, process and transfer materials to TikTok to the extent needed to provide the service. Exclusive rights to finished videos belong to the Customer. The Contractor does not use materials or results in advertising without the Customer's separate written consent.</p>
          <h2>7. Liability</h2><p>The Contractor does not guarantee particular view, reach or promotion figures and is not responsible for third-party platforms. Liability is limited by mandatory applicable law.</p>
          <h2>8. Details</h2><p>Contractor: ${operatorEn}. Address: ${addressEn}. Contact: ${contact}, phone +7 (910) 572‑49‑67.</p>`
      }
    },
    contacts: {
      ru: {
        title: 'Контакты и реквизиты',
        body: `<h2>Служба поддержки</h2><p>Email: ${contact}<br>Телефон: ${phoneRu}<br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a></p>
          <h2>Оператор и исполнитель</h2><p>${operatorRu}<br>Адрес: ${addressRu}</p>
          <h2>Сервис</h2><p>Лендинг: blast808.com<br>Веб-приложение: ${appRu}<br>Telegram-бот: <a href="https://t.me/blast808bot" target="_blank" rel="noopener">@blast808bot</a></p>
          <h2>Платёжный партнёр</h2><p>${bankRu}. Приём платежей — карты Visa, Mastercard, МИР и сервис T‑Pay.</p>`
      },
      en: {
        title: 'Contacts and Legal Details',
        body: `<h2>Support</h2><p>Email: ${contact}<br>Phone: <a href="tel:+79105724967">+7 (910) 572‑49‑67</a><br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a></p>
          <h2>Controller and contractor</h2><p>${operatorEn}<br>Address: ${addressEn}</p>
          <h2>Service</h2><p>Landing page: blast808.com<br>Web application: ${appEn}<br>Telegram bot: <a href="https://t.me/blast808bot" target="_blank" rel="noopener">@blast808bot</a></p>
          <h2>Payment partner</h2><p>${bankEn}. Payments are accepted by Visa, Mastercard and MIR cards and through T‑Pay.</p>`
      }
    }
  };

  const content = document.querySelector('[data-legal-content]');
  const key = document.body.dataset.legalDocument;
  if (!content || !documents[key]) throw new Error(`[landing] unknown legal document: ${key}`);

  function render() {
    const language = window.BLAST_I18N?.getLanguage() || 'en';
    const documentData = documents[key][language] || documents[key].en;
    const effective = language === 'ru' ? EFFECTIVE_RU : EFFECTIVE_EN;
    const versionLabel = language === 'ru' ? 'Версия' : 'Version';
    const dateLabel = language === 'ru' ? 'Дата вступления в силу' : 'Effective date';
    content.innerHTML = `<h1>${documentData.title}</h1><div class="legal-meta"><span>${versionLabel}: ${VERSION}</span><span>${dateLabel}: ${effective}</span></div>${documentData.body}`;
  }

  document.addEventListener('blast:languagechange', render);
  render();
})();
