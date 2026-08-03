(() => {
  'use strict';

  const VERSION = '1.0';
  const EFFECTIVE_RU = '3 августа 2026 г.';
  const EFFECTIVE_EN = '3 August 2026';
  const operatorRu = 'Индивидуальный предприниматель Чернов Никита Романович, ИНН 623013205426, ОГРНИП 324620000005644';
  const operatorEn = 'Individual Entrepreneur Nikita Romanovich Chernov, Tax ID (INN) 623013205426, State Registration Number (OGRNIP) 324620000005644';
  const addressRu = '390048, Россия, Рязанская обл., г. Рязань, ул. Васильевская, д. 18, кв. 60';
  const addressEn = '18 Vasilievskaya St., Apt. 60, Ryazan, Ryazan Region, 390048, Russia';
  const contact = '<a href="mailto:support@blast808.com">support@blast808.com</a>';
  const phoneRu = '<a href="tel:+79105724967">+7 (910) 572-49-67</a>';
  const botLink = '<a href="https://t.me/blast808bot" target="_blank" rel="noopener">@blast808bot</a>';

  const documents = {
    privacy: {
      ru: {
        title: 'Политика конфиденциальности и обработки персональных данных',
        body: `<p>Настоящая Политика определяет порядок обработки и защиты персональных данных пользователей сайта blast808.com и сервиса Blast, доступного через Telegram-бот ${botLink} (далее — «Сервис»), и разработана в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ «О персональных данных» (далее — «Закон № 152-ФЗ»).</p>

          <h2>1. Оператор</h2>
          <p>Оператор персональных данных: ${operatorRu} (далее — «Оператор»).</p>
          <p>Адрес: ${addressRu}. Электронная почта по вопросам обработки персональных данных: ${contact}. Телефон: ${phoneRu}.</p>
          <p>Лицом, ответственным за организацию обработки персональных данных, является Оператор. Запросы субъектов персональных данных рассматриваются по адресу ${contact}.</p>

          <h2>2. Область действия</h2>
          <p>2.1. Политика распространяется на обработку данных, получаемых через сайт blast808.com, через Telegram-бот Сервиса, через каналы поддержки, а также через аккаунты сторонних платформ (например, TikTok), если пользователь добровольно подключает их к Сервису.</p>
          <p>2.2. Политика не распространяется на сайты и сервисы третьих лиц, ссылки на которые могут размещаться в Сервисе. Обработка данных такими лицами регулируется их собственными политиками.</p>

          <h2>3. Категории субъектов и обрабатываемые данные</h2>
          <p>3.1. Оператор обрабатывает данные посетителей сайта, пользователей Telegram-бота и заказчиков платных услуг.</p>
          <p>3.2. Обрабатываются следующие категории данных:</p>
          <ul>
            <li><strong>Данные сайта:</strong> выбранный язык интерфейса и решение по cookie, сохраняемые локально в браузере пользователя; технические данные соединения (IP-адрес, тип и версия браузера, время обращения), фиксируемые в журналах веб-сервера.</li>
            <li><strong>Данные Telegram:</strong> числовой идентификатор пользователя (Telegram ID), имя пользователя (username), имя и фамилия и иные общедоступные данные профиля, передаваемые Telegram при обращении к боту.</li>
            <li><strong>Пользовательские материалы:</strong> аудиозаписи (музыкальные треки), тексты песен, изображения, параметры и пожелания, добровольно направленные пользователем для генерации контента.</li>
            <li><strong>Данные заказа:</strong> история обращений, состав и статус заказа, сгенерированные видеоматериалы, служебные технические журналы выполнения заказа.</li>
            <li><strong>Данные обращений:</strong> контактные данные и содержание переписки при обращении в поддержку.</li>
            <li><strong>Платёжные данные:</strong> идентификатор платежа, сумма, дата, статус и способ оплаты, признак наличия привязанной карты, адрес электронной почты для направления кассового чека. <strong>Оператор не получает и не хранит номер банковской карты, срок её действия и код CVV/CVC.</strong></li>
            <li><strong>Данные подключённых платформ:</strong> сведения, получаемые от TikTok или иной платформы, если пользователь самостоятельно подключил аккаунт (раздел 11 настоящей Политики).</li>
          </ul>
          <p>3.3. Оператор не обрабатывает специальные категории персональных данных (о состоянии здоровья, расовой и национальной принадлежности, политических и религиозных убеждениях) и не обрабатывает биометрические персональные данные. Голос, содержащийся в аудиоматериалах, не используется Оператором для установления личности.</p>
          <p>3.4. Пользователь не должен направлять в Сервис персональные данные третьих лиц без наличия правовых оснований.</p>

          <h2>4. Цели и правовые основания обработки</h2>
          <ul>
            <li><strong>Оказание услуг</strong> (приём материалов, генерация и передача контента, доступ к функциям бота) — исполнение договора, стороной которого является субъект персональных данных (п. 5 ч. 1 ст. 6 Закона № 152-ФЗ).</li>
            <li><strong>Идентификация пользователя и учёт заказов</strong> — исполнение договора (п. 5 ч. 1 ст. 6).</li>
            <li><strong>Обработка оплаты, возвратов, оформление кассовых чеков и ведение учёта</strong> — исполнение договора и исполнение обязанностей, возложенных на Оператора законодательством (п. 2, п. 5 ч. 1 ст. 6; Федеральный закон от 22.05.2003 № 54-ФЗ; Налоговый кодекс РФ).</li>
            <li><strong>Поддержка пользователей и рассмотрение обращений</strong> — исполнение договора и согласие пользователя (п. 1, п. 5 ч. 1 ст. 6).</li>
            <li><strong>Обеспечение безопасности Сервиса, предотвращение злоупотреблений и мошенничества</strong> — исполнение обязанностей и защита законных интересов Оператора (п. 2, п. 7 ч. 1 ст. 6).</li>
            <li><strong>Публикация контента в подключённом аккаунте сторонней платформы</strong> — согласие пользователя, выраженное подключением аккаунта и подтверждением публикации (п. 1 ч. 1 ст. 6).</li>
            <li><strong>Информационные и маркетинговые рассылки, веб-аналитика</strong> — исключительно на основании отдельного, предварительно полученного согласия (п. 1 ч. 1 ст. 6; ч. 1 ст. 18 Федерального закона от 13.03.2006 № 38-ФЗ). На дату настоящей версии инструменты веб-аналитики и рекламные сценарии в Сервисе не подключены.</li>
          </ul>

          <h2>5. Способы и перечень действий с персональными данными</h2>
          <p>5.1. Обработка осуществляется с использованием средств автоматизации и без их использования и включает: сбор, запись, систематизацию, накопление, хранение, уточнение (обновление, изменение), извлечение, использование, передачу (предоставление, доступ), обезличивание, блокирование, удаление и уничтожение персональных данных.</p>
          <p>5.2. Оператор не принимает решений, порождающих юридические последствия в отношении пользователя, исключительно на основании автоматизированной обработки персональных данных.</p>
          <p>5.3. Пользовательские материалы не используются Оператором для обучения собственных моделей машинного обучения. Привлекаемые поставщики технологий обрабатывают материалы по поручению Оператора на условиях корпоративных (платных) программных интерфейсов, исключающих использование переданных данных для обучения моделей поставщика.</p>

          <h2>6. Место обработки и хранения. Локализация</h2>
          <p>6.1. Запись, систематизация, накопление, хранение, уточнение и извлечение персональных данных граждан Российской Федерации осуществляются с использованием баз данных, расположенных на территории Российской Федерации (ч. 5 ст. 18 Закона № 152-ФЗ). Серверная инфраструктура Сервиса размещена в дата-центрах российского провайдера облачной инфраструктуры.</p>
          <p>6.2. Хранение резервных копий осуществляется в той же инфраструктуре с ограничением доступа.</p>

          <h2>7. Передача данных третьим лицам</h2>
          <p>7.1. Оператор не продаёт персональные данные и не передаёт их третьим лицам для их собственных целей. Передача осуществляется только в объёме, необходимом для оказания услуг, и только следующим категориям получателей:</p>
          <ul>
            <li><strong>Telegram</strong> — платформа доставки сообщений и файлов, через которую работает Сервис;</li>
            <li><strong>АО «Т-Банк»</strong> — приём платежей, возвраты, формирование и направление кассовых чеков;</li>
            <li><strong>Российский провайдер облачной инфраструктуры</strong> — размещение серверов, объектное хранилище и резервное копирование;</li>
            <li><strong>Поставщики технологий генеративного искусственного интеллекта</strong> (в том числе Google LLC — Gemini API, и агрегатор доступа к моделям OpenRouter) — автоматическая расшифровка аудио, разметка субтитров и подбор визуального ряда;</li>
            <li><strong>TikTok и иные подключённые пользователем платформы</strong> — только при добровольном подключении аккаунта и только в объёме, необходимом для запрошенного пользователем действия;</li>
            <li><strong>Государственные органы</strong> — по мотивированным запросам в случаях и порядке, установленных законодательством Российской Федерации.</li>
          </ul>
          <p>7.2. Передача обработчикам осуществляется на основании договоров, содержащих обязанность соблюдать конфиденциальность и требования к безопасности персональных данных (ч. 3 ст. 6 Закона № 152-ФЗ).</p>

          <h2>8. Трансграничная передача</h2>
          <p>8.1. При использовании технологий генеративного искусственного интеллекта и платформы Telegram отдельные операции обработки могут осуществляться на территории иностранных государств. Такая передача является трансграничной в значении ст. 12 Закона № 152-ФЗ и осуществляется с уведомлением уполномоченного органа по защите прав субъектов персональных данных в установленном порядке.</p>
          <p>8.2. Объём трансграничной передачи ограничен пользовательскими материалами и техническими метаданными, необходимыми для выполнения заказа. Идентификаторы пользователя, платёжные и учётные данные за пределы Российской Федерации Оператором не передаются.</p>
          <p>8.3. Пользователь, не желающий трансграничной передачи своих материалов, вправе отказаться от использования Сервиса; оказание услуг без такой передачи технически невозможно.</p>

          <h2>9. Сроки обработки и хранения</h2>
          <ul>
            <li>Идентификатор и данные профиля Telegram, история заказов — в течение срока использования Сервиса и 12 месяцев после последнего обращения пользователя.</li>
            <li>Исходные пользовательские материалы (аудио, тексты, изображения) — до 90 дней с даты выполнения заказа.</li>
            <li>Сгенерированные видеоматериалы — до 90 дней с даты передачи результата пользователю.</li>
            <li>Технические журналы выполнения заказов и журналы безопасности — до 12 месяцев.</li>
            <li>Переписка с поддержкой — до 12 месяцев с даты закрытия обращения.</li>
            <li>Данные о платежах и документы учёта — 5 лет (ст. 29 Федерального закона от 06.12.2011 № 402-ФЗ, ст. 23 Налогового кодекса РФ).</li>
            <li>Сведения о факте, версии и времени предоставления и отзыва согласия — в течение срока действия согласия и 3 лет после его отзыва.</li>
            <li>Резервные копии — до 30 дней, после чего перезаписываются.</li>
          </ul>
          <p>9.1. По достижении целей обработки, а также при отзыве согласия персональные данные подлежат уничтожению или обезличиванию в срок, не превышающий 30 дней, если иной срок не установлен законодательством или договором (ст. 21 Закона № 152-ФЗ). Сведения, подлежащие обязательному хранению по закону, сохраняются в течение установленного законом срока.</p>

          <h2>10. Права пользователя и порядок их реализации</h2>
          <p>10.1. Пользователь вправе получать сведения об обработке своих персональных данных, требовать их уточнения, блокирования или уничтожения в случае, если они являются неполными, устаревшими, неточными, незаконно полученными или не являются необходимыми для заявленной цели обработки, отзывать согласие, а также обжаловать действия Оператора.</p>
          <p>10.2. Запрос направляется на ${contact} с указанием сведений, позволяющих идентифицировать пользователя (в том числе Telegram ID или username, использованных при обращении к Сервису). Оператор вправе запросить дополнительное подтверждение личности, если это необходимо для защиты данных.</p>
          <p>10.3. Сведения предоставляются в течение 10 рабочих дней с даты получения запроса. Срок может быть продлён не более чем на 5 рабочих дней с уведомлением пользователя о причинах продления (ст. 20 Закона № 152-ФЗ).</p>
          <p>10.4. При выявлении неправомерной обработки Оператор блокирует данные на период проверки в срок не более 3 рабочих дней и устраняет нарушение либо уничтожает данные в установленные законом сроки.</p>
          <p>10.5. Отзыв согласия направляется на ${contact}. Отзыв не влияет на правомерность обработки, осуществлённой до его получения, и не прекращает обработку, осуществляемую на иных законных основаниях (в том числе для исполнения договора и обязанностей по закону).</p>
          <p>10.6. Пользователь вправе обжаловать действия Оператора в Федеральную службу по надзору в сфере связи, информационных технологий и массовых коммуникаций (Роскомнадзор) или в судебном порядке.</p>

          <h2>11. Данные, получаемые через TikTok</h2>
          <p>11.1. Подключение аккаунта TikTok к Сервису является добровольным и не требуется для генерации контента. Сервис не является продуктом TikTok, не аффилирован с TikTok и не действует от его имени.</p>
          <p>11.2. При подключении аккаунта пользователю до предоставления доступа отображается перечень запрашиваемых разрешений. Оператор запрашивает минимально необходимый набор разрешений и может получать: обезличенный идентификатор аккаунта (open_id, union_id), отображаемое имя и аватар профиля, а при наличии соответствующего разрешения — перечень размещённых пользователем видео и агрегированную статистику по ним, а также технические маркеры доступа (токены).</p>
          <p>11.3. Полученные от TikTok данные используются исключительно для: отображения подключённого аккаунта в интерфейсе Сервиса; публикации контента в аккаунт пользователя по его прямой команде и после предварительного просмотра; отображения пользователю статистики его собственных публикаций.</p>
          <p>11.4. Данные, полученные от TikTok, <strong>не продаются, не передаются третьим лицам, не используются для рекламы, профилирования, скоринга и обучения моделей машинного обучения</strong>. Публикация в аккаунт пользователя невозможна без его явного подтверждения в интерфейсе Сервиса.</p>
          <p>11.5. Токены доступа хранятся в зашифрованном виде и удаляются при отзыве доступа, при отключении аккаунта или по истечении 12 месяцев без обращений пользователя, в зависимости от того, что наступит раньше.</p>
          <p>11.6. <strong>Отключение и удаление данных.</strong> Пользователь может в любой момент: (а) отключить аккаунт в интерфейсе Сервиса; (б) отозвать доступ в настройках TikTok в разделе управления разрешениями приложений; (в) направить запрос на удаление данных на ${contact}. При отзыве доступа или получении запроса Оператор прекращает обращение к TikTok API и удаляет полученные от TikTok данные и токены в срок не более 30 дней, за исключением сведений, хранение которых обязательно в силу закона. О результатах удаления пользователь уведомляется по указанному им адресу.</p>

          <h2>12. Безопасность</h2>
          <p>12.1. Оператор принимает правовые, организационные и технические меры для защиты персональных данных от неправомерного или случайного доступа, уничтожения, изменения, блокирования, копирования, предоставления и распространения (ст. 18.1, 19 Закона № 152-ФЗ), в том числе: передачу данных по защищённому протоколу HTTPS/TLS; разграничение и минимизацию прав доступа; хранение секретов и токенов в зашифрованном виде; ведение журналов доступа; резервное копирование; регулярный контроль состава обработчиков.</p>
          <p>12.2. Оператор не получает и не хранит реквизиты банковских карт. Приём платежей осуществляется АО «Т-Банк» на защищённой платёжной странице банка; платёжная инфраструктура банка сертифицирована по стандарту PCI DSS.</p>
          <p>12.3. При выявлении инцидента, повлёкшего неправомерную передачу персональных данных, Оператор уведомляет уполномоченный орган в сроки, установленные ч. 3.1 ст. 21 Закона № 152-ФЗ (в течение 24 часов о факте инцидента и в течение 72 часов о результатах внутреннего расследования).</p>

          <h2>13. Cookie и локальное хранение</h2>
          <p>13.1. Порядок использования cookie и иных технологий локального хранения описан в <a href="cookies.html" data-keep-language>Политике cookie</a>.</p>

          <h2>14. Возрастные ограничения</h2>
          <p>14.1. Сервис предназначен для лиц, достигших 18 лет. Лица в возрасте от 14 до 18 лет вправе пользоваться Сервисом с согласия законного представителя. Оператор не осуществляет целенаправленный сбор данных лиц младше 14 лет; при выявлении таких данных они удаляются.</p>

          <h2>15. Изменения Политики</h2>
          <p>15.1. Оператор вправе изменять настоящую Политику. Актуальная редакция с указанием версии и даты вступления в силу публикуется на этой странице. Продолжение использования Сервиса после вступления изменений в силу означает согласие с новой редакцией.</p>

          <h2>16. Контакты</h2>
          <p>${operatorRu}<br>Адрес: ${addressRu}<br>E-mail: ${contact}<br>Телефон: ${phoneRu}</p>`
      },
      en: {
        title: 'Privacy and Personal Data Processing Policy',
        body: `<p>This Policy sets out how personal data of users of the blast808.com website and of the Blast service available through the Telegram bot ${botLink} (the "Service") is processed and protected. It is prepared in accordance with Russian Federal Law No. 152-FZ of 27 July 2006 "On Personal Data" ("Law No. 152-FZ").</p>

          <h2>1. Controller</h2>
          <p>Data controller (operator): ${operatorEn} (the "Controller").</p>
          <p>Address: ${addressEn}. Data protection contact: ${contact}. Phone: +7 (910) 572-49-67.</p>
          <p>The Controller is the person responsible for organizing personal data processing. Data subject requests are handled at ${contact}.</p>

          <h2>2. Scope</h2>
          <p>2.1. This Policy covers data received through blast808.com, through the Service's Telegram bot, through support channels, and through third-party platform accounts (for example TikTok) that a user voluntarily connects to the Service.</p>
          <p>2.2. This Policy does not cover third-party websites or services linked from the Service. Their processing is governed by their own policies.</p>

          <h2>3. Categories of data subjects and data processed</h2>
          <p>3.1. The Controller processes data of website visitors, Telegram bot users and customers of paid services.</p>
          <p>3.2. The following categories are processed:</p>
          <ul>
            <li><strong>Website data:</strong> selected interface language and cookie choice stored locally in the browser; technical connection data (IP address, browser type and version, request time) recorded in web server logs.</li>
            <li><strong>Telegram data:</strong> numeric user identifier (Telegram ID), username, first and last name and other public profile data supplied by Telegram when the user contacts the bot.</li>
            <li><strong>User materials:</strong> audio recordings (music tracks), lyrics, images, settings and preferences voluntarily submitted for content generation.</li>
            <li><strong>Order data:</strong> request history, order contents and status, generated video files, and technical execution logs.</li>
            <li><strong>Support data:</strong> contact details and correspondence submitted to support.</li>
            <li><strong>Payment data:</strong> payment identifier, amount, date, status and payment method, an indicator of whether a card is linked, and the e-mail address used to deliver the fiscal receipt. <strong>The Controller does not receive or store card numbers, expiry dates or CVV/CVC codes.</strong></li>
            <li><strong>Connected platform data:</strong> information received from TikTok or another platform if the user has connected an account (Section 11).</li>
          </ul>
          <p>3.3. The Controller does not process special categories of personal data (health, racial or ethnic origin, political or religious beliefs) and does not process biometric personal data. Any voice contained in submitted audio is not used by the Controller to identify individuals.</p>
          <p>3.4. Users must not submit personal data of third parties to the Service without a lawful basis.</p>

          <h2>4. Purposes and legal bases</h2>
          <ul>
            <li><strong>Providing the service</strong> (receiving materials, generating and delivering content, access to bot features) — performance of a contract to which the data subject is a party (Art. 6(1)(5) of Law No. 152-FZ).</li>
            <li><strong>User identification and order records</strong> — performance of a contract (Art. 6(1)(5)).</li>
            <li><strong>Payments, refunds, fiscal receipts and accounting</strong> — performance of a contract and compliance with statutory obligations (Art. 6(1)(2) and (5); Federal Law No. 54-FZ of 22 May 2003; Russian Tax Code).</li>
            <li><strong>User support and handling of requests</strong> — performance of a contract and consent (Art. 6(1)(1) and (5)).</li>
            <li><strong>Security of the Service, prevention of abuse and fraud</strong> — statutory obligations and the Controller's legitimate interests (Art. 6(1)(2) and (7)).</li>
            <li><strong>Publishing content to a connected third-party account</strong> — consent given by connecting the account and confirming the publication (Art. 6(1)(1)).</li>
            <li><strong>Marketing messages and web analytics</strong> — solely on the basis of separate prior consent (Art. 6(1)(1); Art. 18(1) of Federal Law No. 38-FZ of 13 March 2006). As of this version, no web analytics or advertising tools are enabled in the Service.</li>
          </ul>

          <h2>5. Processing operations</h2>
          <p>5.1. Processing is carried out with and without automation and includes: collection, recording, systematization, accumulation, storage, updating, retrieval, use, transfer (provision, access), anonymization, blocking, deletion and destruction of personal data.</p>
          <p>5.2. The Controller does not take decisions producing legal effects concerning the user based solely on automated processing of personal data.</p>
          <p>5.3. User materials are not used by the Controller to train its own machine learning models. Technology providers process materials on the Controller's instructions under enterprise (paid) API terms that exclude the use of submitted data for training the provider's models.</p>

          <h2>6. Place of processing and data localization</h2>
          <p>6.1. Recording, systematization, accumulation, storage, updating and retrieval of personal data of citizens of the Russian Federation are carried out using databases located in the Russian Federation (Art. 18(5) of Law No. 152-FZ). The Service's server infrastructure is hosted in data centres of a Russian cloud provider.</p>
          <p>6.2. Backups are stored within the same infrastructure with restricted access.</p>

          <h2>7. Disclosure to third parties</h2>
          <p>7.1. The Controller does not sell personal data and does not transfer it to third parties for their own purposes. Data is shared only to the extent necessary to provide the service and only with the following categories of recipients:</p>
          <ul>
            <li><strong>Telegram</strong> — the messaging platform through which the Service operates;</li>
            <li><strong>T-Bank JSC</strong> — payment acceptance, refunds, issuance and delivery of fiscal receipts;</li>
            <li><strong>A Russian cloud infrastructure provider</strong> — server hosting, object storage and backups;</li>
            <li><strong>Generative AI technology providers</strong> (including Google LLC — Gemini API — and the model access aggregator OpenRouter) — automatic speech recognition, subtitle markup and visual selection;</li>
            <li><strong>TikTok and other platforms connected by the user</strong> — only upon voluntary account connection and only to the extent required for the action requested by the user;</li>
            <li><strong>Public authorities</strong> — upon reasoned requests in the cases and manner established by Russian law.</li>
          </ul>
          <p>7.2. Transfers to processors are made under agreements imposing confidentiality and personal data security obligations (Art. 6(3) of Law No. 152-FZ).</p>

          <h2>8. Cross-border transfers</h2>
          <p>8.1. When generative AI technologies and the Telegram platform are used, certain processing operations may take place outside the Russian Federation. Such transfers are cross-border within the meaning of Art. 12 of Law No. 152-FZ and are carried out with notification to the competent supervisory authority in the established manner.</p>
          <p>8.2. Cross-border transfers are limited to user materials and technical metadata required to fulfil an order. User identifiers, payment and accounting data are not transferred outside the Russian Federation by the Controller.</p>
          <p>8.3. A user who does not wish their materials to be transferred cross-border may decline to use the Service; the service cannot technically be provided without such transfer.</p>

          <h2>9. Retention periods</h2>
          <ul>
            <li>Telegram identifier and profile data, order history — for as long as the Service is used and 12 months after the user's last interaction.</li>
            <li>Source user materials (audio, text, images) — up to 90 days from order fulfilment.</li>
            <li>Generated videos — up to 90 days from delivery to the user.</li>
            <li>Order execution and security logs — up to 12 months.</li>
            <li>Support correspondence — up to 12 months after the request is closed.</li>
            <li>Payment data and accounting records — 5 years (Art. 29 of Federal Law No. 402-FZ of 6 December 2011; Art. 23 of the Russian Tax Code).</li>
            <li>Records of the fact, version and time of consent and its withdrawal — for the term of consent and 3 years thereafter.</li>
            <li>Backups — up to 30 days, after which they are overwritten.</li>
          </ul>
          <p>9.1. Once the purposes of processing are achieved, or upon withdrawal of consent, personal data is destroyed or anonymized within 30 days unless another period is prescribed by law or contract (Art. 21 of Law No. 152-FZ). Data subject to mandatory statutory retention is kept for the period required by law.</p>

          <h2>10. User rights and how to exercise them</h2>
          <p>10.1. Users may obtain information about the processing of their personal data, request its correction, blocking or destruction where it is incomplete, outdated, inaccurate, unlawfully obtained or not necessary for the stated purpose, withdraw consent, and appeal against the Controller's actions.</p>
          <p>10.2. Requests are sent to ${contact} with details allowing identification of the user (including the Telegram ID or username used with the Service). The Controller may request additional identity verification where necessary to protect the data.</p>
          <p>10.3. Information is provided within 10 business days of receipt of the request. This period may be extended by no more than 5 business days with notice to the user stating the reasons (Art. 20 of Law No. 152-FZ).</p>
          <p>10.4. If unlawful processing is identified, the Controller blocks the data for the period of verification within no more than 3 business days and remedies the breach or destroys the data within the statutory periods.</p>
          <p>10.5. Consent may be withdrawn by writing to ${contact}. Withdrawal does not affect the lawfulness of processing carried out before it was received and does not stop processing carried out on other lawful bases (including performance of a contract and statutory obligations).</p>
          <p>10.6. Users may lodge a complaint with the Russian supervisory authority (Roskomnadzor) or bring court proceedings.</p>

          <h2>11. Data received through TikTok</h2>
          <p>11.1. Connecting a TikTok account to the Service is voluntary and is not required to generate content. The Service is not a TikTok product, is not affiliated with TikTok and does not act on its behalf.</p>
          <p>11.2. Before access is granted, the user is shown the permissions being requested. The Controller requests the minimum necessary scopes and may receive: pseudonymous account identifiers (open_id, union_id), display name and profile avatar, and — where the corresponding permission is granted — the list of the user's own posted videos and aggregate statistics for them, together with technical access tokens.</p>
          <p>11.3. Data received from TikTok is used solely to: display the connected account in the Service interface; publish content to the user's account at the user's explicit command and after a preview; and show the user statistics of their own posts.</p>
          <p>11.4. Data received from TikTok is <strong>not sold, not shared with third parties, and not used for advertising, profiling, scoring or machine learning training</strong>. No content can be published to the user's account without the user's explicit confirmation in the Service interface.</p>
          <p>11.5. Access tokens are stored in encrypted form and are deleted when access is revoked, when the account is disconnected, or after 12 months of user inactivity, whichever occurs first.</p>
          <p>11.6. <strong>Disconnection and data deletion.</strong> A user may at any time: (a) disconnect the account in the Service interface; (b) revoke access in TikTok settings under app permissions management; or (c) send a deletion request to ${contact}. Upon revocation or receipt of a request, the Controller stops calling the TikTok API and deletes the data and tokens received from TikTok within no more than 30 days, except where retention is required by law. The user is notified of completion at the address they provided.</p>

          <h2>12. Security</h2>
          <p>12.1. The Controller applies legal, organizational and technical measures to protect personal data against unlawful or accidental access, destruction, alteration, blocking, copying, provision and dissemination (Art. 18.1, 19 of Law No. 152-FZ), including: transmission over HTTPS/TLS; least-privilege access control; encrypted storage of secrets and tokens; access logging; backups; and regular review of processors.</p>
          <p>12.2. The Controller does not receive or store bank card details. Payments are accepted by T-Bank JSC on the bank's secure payment page; the bank's payment infrastructure is PCI DSS certified.</p>
          <p>12.3. If an incident resulting in unlawful transfer of personal data occurs, the Controller notifies the supervisory authority within the periods set by Art. 21(3.1) of Law No. 152-FZ (within 24 hours of the incident and within 72 hours of the results of the internal investigation).</p>

          <h2>13. Cookies and local storage</h2>
          <p>13.1. The use of cookies and other local storage technologies is described in the <a href="cookies.html" data-keep-language>Cookie Policy</a>.</p>

          <h2>14. Age restrictions</h2>
          <p>14.1. The Service is intended for persons aged 18 and over. Persons aged 14 to 18 may use the Service with the consent of a legal representative. The Controller does not knowingly collect data of persons under 14; such data is deleted when identified.</p>

          <h2>15. Changes to this Policy</h2>
          <p>15.1. The Controller may amend this Policy. The current version, with its version number and effective date, is published on this page. Continued use of the Service after the effective date constitutes acceptance of the new version.</p>

          <h2>16. Contact</h2>
          <p>${operatorEn}<br>Address: ${addressEn}<br>E-mail: ${contact}<br>Phone: +7 (910) 572-49-67</p>`
      }
    },

    terms: {
      ru: {
        title: 'Условия использования',
        body: `<p>Настоящие Условия использования (далее — «Условия») регулируют использование сайта blast808.com, Telegram-бота ${botLink} и связанных функций сервиса Blast (далее — «Сервис»). Условия являются соглашением между пользователем и Оператором Сервиса — ${operatorRu} (далее — «Оператор»).</p>

          <h2>1. Принятие Условий</h2>
          <p>1.1. Использование Сервиса означает полное и безоговорочное принятие настоящих Условий, <a href="privacy.html" data-keep-language>Политики конфиденциальности</a> и <a href="cookies.html" data-keep-language>Политики cookie</a>. Если пользователь не согласен с Условиями, он обязан прекратить использование Сервиса.</p>
          <p>1.2. Приобретение платных услуг дополнительно регулируется <a href="offer.html" data-keep-language>Публичной офертой</a>. В случае противоречия между Условиями и Публичной офертой в части платных услуг применяется Публичная оферта.</p>

          <h2>2. Описание Сервиса</h2>
          <p>2.1. Сервис предоставляет автоматизированную генерацию вертикального видеоконтента на основе аудиоматериала и сопроводительных данных, предоставленных пользователем, с использованием технологий искусственного интеллекта.</p>
          <p>2.2. Доступные функции, лимиты, стоимость и сроки отображаются пользователю в интерфейсе Telegram-бота до оформления заказа.</p>
          <p>2.3. Оператор вправе изменять, дополнять и прекращать отдельные функции Сервиса, уведомляя об этом пользователей через интерфейс Сервиса. Изменения не затрагивают уже оплаченные и не исполненные заказы.</p>

          <h2>3. Доступ и возрастные требования</h2>
          <p>3.1. Доступ к Сервису осуществляется через аккаунт Telegram пользователя. Пользователь несёт ответственность за сохранность доступа к своему аккаунту Telegram.</p>
          <p>3.2. Использование Сервиса допускается лицами, достигшими 18 лет, а лицами от 14 до 18 лет — с согласия законного представителя.</p>

          <h2>4. Допустимое использование</h2>
          <p>4.1. Пользователь обязуется не использовать Сервис для:</p>
          <ul>
            <li>загрузки и обработки материалов, права на которые ему не принадлежат и не предоставлены правообладателем;</li>
            <li>создания и распространения материалов, нарушающих законодательство Российской Федерации, в том числе экстремистских материалов, материалов, пропагандирующих насилие или наркотические средства, а также материалов порнографического характера и материалов, причиняющих вред несовершеннолетним;</li>
            <li>создания вводящих в заблуждение материалов о реальных людях, в том числе синтетических изображений и голосов без согласия соответствующих лиц;</li>
            <li>нарушения прав третьих лиц, распространения клеветы и оскорблений;</li>
            <li>обхода технических ограничений, автоматизированного массового обращения к Сервису, вмешательства в его работу, декомпиляции и попыток несанкционированного доступа;</li>
            <li>перепродажи доступа к Сервису без письменного согласия Оператора.</li>
          </ul>
          <p>4.2. Нарушение п. 4.1 является основанием для ограничения или прекращения доступа к Сервису без возврата стоимости неиспользованных услуг в части, соответствующей нарушению.</p>

          <h2>5. Пользовательские материалы</h2>
          <p>5.1. Исключительные права на материалы, загружаемые пользователем, сохраняются за пользователем или иными правообладателями. Оператор не приобретает прав на такие материалы.</p>
          <p>5.2. Пользователь гарантирует, что обладает всеми правами и разрешениями, необходимыми для загрузки материалов в Сервис и для использования результата, включая права на музыкальное произведение, фонограмму, исполнение и текст, а также согласия изображённых лиц.</p>
          <p>5.3. Пользователь предоставляет Оператору безвозмездную непередаваемую лицензию на использование загруженных материалов исключительно в целях выполнения заказа, оказания поддержки, обеспечения безопасности и исполнения требований закона, на срок хранения, указанный в Политике конфиденциальности.</p>
          <p>5.4. Оператор вправе отказать в обработке материалов, если имеются достаточные основания полагать, что их обработка нарушает закон или права третьих лиц.</p>

          <h2>6. Результаты генерации</h2>
          <p>6.1. Пользователь вправе свободно использовать полученные видеоматериалы, в том числе в коммерческих целях, при условии соблюдения прав на исходные материалы и правил платформ размещения.</p>
          <p>6.2. Пользователь уведомлён, что результат создаётся автоматизированно. В соответствии с законодательством Российской Федерации об интеллектуальной собственности автором результата интеллектуальной деятельности признаётся гражданин, творческим трудом которого он создан; объём правовой охраны автоматически сгенерированных материалов может быть ограничен. Оператор не гарантирует охраноспособность результата как объекта авторского права.</p>
          <p>6.3. Визуальные материалы (футаж), используемые при сборке ролика, предоставляются пользователю в составе результата для использования в целях продвижения загруженного трека. Пользователь не приобретает исключительных прав на отдельные визуальные элементы и не вправе распространять их отдельно от готового ролика.</p>
          <p>6.4. Сервис не гарантирует уникальность результата: различные пользователи могут получить визуально схожие материалы.</p>

          <h2>7. Маркировка контента, созданного с помощью ИИ</h2>
          <p>7.1. Результаты работы Сервиса представляют собой контент, созданный с использованием искусственного интеллекта. Пользователь обязуется соблюдать правила площадок, на которых размещает контент, включая обязательную маркировку синтетического контента (AI-generated content), если такая маркировка требуется правилами площадки или законодательством.</p>

          <h2>8. Сторонние платформы. Интеграция с TikTok</h2>
          <p>8.1. Telegram, TikTok и иные платформы являются независимыми сервисами и действуют на основании собственных правил. Оператор не аффилирован с ними, не является их представителем, партнёром или подразделением и не отвечает за их работу, доступность и решения по модерации.</p>
          <p>8.2. Подключение аккаунта TikTok к Сервису является добровольным. До предоставления доступа пользователю отображается перечень запрашиваемых разрешений и цель их использования. Пользователь вправе отказаться от подключения без потери доступа к остальным функциям Сервиса.</p>
          <p>8.3. Публикация контента в аккаунт TikTok осуществляется исключительно по прямой команде пользователя. Перед публикацией пользователю отображается предварительный просмотр материала и предоставляется возможность отредактировать текст публикации, хештеги, настройки приватности и настройки взаимодействия (комментарии, дуэты, склейки). Значения этих настроек не устанавливаются Сервисом по умолчанию.</p>
          <p>8.4. Сервис не наносит на контент пользователя собственные логотипы и рекламные водяные знаки.</p>
          <p>8.5. Пользователь обязуется соблюдать Условия обслуживания TikTok, Правила сообщества TikTok, Политику брендированного контента и Подтверждение об использовании музыки (Music Usage Confirmation). Размещая контент через Сервис, пользователь соглашается с Подтверждением об использовании музыки TikTok, а при размещении коммерческого или брендированного контента — также с Политикой брендированного контента TikTok, и обязуется указывать соответствующие раскрытия.</p>
          <p>8.6. Пользователь несёт ответственность за наличие прав на музыкальное произведение и иные материалы, размещаемые в TikTok через Сервис.</p>
          <p>8.7. Пользователь вправе в любой момент отключить аккаунт в интерфейсе Сервиса или отозвать доступ в настройках TikTok. Порядок удаления данных, полученных от TikTok, описан в разделе 11 <a href="privacy.html" data-keep-language>Политики конфиденциальности</a>.</p>
          <p>8.8. До прохождения аудита клиента TikTok API действуют ограничения платформы на количество публикаций и уровень их видимости; Оператор соблюдает такие ограничения и информирует пользователя о них в интерфейсе.</p>

          <h2>9. Права Оператора на Сервис</h2>
          <p>9.1. Исключительные права на программное обеспечение Сервиса, его интерфейсы, дизайн, тексты, товарные знаки и иные элементы принадлежат Оператору. Использование без письменного согласия Оператора не допускается.</p>

          <h2>10. Претензии о нарушении прав</h2>
          <p>10.1. Правообладатель, считающий, что через Сервис нарушены его права, вправе направить обращение на ${contact} с указанием: сведений о заявителе и способа связи; описания объекта прав и подтверждения прав на него; ссылки или иных данных, позволяющих идентифицировать материал; заявления о том, что использование осуществляется без разрешения.</p>
          <p>10.2. Оператор рассматривает обращение и принимает меры (включая удаление материалов и ограничение доступа нарушителя) в срок не более 10 рабочих дней.</p>

          <h2>11. Доступность Сервиса</h2>
          <p>11.1. Оператор стремится обеспечивать бесперебойную работу Сервиса, но не гарантирует его непрерывную и безошибочную работу, а также работу сторонних платформ и провайдеров.</p>
          <p>11.2. Оператор вправе проводить плановые технические работы, по возможности уведомляя пользователей заранее.</p>

          <h2>12. Отсутствие гарантий результата продвижения</h2>
          <p>12.1. Сервис является инструментом создания контента. Оператор не гарантирует достижение конкретных показателей просмотров, охватов, подписчиков, прослушиваний, попадание в рекомендации или иной результат продвижения, поскольку такие показатели зависят от алгоритмов платформ и иных обстоятельств, находящихся вне контроля Оператора.</p>

          <h2>13. Ответственность</h2>
          <p>13.1. Оператор не несёт ответственности за последствия использования пользователем сгенерированного контента, в том числе за санкции платформ и претензии третьих лиц, вызванные отсутствием у пользователя прав на исходные материалы.</p>
          <p>13.2. Ответственность Оператора определяется законодательством Российской Федерации. Ничто в настоящих Условиях не ограничивает права потребителей, установленные Законом РФ от 07.02.1992 № 2300-1 «О защите прав потребителей», и не исключает ответственность, которая не может быть исключена или ограничена по закону.</p>
          <p>13.3. Пользователь обязуется возместить Оператору документально подтверждённые убытки, возникшие вследствие нарушения пользователем п. 4.1 и п. 5.2 настоящих Условий.</p>

          <h2>14. Персональные данные</h2>
          <p>14.1. Обработка персональных данных осуществляется в соответствии с <a href="privacy.html" data-keep-language>Политикой конфиденциальности</a> и <a href="personal-data-consent.html" data-keep-language>Согласием на обработку персональных данных</a>.</p>

          <h2>15. Прекращение доступа</h2>
          <p>15.1. Оператор вправе ограничить или прекратить доступ пользователя к Сервису при нарушении настоящих Условий, требований закона или при наличии обоснованных подозрений в мошенничестве, уведомив пользователя через интерфейс Сервиса.</p>
          <p>15.2. Пользователь вправе прекратить использование Сервиса в любой момент, направив запрос на удаление данных на ${contact}.</p>

          <h2>16. Применимое право и разрешение споров</h2>
          <p>16.1. К настоящим Условиям применяется право Российской Федерации.</p>
          <p>16.2. Стороны принимают меры к досудебному урегулированию. Претензия направляется на ${contact} и рассматривается в течение 10 календарных дней.</p>
          <p>16.3. При недостижении согласия спор рассматривается в суде. Споры с участием потребителей рассматриваются по правилам подсудности, установленным ст. 17 Закона РФ «О защите прав потребителей», в том числе по выбору потребителя.</p>

          <h2>17. Изменения Условий</h2>
          <p>17.1. Оператор вправе изменять Условия. Актуальная редакция с указанием версии и даты вступления в силу публикуется на этой странице. Продолжение использования Сервиса после вступления изменений в силу означает согласие с новой редакцией.</p>

          <h2>18. Реквизиты</h2>
          <p>${operatorRu}<br>Адрес: ${addressRu}<br>E-mail: ${contact}<br>Телефон: ${phoneRu}</p>`
      },
      en: {
        title: 'Terms of Service',
        body: `<p>These Terms of Service (the "Terms") govern the use of the blast808.com website, the Telegram bot ${botLink} and related features of the Blast service (the "Service"). The Terms constitute an agreement between the user and ${operatorEn} (the "Operator").</p>

          <h2>1. Acceptance</h2>
          <p>1.1. Using the Service constitutes full and unconditional acceptance of these Terms, the <a href="privacy.html" data-keep-language>Privacy Policy</a> and the <a href="cookies.html" data-keep-language>Cookie Policy</a>. Users who do not agree must stop using the Service.</p>
          <p>1.2. Purchases of paid services are additionally governed by the <a href="offer.html" data-keep-language>Public Offer</a>. In case of conflict regarding paid services, the Public Offer prevails.</p>

          <h2>2. The Service</h2>
          <p>2.1. The Service provides automated generation of vertical video content from audio and accompanying data submitted by the user, using artificial intelligence technologies.</p>
          <p>2.2. Available features, limits, prices and timelines are displayed in the Telegram bot before an order is placed.</p>
          <p>2.3. The Operator may modify, add or discontinue individual features, notifying users through the Service interface. Changes do not affect orders already paid for and not yet fulfilled.</p>

          <h2>3. Access and age requirements</h2>
          <p>3.1. Access is provided through the user's Telegram account. The user is responsible for keeping their Telegram account secure.</p>
          <p>3.2. The Service may be used by persons aged 18 and over, and by persons aged 14 to 18 with the consent of a legal representative.</p>

          <h2>4. Acceptable use</h2>
          <p>4.1. The user must not use the Service to:</p>
          <ul>
            <li>upload or process materials to which they hold no rights and for which no rightsholder permission has been granted;</li>
            <li>create or distribute materials that violate Russian law, including extremist materials, materials promoting violence or narcotics, pornographic materials and materials harmful to minors;</li>
            <li>create misleading materials about real people, including synthetic images or voices without the consent of the persons concerned;</li>
            <li>infringe third-party rights or distribute defamatory or abusive content;</li>
            <li>circumvent technical restrictions, generate automated bulk requests, interfere with the Service, decompile it or attempt unauthorized access;</li>
            <li>resell access to the Service without the Operator's written consent.</li>
          </ul>
          <p>4.2. Breach of clause 4.1 is grounds for restricting or terminating access without refund of unused services to the extent attributable to the breach.</p>

          <h2>5. User materials</h2>
          <p>5.1. Exclusive rights in materials uploaded by the user remain with the user or other rightsholders. The Operator acquires no rights in such materials.</p>
          <p>5.2. The user warrants that they hold all rights and permissions required to upload materials to the Service and to use the output, including rights in the musical work, phonogram, performance and lyrics, and consents of any individuals depicted.</p>
          <p>5.3. The user grants the Operator a royalty-free, non-transferable licence to use uploaded materials solely to fulfil the order, provide support, ensure security and comply with law, for the retention period stated in the Privacy Policy.</p>
          <p>5.4. The Operator may refuse to process materials where there are sufficient grounds to believe that processing would violate law or third-party rights.</p>

          <h2>6. Generated output</h2>
          <p>6.1. The user may freely use the resulting videos, including commercially, provided that rights in the source materials and the rules of the publishing platforms are respected.</p>
          <p>6.2. The user acknowledges that the output is produced automatically. Under Russian intellectual property law, the author of a result of intellectual activity is the individual by whose creative work it was created; the scope of legal protection of automatically generated materials may be limited. The Operator does not warrant that the output is protectable by copyright.</p>
          <p>6.3. Visual materials (footage) used in assembling a video are supplied as part of the output for the purpose of promoting the uploaded track. The user acquires no exclusive rights in individual visual elements and may not distribute them separately from the finished video.</p>
          <p>6.4. The Service does not guarantee uniqueness of output: different users may receive visually similar materials.</p>

          <h2>7. Labelling of AI-generated content</h2>
          <p>7.1. Output of the Service is content created using artificial intelligence. The user undertakes to comply with the rules of the platforms where the content is published, including mandatory labelling of AI-generated content where such labelling is required by platform rules or by law.</p>

          <h2>8. Third-party platforms. TikTok integration</h2>
          <p>8.1. Telegram, TikTok and other platforms are independent services operating under their own rules. The Operator is not affiliated with them, is not their representative, partner or division, and is not responsible for their operation, availability or moderation decisions.</p>
          <p>8.2. Connecting a TikTok account is voluntary. Before access is granted, the user is shown the permissions requested and the purpose of each. The user may decline to connect without losing access to other features.</p>
          <p>8.3. Content is published to a TikTok account solely at the user's explicit command. Before publication the user is shown a preview of the content and can edit the caption, hashtags, privacy setting and interaction settings (comments, duet, stitch). The Service does not preset default values for these settings.</p>
          <p>8.4. The Service does not superimpose its own logos or promotional watermarks on user content.</p>
          <p>8.5. The user undertakes to comply with the TikTok Terms of Service, TikTok Community Guidelines, Branded Content Policy and Music Usage Confirmation. By posting through the Service the user agrees to TikTok's Music Usage Confirmation and, where commercial or branded content is posted, to TikTok's Branded Content Policy, and undertakes to make the corresponding disclosures.</p>
          <p>8.6. The user is responsible for holding the rights to the music and other materials published to TikTok through the Service.</p>
          <p>8.7. The user may disconnect the account in the Service interface or revoke access in TikTok settings at any time. Deletion of data received from TikTok is described in Section 11 of the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>
          <p>8.8. Until the TikTok API client is audited, platform limits apply to the number of posts and their visibility; the Operator complies with those limits and informs users of them in the interface.</p>

          <h2>9. Operator's rights in the Service</h2>
          <p>9.1. Exclusive rights in the Service software, interfaces, design, texts, trademarks and other elements belong to the Operator. Use without the Operator's written consent is prohibited.</p>

          <h2>10. Rights infringement claims</h2>
          <p>10.1. A rightsholder who believes their rights have been infringed through the Service may write to ${contact} providing: their details and contact method; a description of the protected subject matter and evidence of rights in it; a link or other data identifying the material; and a statement that the use is unauthorized.</p>
          <p>10.2. The Operator reviews the claim and takes measures (including removal of materials and restriction of the infringer's access) within no more than 10 business days.</p>

          <h2>11. Availability</h2>
          <p>11.1. The Operator seeks to keep the Service running but does not guarantee uninterrupted or error-free operation, nor the operation of third-party platforms and providers.</p>
          <p>11.2. The Operator may carry out scheduled maintenance, giving advance notice where practicable.</p>

          <h2>12. No promotion guarantees</h2>
          <p>12.1. The Service is a content creation tool. The Operator does not guarantee any particular number of views, reach, followers or streams, placement in recommendations, or any other promotional outcome, as these depend on platform algorithms and other circumstances beyond the Operator's control.</p>

          <h2>13. Liability</h2>
          <p>13.1. The Operator is not liable for the consequences of the user's use of generated content, including platform sanctions and third-party claims arising from the user's lack of rights in the source materials.</p>
          <p>13.2. The Operator's liability is governed by Russian law. Nothing in these Terms limits consumer rights under Russian Law No. 2300-1 of 7 February 1992 "On Protection of Consumer Rights" or excludes liability that cannot lawfully be excluded or limited.</p>
          <p>13.3. The user shall reimburse the Operator for documented losses arising from the user's breach of clauses 4.1 and 5.2.</p>

          <h2>14. Personal data</h2>
          <p>14.1. Personal data is processed in accordance with the <a href="privacy.html" data-keep-language>Privacy Policy</a> and the <a href="personal-data-consent.html" data-keep-language>Consent to Personal Data Processing</a>.</p>

          <h2>15. Termination</h2>
          <p>15.1. The Operator may restrict or terminate access where these Terms or the law are breached, or where fraud is reasonably suspected, notifying the user through the Service interface.</p>
          <p>15.2. The user may stop using the Service at any time and request deletion of their data at ${contact}.</p>

          <h2>16. Governing law and disputes</h2>
          <p>16.1. These Terms are governed by the law of the Russian Federation.</p>
          <p>16.2. The parties shall seek pre-trial settlement. Claims are sent to ${contact} and reviewed within 10 calendar days.</p>
          <p>16.3. Failing agreement, disputes are resolved in court. Consumer disputes are heard under the jurisdiction rules of Art. 17 of the Russian Law "On Protection of Consumer Rights", including at the consumer's election.</p>

          <h2>17. Changes</h2>
          <p>17.1. The Operator may amend these Terms. The current version, with its version number and effective date, is published on this page. Continued use after the effective date constitutes acceptance.</p>

          <h2>18. Details</h2>
          <p>${operatorEn}<br>Address: ${addressEn}<br>E-mail: ${contact}<br>Phone: +7 (910) 572-49-67</p>`
      }
    },

    cookies: {
      ru: {
        title: 'Политика cookie',
        body: `<p>Настоящая Политика объясняет, какие файлы cookie и технологии локального хранения используются на сайте blast808.com и как пользователь может управлять ими.</p>

          <h2>1. Что мы используем</h2>
          <p>1.1. Сайт сохраняет в локальном хранилище браузера (localStorage) два значения:</p>
          <ul>
            <li><code>blast_language</code> — выбранный язык интерфейса. Хранится до очистки данных сайта пользователем.</li>
            <li><code>blast_cookie_consent</code> — версия и состав решения пользователя по необязательным категориям, а также дата решения. Хранится до очистки данных сайта или до изменения решения пользователем.</li>
          </ul>
          <p>1.2. Эти значения не передаются сайтом третьим лицам и используются только для сохранения пользовательских настроек.</p>
          <p>1.3. Веб-сервер фиксирует технические данные обращений (IP-адрес, дата и время, тип браузера) в журналах, необходимых для обеспечения работоспособности и безопасности сайта.</p>

          <h2>2. Категории</h2>
          <ul>
            <li><strong>Необходимые.</strong> Обеспечивают базовую работу сайта, сохранение языка и фиксацию решения по cookie. Не отключаются, так как без них сайт не может работать корректно.</li>
            <li><strong>Аналитические.</strong> Позволяют оценивать использование сайта и улучшать его. Подключаются только с согласия пользователя.</li>
            <li><strong>Маркетинговые.</strong> Позволяют измерять эффективность рекламных кампаний. Подключаются только с согласия пользователя.</li>
          </ul>
          <p>2.1. На дату вступления в силу настоящей версии аналитические и маркетинговые скрипты на сайте не подключены. Если они будут добавлены, они не будут загружаться до получения согласия по соответствующей категории, а настоящая Политика будет дополнена сведениями о поставщике, наименованиях и сроках хранения файлов, целях и странах обработки.</p>

          <h2>3. Управление выбором</h2>
          <p>3.1. При первом посещении сайта отображается баннер, позволяющий принять все категории, отклонить необязательные или настроить категории по отдельности. Необязательные категории по умолчанию отключены.</p>
          <p>3.2. Изменить решение можно в любой момент через ссылку «Настроить cookie» в нижней части главной страницы.</p>
          <p>3.3. Отзыв согласия не влияет на правомерность обработки, осуществлённой до отзыва.</p>

          <h2>4. Управление средствами браузера</h2>
          <p>4.1. Пользователь может удалить сохранённые данные сайта или запретить их сохранение в настройках браузера. При блокировке локального хранилища выбор языка и решение по cookie не сохраняются между посещениями.</p>

          <h2>5. Персональные данные</h2>
          <p>5.1. Обработка данных, которые могут быть получены с использованием указанных технологий, осуществляется в соответствии с <a href="privacy.html" data-keep-language>Политикой конфиденциальности</a>.</p>

          <h2>6. Контакты</h2>
          <p>Вопросы по настоящей Политике направляются на ${contact}.</p>`
      },
      en: {
        title: 'Cookie Policy',
        body: `<p>This Policy explains which cookies and local storage technologies are used on blast808.com and how users can control them.</p>

          <h2>1. What we use</h2>
          <p>1.1. The website stores two values in the browser's localStorage:</p>
          <ul>
            <li><code>blast_language</code> — the selected interface language. Stored until the user clears site data.</li>
            <li><code>blast_cookie_consent</code> — the version and contents of the user's decision on optional categories and the date of that decision. Stored until site data is cleared or the decision is changed.</li>
          </ul>
          <p>1.2. These values are not sent by the website to third parties and are used only to preserve user settings.</p>
          <p>1.3. The web server records technical request data (IP address, date and time, browser type) in logs required to keep the site operational and secure.</p>

          <h2>2. Categories</h2>
          <ul>
            <li><strong>Necessary.</strong> Provide core site functionality, language persistence and recording of the cookie decision. These cannot be disabled as the site cannot function correctly without them.</li>
            <li><strong>Analytics.</strong> Allow measurement of site usage and improvement of the site. Enabled only with consent.</li>
            <li><strong>Marketing.</strong> Allow measurement of advertising campaigns. Enabled only with consent.</li>
          </ul>
          <p>2.1. As of the effective date of this version, no analytics or marketing scripts are enabled on the site. If they are added, they will not load before consent for the relevant category is obtained, and this Policy will be supplemented with the provider, file names and lifetimes, purposes and processing countries.</p>

          <h2>3. Managing your choice</h2>
          <p>3.1. On the first visit a banner is shown allowing the user to accept all categories, reject optional ones or configure categories individually. Optional categories are off by default.</p>
          <p>3.2. The decision can be changed at any time using the "Cookie settings" link at the bottom of the home page.</p>
          <p>3.3. Withdrawal of consent does not affect the lawfulness of processing carried out before withdrawal.</p>

          <h2>4. Browser controls</h2>
          <p>4.1. Users may delete stored site data or block its storage in browser settings. If local storage is blocked, the language selection and cookie decision will not persist between visits.</p>

          <h2>5. Personal data</h2>
          <p>5.1. Any personal data obtained through these technologies is processed in accordance with the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>

          <h2>6. Contact</h2>
          <p>Questions about this Policy may be sent to ${contact}.</p>`
      }
    },

    consent: {
      ru: {
        title: 'Согласие на обработку персональных данных',
        body: `<p>Настоящий документ является формой согласия на обработку персональных данных, предоставляемого в соответствии со ст. 9 Федерального закона от 27.07.2006 № 152-ФЗ «О персональных данных». Согласие предоставляется пользователем путём совершения отдельного подтверждающего действия в интерфейсе Telegram-бота ${botLink} перед началом использования Сервиса.</p>

          <h2>1. Оператор, которому предоставляется согласие</h2>
          <p>${operatorRu}<br>Адрес: ${addressRu}<br>E-mail: ${contact}</p>

          <h2>2. Субъект персональных данных</h2>
          <p>Пользователь Сервиса, идентифицируемый по числовому идентификатору аккаунта Telegram (Telegram ID) и имени пользователя (username), с которых осуществляется обращение к Telegram-боту Сервиса.</p>

          <h2>3. Цели обработки</h2>
          <ul>
            <li>оказание услуг по автоматизированной генерации видеоконтента и передача результата;</li>
            <li>идентификация пользователя, учёт заказов и лимитов;</li>
            <li>приём оплаты, оформление возвратов, направление кассовых чеков и ведение учёта;</li>
            <li>обработка обращений в службу поддержки;</li>
            <li>обеспечение безопасности Сервиса и предотвращение злоупотреблений;</li>
            <li>публикация контента в подключённом пользователем аккаунте сторонней платформы — по прямой команде пользователя.</li>
          </ul>

          <h2>4. Перечень персональных данных</h2>
          <ul>
            <li>идентификатор аккаунта Telegram, имя пользователя, имя и фамилия, иные общедоступные данные профиля Telegram;</li>
            <li>адрес электронной почты и иные контактные данные, сообщённые пользователем;</li>
            <li>аудиозаписи, тексты, изображения и иные материалы, направленные для генерации, а также параметры заказа;</li>
            <li>результаты генерации и история заказов;</li>
            <li>сведения о платежах (идентификатор, сумма, дата, статус, способ оплаты) без реквизитов банковской карты;</li>
            <li>технические данные обращений и журналы работы Сервиса;</li>
            <li>идентификаторы и данные профиля аккаунта сторонней платформы — при её добровольном подключении пользователем.</li>
          </ul>

          <h2>5. Перечень действий с персональными данными и способы обработки</h2>
          <p>Согласие предоставляется на сбор, запись, систематизацию, накопление, хранение, уточнение (обновление, изменение), извлечение, использование, передачу (предоставление, доступ) лицам, указанным в разделе 6, обезличивание, блокирование, удаление и уничтожение персональных данных с использованием средств автоматизации и без их использования.</p>

          <h2>6. Лица, которым может быть поручена обработка</h2>
          <p>Обработка может быть поручена: Telegram — в части доставки сообщений и файлов; АО «Т-Банк» — в части приёма платежей и оформления чеков; российскому провайдеру облачной инфраструктуры — в части размещения и хранения данных; поставщикам технологий генеративного искусственного интеллекта (в том числе Google LLC и OpenRouter) — в части автоматической обработки материалов; подключённой пользователем платформе (TikTok) — в части выполнения запрошенных пользователем действий. Перечень и условия передачи раскрыты в <a href="privacy.html" data-keep-language>Политике конфиденциальности</a>.</p>

          <h2>7. Трансграничная передача</h2>
          <p>Согласие включает согласие на трансграничную передачу пользовательских материалов и связанных технических метаданных поставщикам технологий генеративного искусственного интеллекта и платформе Telegram в объёме, необходимом для оказания услуг, в порядке, предусмотренном ст. 12 Закона № 152-ФЗ.</p>

          <h2>8. Срок действия и порядок отзыва</h2>
          <p>8.1. Согласие действует с момента его предоставления до достижения целей обработки либо до его отзыва пользователем.</p>
          <p>8.2. Отзыв согласия осуществляется путём направления заявления на ${contact} с указанием Telegram ID или username, использованных при обращении к Сервису.</p>
          <p>8.3. После отзыва согласия обработка прекращается, а персональные данные уничтожаются в срок не более 30 дней, за исключением данных, обработка и хранение которых осуществляются на иных законных основаниях (в том числе для исполнения договора и обязанностей, установленных законодательством о бухгалтерском учёте и налогах).</p>

          <h2>9. Порядок фиксации согласия</h2>
          <p>9.1. Согласие предоставляется отдельным подтверждающим действием пользователя, не предустановленным по умолчанию, с одновременным предоставлением доступа к настоящему документу и <a href="privacy.html" data-keep-language>Политике конфиденциальности</a>.</p>
          <p>9.2. Оператор фиксирует и хранит сведения о факте предоставления согласия: идентификатор пользователя, версию документа, дату и время подтверждения, а также сведения об отзыве согласия.</p>

          <h2>10. Подтверждение</h2>
          <p>Совершая подтверждающее действие в интерфейсе Сервиса, пользователь подтверждает, что ознакомлен с настоящим Согласием и <a href="privacy.html" data-keep-language>Политикой конфиденциальности</a>, действует свободно, своей волей и в своём интересе, а также обладает необходимой дееспособностью либо согласием законного представителя.</p>`
      },
      en: {
        title: 'Consent to Personal Data Processing',
        body: `<p>This document is the form of consent to personal data processing given under Art. 9 of Russian Federal Law No. 152-FZ of 27 July 2006 "On Personal Data". Consent is given by the user through a separate affirmative action in the interface of the Telegram bot ${botLink} before the Service is used.</p>

          <h2>1. Controller to whom consent is given</h2>
          <p>${operatorEn}<br>Address: ${addressEn}<br>E-mail: ${contact}</p>

          <h2>2. Data subject</h2>
          <p>The user of the Service, identified by the numeric Telegram account identifier (Telegram ID) and username from which the Service's Telegram bot is contacted.</p>

          <h2>3. Purposes</h2>
          <ul>
            <li>providing automated video content generation services and delivering the output;</li>
            <li>identifying the user and maintaining order and quota records;</li>
            <li>accepting payment, processing refunds, issuing fiscal receipts and keeping accounts;</li>
            <li>handling support requests;</li>
            <li>securing the Service and preventing abuse;</li>
            <li>publishing content to a third-party account connected by the user, at the user's explicit command.</li>
          </ul>

          <h2>4. Categories of personal data</h2>
          <ul>
            <li>Telegram account identifier, username, first and last name and other public Telegram profile data;</li>
            <li>e-mail address and other contact details provided by the user;</li>
            <li>audio, text, images and other materials submitted for generation, and order settings;</li>
            <li>generated output and order history;</li>
            <li>payment information (identifier, amount, date, status, payment method) excluding bank card details;</li>
            <li>technical request data and Service logs;</li>
            <li>identifiers and profile data of a third-party account, where voluntarily connected by the user.</li>
          </ul>

          <h2>5. Processing operations and methods</h2>
          <p>Consent is given to the collection, recording, systematization, accumulation, storage, updating, retrieval, use, transfer (provision, access) to the persons listed in Section 6, anonymization, blocking, deletion and destruction of personal data, with and without automation.</p>

          <h2>6. Persons who may be instructed to process data</h2>
          <p>Processing may be entrusted to: Telegram — delivery of messages and files; T-Bank JSC — payment acceptance and fiscal receipts; a Russian cloud infrastructure provider — hosting and storage; generative AI technology providers (including Google LLC and OpenRouter) — automated processing of materials; and a platform connected by the user (TikTok) — performing the actions the user requests. The list and terms of transfer are disclosed in the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>

          <h2>7. Cross-border transfer</h2>
          <p>This consent includes consent to the cross-border transfer of user materials and related technical metadata to generative AI technology providers and to the Telegram platform, to the extent necessary to provide the services, in the manner provided by Art. 12 of Law No. 152-FZ.</p>

          <h2>8. Term and withdrawal</h2>
          <p>8.1. Consent is effective from the moment it is given until the purposes of processing are achieved or until it is withdrawn by the user.</p>
          <p>8.2. Consent is withdrawn by sending a request to ${contact} stating the Telegram ID or username used with the Service.</p>
          <p>8.3. After withdrawal, processing ceases and personal data is destroyed within no more than 30 days, except for data processed and retained on other lawful grounds (including performance of a contract and obligations under accounting and tax legislation).</p>

          <h2>9. How consent is recorded</h2>
          <p>9.1. Consent is given by a separate, non-pre-ticked affirmative action, with simultaneous access to this document and to the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>
          <p>9.2. The Controller records and retains evidence of consent: user identifier, document version, date and time of confirmation, and any withdrawal.</p>

          <h2>10. Confirmation</h2>
          <p>By completing the affirmative action in the Service interface, the user confirms that they have read this Consent and the <a href="privacy.html" data-keep-language>Privacy Policy</a>, act freely, of their own will and in their own interest, and have the required legal capacity or the consent of a legal representative.</p>`
      }
    },

    offer: {
      ru: {
        title: 'Публичная оферта на оказание услуг',
        body: `<p>${operatorRu}, адрес: ${addressRu} (далее — «Исполнитель»), в соответствии с п. 2 ст. 437 Гражданского кодекса Российской Федерации предлагает любому дееспособному физическому лицу, индивидуальному предпринимателю или юридическому лицу (далее — «Заказчик») заключить договор возмездного оказания услуг на указанных ниже условиях.</p>

          <h2>1. Термины</h2>
          <p>1.1. <strong>Сервис</strong> — программно-аппаратный комплекс Blast, доступный через Telegram-бот ${botLink}.</p>
          <p>1.2. <strong>Услуги</strong> — автоматизированная генерация видеороликов на основе аудиоматериала и данных, предоставленных Заказчиком.</p>
          <p>1.3. <strong>Ролик</strong> — один готовый видеофайл вертикального формата, переданный Заказчику через Сервис.</p>
          <p>1.4. <strong>Генерация</strong> — запуск автоматизированной обработки материалов Заказчика после подтверждения заказа.</p>

          <h2>2. Предмет договора</h2>
          <p>2.1. Исполнитель оказывает Заказчику Услуги в объёме выбранного тарифа, а Заказчик оплачивает их в порядке, предусмотренном настоящей офертой.</p>
          <p>2.2. Услуги оказываются дистанционно. Страна оказания услуг — Российская Федерация. Результат передаётся в электронной форме через Telegram-бот.</p>
          <p>2.3. Услуги носят информационно-технический характер и не являются гарантией достижения каких-либо показателей продвижения (просмотров, охватов, подписчиков, попадания в рекомендации).</p>

          <h2>3. Заключение договора</h2>
          <p>3.1. Акцептом оферты является оплата выбранного тарифа Заказчиком. Акцепт означает полное и безоговорочное принятие условий настоящей оферты, <a href="terms.html" data-keep-language>Условий использования</a> и <a href="privacy.html" data-keep-language>Политики конфиденциальности</a>.</p>
          <p>3.2. Договор считается заключённым с момента поступления оплаты Исполнителю.</p>
          <p>3.3. До оплаты Заказчику в интерфейсе Сервиса предоставляется информация о составе, стоимости и сроках оказания Услуг (ст. 10 Закона РФ «О защите прав потребителей»).</p>

          <h2>4. Тарифы и состав услуг</h2>
          <p>4.1. Действующие тарифы:</p>
          <ul>
            <li><strong>«Бласт Trial» — 990 ₽.</strong> Разовый пакет: приём аудиоматериала и текста, автоматизированный анализ, генерация контент-плана и подборки идей, создание 5 (пяти) роликов по одному треку.</li>
            <li><strong>«Бласт» — 1 990 ₽ в месяц.</strong> Подписка с автоматическим продлением: до 100 (ста) роликов в течение оплаченного месяца.</li>
            <li><strong>«Глоу» — 7 990 ₽.</strong> Разовый пакет: до 400 (четырёхсот) роликов, до 10 (десяти) загружаемых треков, генерация роликов пачками и расширенный тайминг, а также подготовка контент-менеджером Исполнителя персонального шаблона CapCut на основе наиболее результативного формата.</li>
            <li><strong>«Импульс» — 29 990 ₽.</strong> Пакет сроком 1 (один) год: генерация роликов без количественных ограничений в рамках технических возможностей Сервиса и правил добросовестного использования, до 24 (двадцати четырёх) загружаемых треков, персональная аналитика.</li>
          </ul>
          <p>4.2. Дополнительно Исполнитель может предоставлять пробный бесплатный доступ при первом обращении к Telegram-боту. Количество бесплатных роликов отображается Заказчику в интерфейсе Telegram-бота. Условия пробного доступа являются акцией, могут быть изменены или прекращены Исполнителем в одностороннем порядке и не создают обязательств по оказанию платных Услуг.</p>
          <p>4.3. Стоимость указана в рублях Российской Федерации. Исполнитель применяет упрощённую систему налогообложения; НДС не начисляется. Кассовый чек направляется Заказчику в электронной форме в соответствии с Федеральным законом от 22.05.2003 № 54-ФЗ.</p>
          <p>4.4. Актуальные тарифы отображаются в интерфейсе Сервиса. Изменение тарифов не распространяется на уже оплаченные и не исполненные заказы.</p>
          <p>4.5. Под правилами добросовестного использования для тарифа «Импульс» понимается использование Услуг лично Заказчиком для продвижения собственных музыкальных материалов без автоматизированной перепродажи и без нагрузки, существенно превышающей типичное потребление сопоставимых Заказчиков.</p>

          <h2>5. Подписка и автоматическое продление</h2>
          <p>5.1. Тариф «Бласт» оформляется как подписка сроком 1 календарный месяц с автоматическим продлением на каждый следующий месяц путём списания стоимости с привязанной банковской карты.</p>
          <p>5.2. Заказчик вправе отказаться от автоматического продления в любой момент, направив команду <code>/cancelsubscription</code> в Telegram-боте Сервиса либо обращение на ${contact}. Отмена вступает в силу с окончания оплаченного периода; ранее оплаченный период сохраняется за Заказчиком.</p>
          <p>5.3. Неиспользованный в течение оплаченного месяца объём роликов на следующий период не переносится.</p>

          <h2>6. Порядок оказания услуг и сроки</h2>
          <p>6.1. Заказчик направляет аудиоматериал и сопроводительные данные через Telegram-бот и подтверждает параметры заказа.</p>
          <p>6.2. Генерация запускается автоматически после подтверждения заказа. Ориентировочный срок передачи готовых роликов — до 60 минут с момента запуска генерации. Предельный срок оказания Услуг по одному заказу — 3 (три) рабочих дня.</p>
          <p>6.3. При нарушении предельного срока по обстоятельствам, зависящим от Исполнителя, Заказчик вправе предъявить требования, предусмотренные ст. 28 Закона РФ «О защите прав потребителей», в том числе потребовать возврата уплаченной суммы за неоказанную часть Услуг.</p>
          <p>6.4. Услуга по конкретному ролику считается оказанной в момент передачи готового видеофайла Заказчику через Telegram-бот. Отдельный акт не составляется; надлежащим подтверждением является факт передачи файла, зафиксированный в Сервисе.</p>
          <p>6.5. Заказчик обязан самостоятельно сохранить полученные материалы. Хранение результатов на стороне Исполнителя ограничено сроками, указанными в <a href="privacy.html" data-keep-language>Политике конфиденциальности</a>.</p>

          <h2>7. Права на материалы и результат</h2>
          <p>7.1. Заказчик гарантирует наличие у него всех прав на загружаемые материалы и несёт ответственность за их нарушение.</p>
          <p>7.2. Права сторон в отношении результата генерации определяются разделами 5 и 6 <a href="terms.html" data-keep-language>Условий использования</a>.</p>

          <h2>8. Отказ от договора и возврат денежных средств</h2>
          <p>8.1. Заказчик вправе отказаться от исполнения договора в любое время до фактического оказания Услуг в полном объёме, уплатив Исполнителю часть цены пропорционально объёму фактически оказанных Услуг и возместив фактически понесённые расходы (ст. 32 Закона РФ «О защите прав потребителей», ст. 782 Гражданского кодекса РФ).</p>
          <p>8.2. Порядок расчёта возврата:</p>
          <ul>
            <li>если генерация не запускалась — возврату подлежит вся уплаченная сумма;</li>
            <li>если часть роликов уже передана Заказчику — возврату подлежит сумма за непереданные ролики, рассчитанная пропорционально их количеству в составе тарифа;</li>
            <li>по тарифу-подписке — возврату подлежит стоимость оплаченного периода за вычетом стоимости фактически переданных в этом периоде роликов;</li>
            <li>по тарифу «Импульс» — возврату подлежит стоимость пропорционально неиспользованному сроку с учётом фактически переданных роликов.</li>
          </ul>
          <p>8.3. Заявление об отказе направляется на ${contact} с указанием Telegram ID или username и сведений о платеже. Возврат осуществляется тем же способом, которым была произведена оплата, в срок не более 10 (десяти) календарных дней с даты получения требования (ст. 31 Закона РФ «О защите прав потребителей»).</p>
          <p>8.4. При обнаружении недостатков оказанных Услуг Заказчик вправе заявить требования, предусмотренные ст. 29 Закона РФ «О защите прав потребителей».</p>
          <p>8.5. Отказ Заказчика от договора не освобождает его от ответственности за нарушение п. 7.1 настоящей оферты.</p>

          <h2>9. Ответственность сторон</h2>
          <p>9.1. Стороны несут ответственность в соответствии с законодательством Российской Федерации.</p>
          <p>9.2. Исполнитель не несёт ответственности за результаты использования Заказчиком полученных материалов, а также за действия сторонних платформ, включая ограничение или удаление публикаций.</p>
          <p>9.3. Стороны освобождаются от ответственности при наступлении обстоятельств непреодолимой силы.</p>
          <p>9.4. Положения настоящего раздела не ограничивают права Заказчика-потребителя, установленные законом.</p>

          <h2>10. Персональные данные</h2>
          <p>10.1. Обработка персональных данных осуществляется в соответствии с <a href="privacy.html" data-keep-language>Политикой конфиденциальности</a>.</p>

          <h2>11. Разрешение споров</h2>
          <p>11.1. Претензия направляется на ${contact} и рассматривается в течение 10 календарных дней.</p>
          <p>11.2. При недостижении согласия спор рассматривается судом в соответствии с законодательством Российской Федерации; споры с участием потребителей — по правилам ст. 17 Закона РФ «О защите прав потребителей».</p>

          <h2>12. Срок действия и изменения оферты</h2>
          <p>12.1. Оферта действует с даты её публикации и до отзыва Исполнителем. Актуальная редакция размещается на этой странице с указанием версии и даты вступления в силу.</p>
          <p>12.2. Изменения оферты не распространяются на договоры, заключённые до вступления изменений в силу.</p>

          <h2>13. Реквизиты Исполнителя</h2>
          <p>${operatorRu}<br>Адрес: ${addressRu}<br>E-mail: ${contact}<br>Телефон: ${phoneRu}<br>Приём платежей: АО «Т-Банк» (<a href="https://tbank.ru" target="_blank" rel="noopener">tbank.ru</a>), способы оплаты — банковские карты Visa, Mastercard, МИР и сервис T‑Pay.</p>`
      },
      en: {
        title: 'Public Offer for Services',
        body: `<p>${operatorEn}, address: ${addressEn} (the "Contractor"), pursuant to Art. 437(2) of the Russian Civil Code, offers any individual with legal capacity, sole trader or legal entity (the "Customer") to enter into a services agreement on the terms below.</p>

          <h2>1. Definitions</h2>
          <p>1.1. <strong>Service</strong> — the Blast platform available through the Telegram bot ${botLink}.</p>
          <p>1.2. <strong>Services</strong> — automated generation of videos from audio and data provided by the Customer.</p>
          <p>1.3. <strong>Video</strong> — one finished vertical-format video file delivered to the Customer through the Service.</p>
          <p>1.4. <strong>Generation</strong> — the start of automated processing of the Customer's materials after order confirmation.</p>

          <h2>2. Subject matter</h2>
          <p>2.1. The Contractor provides the Services within the scope of the selected plan and the Customer pays for them as set out in this offer.</p>
          <p>2.2. The Services are provided remotely. The country of provision is the Russian Federation. Output is delivered electronically through the Telegram bot.</p>
          <p>2.3. The Services are informational and technical in nature and are not a guarantee of any promotional metrics (views, reach, followers, placement in recommendations).</p>

          <h2>3. Formation of the contract</h2>
          <p>3.1. Acceptance of this offer is effected by payment for the selected plan. Acceptance means full and unconditional acceptance of this offer, the <a href="terms.html" data-keep-language>Terms of Service</a> and the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>
          <p>3.2. The contract is concluded when payment is received by the Contractor.</p>
          <p>3.3. Before payment, the Service interface provides the Customer with information on the contents, price and timing of the Services (Art. 10 of the Russian Law "On Protection of Consumer Rights").</p>

          <h2>4. Plans and contents</h2>
          <p>4.1. Current plans:</p>
          <ul>
            <li><strong>"Blast Trial" — RUB 990.</strong> One-off package: intake of audio and text, automated analysis, content plan and idea selection, and creation of 5 (five) videos for one track.</li>
            <li><strong>"Blast" — RUB 1,990 per month.</strong> Auto-renewing subscription: up to 100 (one hundred) videos within the paid month.</li>
            <li><strong>"Glow" — RUB 7,990.</strong> One-off package: up to 400 (four hundred) videos, up to 10 (ten) uploaded tracks, batch generation and extended timing, plus preparation by the Contractor's content manager of a personal CapCut template based on the best-performing format.</li>
            <li><strong>"Impulse" — RUB 29,990.</strong> One-year package: generation without a fixed video limit within the technical capacity of the Service and fair use rules, up to 24 (twenty-four) uploaded tracks, and personal analytics.</li>
          </ul>
          <p>4.2. The Contractor may additionally provide free trial access on first contact with the Telegram bot. The number of free videos is shown to the Customer in the Telegram bot interface. Trial access is a promotion, may be changed or withdrawn unilaterally by the Contractor, and creates no obligation to provide paid Services.</p>
          <p>4.3. Prices are stated in Russian roubles. The Contractor applies the simplified taxation regime; VAT is not charged. A fiscal receipt is delivered to the Customer electronically in accordance with Federal Law No. 54-FZ of 22 May 2003.</p>
          <p>4.4. Current prices are displayed in the Service interface. Price changes do not apply to orders already paid for and not yet fulfilled.</p>
          <p>4.5. Fair use for the "Impulse" plan means use of the Services by the Customer personally to promote their own musical materials, without automated resale and without load materially exceeding typical consumption by comparable Customers.</p>

          <h2>5. Subscription and auto-renewal</h2>
          <p>5.1. The "Blast" plan is a subscription for 1 calendar month, automatically renewed each following month by charging the linked bank card.</p>
          <p>5.2. The Customer may cancel auto-renewal at any time using the <code>/cancelsubscription</code> command in the Service's Telegram bot or by writing to ${contact}. Cancellation takes effect at the end of the paid period; the period already paid for remains available to the Customer.</p>
          <p>5.3. Video allowance unused within a paid month does not carry over to the next period.</p>

          <h2>6. Performance and timing</h2>
          <p>6.1. The Customer submits audio and accompanying data through the Telegram bot and confirms the order parameters.</p>
          <p>6.2. Generation starts automatically after order confirmation. Finished videos are typically delivered within 60 minutes of the start of generation. The maximum period for performing the Services under a single order is 3 (three) business days.</p>
          <p>6.3. If the maximum period is exceeded due to circumstances attributable to the Contractor, the Customer may bring the claims provided by Art. 28 of the Russian Law "On Protection of Consumer Rights", including a refund for the part of the Services not performed.</p>
          <p>6.4. The Services in respect of a given video are deemed performed when the finished video file is delivered to the Customer through the Telegram bot. No separate acceptance certificate is executed; delivery recorded in the Service is sufficient evidence.</p>
          <p>6.5. The Customer must save the delivered materials. Retention of output on the Contractor's side is limited to the periods stated in the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>

          <h2>7. Rights in materials and output</h2>
          <p>7.1. The Customer warrants that they hold all rights in the uploaded materials and is liable for any infringement.</p>
          <p>7.2. The parties' rights in the generated output are set out in Sections 5 and 6 of the <a href="terms.html" data-keep-language>Terms of Service</a>.</p>

          <h2>8. Withdrawal and refunds</h2>
          <p>8.1. The Customer may withdraw from the contract at any time before the Services have been fully performed, paying the Contractor a share of the price proportionate to the Services actually performed and reimbursing costs actually incurred (Art. 32 of the Russian Law "On Protection of Consumer Rights"; Art. 782 of the Russian Civil Code).</p>
          <p>8.2. Refunds are calculated as follows:</p>
          <ul>
            <li>if generation has not started — the full amount paid is refunded;</li>
            <li>if some videos have already been delivered — the amount for undelivered videos is refunded, calculated pro rata to their number within the plan;</li>
            <li>for the subscription plan — the price of the paid period less the value of videos actually delivered in that period is refunded;</li>
            <li>for the "Impulse" plan — a refund proportionate to the unused term, taking into account videos actually delivered.</li>
          </ul>
          <p>8.3. Withdrawal requests are sent to ${contact} stating the Telegram ID or username and payment details. Refunds are made by the same method used for payment within no more than 10 (ten) calendar days of receipt of the request (Art. 31 of the Russian Law "On Protection of Consumer Rights").</p>
          <p>8.4. If defects in the Services are found, the Customer may bring the claims provided by Art. 29 of the Russian Law "On Protection of Consumer Rights".</p>
          <p>8.5. Withdrawal does not release the Customer from liability for breach of clause 7.1.</p>

          <h2>9. Liability</h2>
          <p>9.1. The parties are liable in accordance with Russian law.</p>
          <p>9.2. The Contractor is not liable for the results of the Customer's use of the delivered materials, nor for the actions of third-party platforms, including restriction or removal of posts.</p>
          <p>9.3. The parties are released from liability in the event of force majeure.</p>
          <p>9.4. This Section does not limit the statutory rights of a Customer who is a consumer.</p>

          <h2>10. Personal data</h2>
          <p>10.1. Personal data is processed in accordance with the <a href="privacy.html" data-keep-language>Privacy Policy</a>.</p>

          <h2>11. Disputes</h2>
          <p>11.1. Claims are sent to ${contact} and reviewed within 10 calendar days.</p>
          <p>11.2. Failing agreement, disputes are resolved in court under Russian law; consumer disputes are heard under Art. 17 of the Russian Law "On Protection of Consumer Rights".</p>

          <h2>12. Term and amendments</h2>
          <p>12.1. This offer is effective from its publication date until revoked by the Contractor. The current version is published on this page with its version number and effective date.</p>
          <p>12.2. Amendments do not apply to contracts concluded before the amendments take effect.</p>

          <h2>13. Contractor details</h2>
          <p>${operatorEn}<br>Address: ${addressEn}<br>E-mail: ${contact}<br>Phone: +7 (910) 572-49-67<br>Payments are accepted by T-Bank JSC (<a href="https://tbank.ru" target="_blank" rel="noopener">tbank.ru</a>); accepted methods are Visa, Mastercard and MIR bank cards and the T‑Pay service.</p>`
      }
    },

    contacts: {
      ru: {
        title: 'Контакты и реквизиты',
        body: `<h2>Служба поддержки</h2>
          <p>E-mail: ${contact}<br>Телефон: ${phoneRu}<br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a><br>Telegram-бот Сервиса: ${botLink}</p>
          <p>Обращения принимаются круглосуточно; ответ направляется в течение 1 рабочего дня.</p>

          <h2>Вопросы обработки персональных данных</h2>
          <p>Запросы субъектов персональных данных, отзыв согласия и запросы на удаление данных (в том числе данных, полученных через подключённые платформы) направляются на ${contact}. Срок ответа — 10 рабочих дней с возможностью продления не более чем на 5 рабочих дней.</p>

          <h2>Претензии и споры</h2>
          <p>Претензии по качеству услуг и возвратам направляются на ${contact} и рассматриваются в течение 10 календарных дней.</p>

          <h2>Оператор и исполнитель</h2>
          <p>${operatorRu}<br>Адрес: ${addressRu}</p>

          <h2>Платёжный партнёр</h2>
          <p>Приём платежей осуществляется АО «Т-Банк» (<a href="https://tbank.ru" target="_blank" rel="noopener">tbank.ru</a>). Способы оплаты: банковские карты Visa, Mastercard, МИР и сервис T‑Pay. Кассовый чек направляется в электронной форме в соответствии с Федеральным законом от 22.05.2003 № 54-ФЗ.</p>

          <h2>Документы</h2>
          <p><a href="terms.html" data-keep-language>Условия использования</a> · <a href="privacy.html" data-keep-language>Политика конфиденциальности</a> · <a href="cookies.html" data-keep-language>Политика cookie</a> · <a href="personal-data-consent.html" data-keep-language>Согласие на обработку данных</a> · <a href="offer.html" data-keep-language>Публичная оферта</a></p>`
      },
      en: {
        title: 'Contacts and Legal Details',
        body: `<h2>Support</h2>
          <p>E-mail: ${contact}<br>Phone: +7 (910) 572-49-67<br>Telegram: <a href="https://t.me/impulsemarketing" target="_blank" rel="noopener">@impulsemarketing</a><br>Service Telegram bot: ${botLink}</p>
          <p>Requests are accepted around the clock; a reply is sent within 1 business day.</p>

          <h2>Personal data enquiries</h2>
          <p>Data subject requests, withdrawal of consent and data deletion requests (including data received through connected platforms) are sent to ${contact}. The response period is 10 business days, extendable by no more than 5 business days.</p>

          <h2>Claims and disputes</h2>
          <p>Claims regarding service quality and refunds are sent to ${contact} and reviewed within 10 calendar days.</p>

          <h2>Controller and contractor</h2>
          <p>${operatorEn}<br>Address: ${addressEn}</p>

          <h2>Payment partner</h2>
          <p>Payments are accepted by T-Bank JSC (<a href="https://tbank.ru" target="_blank" rel="noopener">tbank.ru</a>). Accepted methods: Visa, Mastercard and MIR bank cards and the T‑Pay service. A fiscal receipt is delivered electronically in accordance with Federal Law No. 54-FZ of 22 May 2003.</p>

          <h2>Documents</h2>
          <p><a href="terms.html" data-keep-language>Terms of Service</a> · <a href="privacy.html" data-keep-language>Privacy Policy</a> · <a href="cookies.html" data-keep-language>Cookie Policy</a> · <a href="personal-data-consent.html" data-keep-language>Personal Data Consent</a> · <a href="offer.html" data-keep-language>Public Offer</a></p>`
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
