import type { ReactNode } from 'react';
import type { FunnelQuestion, FunnelQuota, FunnelRules, VideoVersion } from '../lib/types';
import { LimitsPopoutCard, TrackLimitBar } from '../components/funnel/LimitsPopout';
import { QuizPanel, UnlimitedPanel, type UnlimitedHandlers, type UnlimitedStep, type UnlimitedView } from '../components/funnel/panels';
import { VideoRatingRow } from '../components/funnel/parts';
import { PitchFlow } from '../components/funnel/PitchFlow';
import { quizPath } from '../components/funnel/panels';
import { useFunnelState } from '../components/funnel/useFunnel';
import type { FunnelState } from '../lib/types';

/*
 * Витрина воронки после генерации (docs/BOT_TO_WEB_FLOW.md, разделы 4–5): каждая модалка
 * и каждое состояние рядом, теми же компонентами, что в приложении. Только dev-сборка
 * (/dev/funnel), данные ненастоящие. Строки витрины не переводятся — как в /dev/kit.
 */

const NOW = Date.parse('2026-10-03T12:00:00Z');
const IN = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

const RULES: FunnelRules = { batchCap: 5, cooldownHours: 4, firstDayBatches: 2, dailyVideos: 5, tripwirePriceRub: 399, tripwireBatchCap: 25 };

/* Вопросы квиза — с бэка, если он поднят (тексты сайта); иначе — копия для витрины. */
const QUESTIONS: FunnelQuestion[] = [
  {
    id: 'q1',
    text: 'Сколько роликов в месяц у тебя выходит в TikTok?',
    options: [
      { id: 'none', label: 'Не выкладываю совсем' },
      { id: '1_10', label: '1–10' },
      { id: '10_30', label: '10–30' },
      { id: '30_plus', label: '30+' }
    ],
    next: { none: 'q2', '1_10': 'q2', '10_30': 'q2', '30_plus': 'q2' }
  },
  {
    id: 'q2',
    text: 'Как ты монтируешь ролики?',
    options: [
      { id: 'self', label: 'Сам' },
      { id: 'helper', label: 'С монтажёром или сервисом' },
      { id: 'no_edit', label: 'Не монтирую' }
    ],
    next: { self: 'q2a', helper: 'q2b', no_edit: 'q3' }
  },
  {
    id: 'q2a',
    text: 'Сколько времени уходит на один ролик?',
    options: [
      { id: 'lt_hour', label: 'Меньше часа' },
      { id: '1_3h', label: '1–3 часа' },
      { id: 'half_day', label: 'Полдня и больше' },
      { id: 'a_lot', label: 'Не считал, но точно много' }
    ],
    next: { lt_hour: 'q3', '1_3h': 'q3', half_day: 'q3', a_lot: 'q3' }
  },
  {
    id: 'q2b',
    text: 'Сколько в месяц уходит на монтаж?',
    options: [
      { id: 'zero', label: 'Ничего не трачу' },
      { id: '2_5k', label: '2 000–5 000₽' },
      { id: '5_10k', label: '5 000–10 000₽' },
      { id: '10k_plus', label: '10 000₽+' }
    ],
    next: { zero: 'q3', '2_5k': 'q3', '5_10k': 'q3', '10k_plus': 'q3' }
  },
  {
    id: 'q3',
    text: 'Что мешает выкладывать чаще?',
    options: [
      { id: 'time', label: 'Не хватает времени' },
      { id: 'money', label: 'Не хватает денег на монтаж' },
      { id: 'ideas', label: 'Не хватает идей' },
      { id: 'meaning', label: 'В целом не вижу смысла' }
    ],
    next: { time: '', money: '', ideas: '', meaning: '' }
  }
];
/** Ответы, по которым витрина показывает путь: «Сам» → вопрос про время. */
const PATH_ANSWERS: Record<string, { id: string }> = { q2: { id: 'self' } };
const BRIDGE = 'Растут те, кто выкладывает 1–2 ролика в день. Рассказываем, как успевать без часов в монтаже.';

const VIDEOS: VideoVersion[] = [1, 2, 3].map((index) => ({
  id: `v${index}`,
  index,
  status: 'COMPLETED',
  progress: 100,
  source: 'Тревожная природа',
  subtitleStyle: 'Jakson',
  hook: 'none'
} as unknown as VideoVersion));

const QUOTA_OK: FunnelQuota = { allowed: true, reason: 'ok', maxVideos: 5, batchCap: 5, availableAt: null, tripwire: false, tripwirePriceRub: 399, tripwireBatchCap: 25 };
const QUOTA_COOLDOWN: FunnelQuota = { ...QUOTA_OK, allowed: false, reason: 'cooldown', maxVideos: 0, availableAt: IN(161) };
const QUOTA_TRIPWIRE: FunnelQuota = { ...QUOTA_OK, tripwire: true, maxVideos: 25, batchCap: 25 };

const noop = () => {};
const ON: UnlimitedHandlers = {
  onRate: noop, onReasons: noop, onAnswer: noop, onMethodology: noop, onMethodologyBotOpened: noop, onNext: noop, onChannelOpen: noop,
  onChannelCheck: noop, onManager: noop, onUnlock: noop, onGenerate: noop, onClose: noop
};

const HIGH: UnlimitedStep[] = ['rate', 'quiz', 'methodology', 'pitch', 'actions', 'done'];
const LOW: UnlimitedStep[] = ['rate', 'improve', 'quiz', 'methodology', 'actions', 'done'];

function view(step: UnlimitedStep, extra: Partial<UnlimitedView> = {}): UnlimitedView {
  return {
    step,
    steps: step === 'improve' ? LOW : HIGH,
    trackTitle: 'Нет любви',
    rules: RULES,
    videos: VIDEOS,
    ratings: {},
    channelLink: '#',
    managerLink: '#',
    managerCode: 'B-4F2K1C',
    ...extra
  };
}

function Group({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="mt-[56px] first:mt-0">
      <h2 className="text-ui-32 font-[400] text-text">{title}</h2>
      {note && <p className="mt-[8px] max-w-[72ch] text-ui-16 text-text-60">{note}</p>}
      <div className="mt-[24px] grid grid-cols-[repeat(auto-fill,minmax(min(100%,560px),1fr))] items-start gap-[32px]">{children}</div>
    </section>
  );
}

function State({ label, children }: { label: string; children: ReactNode }) {
  return (
    <figure className="flex flex-col gap-[12px]">
      <figcaption className="text-ui-14 text-text-60">{label}</figcaption>
      {children}
    </figure>
  );
}

/* Фон под окном у кружка лимитов — затемнённая карточка батча, как в приложении. */
function PopoutStage({ children }: { children: ReactNode }) {
  return <div className="flex justify-end rounded-r25 bg-card-2 p-[24px]">{children}</div>;
}

function survey(answers: Record<string, [string, string]>, branch = ''): FunnelState['survey'] {
  return {
    answers: Object.fromEntries(Object.entries(answers).map(([q, [id, label]]) => [q, { id, label }])),
    completed: true,
    branch,
    bridge: null
  };
}

function Pitch({ s }: { s?: FunnelState['survey'] }) {
  return <PitchFlow survey={s} progress={{ total: 5, current: 3 }} onNext={noop} onClose={noop} />;
}

export function FunnelShowcasePage() {
  const live = useFunnelState().data?.questions;
  const questions = live?.length ? live : QUESTIONS;
  const path = quizPath(questions, PATH_ANSWERS);
  const ordered = [...path, ...questions.map((q) => q.id).filter((id) => !path.includes(id))];
  const q1 = questions[0];
  return (
    <main className="min-h-dvh bg-bg px-[16px] py-[48px] text-text">
      <div className="mx-auto max-w-[1220px]">
        <h1 className="text-ui-32 font-[400]">Воронка после генерации</h1>
        <p className="mt-[8px] max-w-[72ch] text-ui-16 text-text-60">
          Все шаги и состояния из docs/BOT_TO_WEB_FLOW.md теми же компонентами, что на сайте. В приложении это окна поверх страницы.
        </p>

        <Group title="Модалка A: как делаешь контент" note="Через 2,5 с после запуска генерации, один раз на батч, только бесплатным и пока квиз не пройден. Ответ одним нажатием, вопрос меняется сам.">
          {ordered.map((id) => {
            const question = questions.find((q) => q.id === id) as FunnelQuestion;
            const index = path.includes(id) ? path.indexOf(id) : path.indexOf('q2a');
            return (
              <State key={id} label={`Вопрос ${id}${id === 'q2b' ? ' (ветка «С монтажёром», вместо q2a)' : ''}`}>
                <QuizPanel view={{ kind: 'question', question, index, total: path.length }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
              </State>
            );
          })}
          <State label="Ответ отправляется">
            <QuizPanel view={{ kind: 'question', question: questions[1] ?? q1, index: 1, total: path.length, pendingId: 'self' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Финал: мостик по ветке, одна кнопка «Получить» (после отправки окно закрывается, тост)">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'idle' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Методичка отправляется">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'sending' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Бот не запущен: просим открыть бота">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'needBot', botLink: 'https://t.me/blast808bot' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Бота открыли: ждём повторного «Получить»">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'botOpened', botLink: 'https://t.me/blast808bot' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Сбой отправки: повтор или «Закрыть»">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'error' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
          <State label="Отправка не настроена (503): только «Закрыть»">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'unavailable' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onBotOpened={noop} onClose={noop} />
          </State>
        </Group>

        <Group title="Оценка под каждым роликом" note="В строке готового ролика на странице генерации и батча. Первая оценка 7+ сразу открывает модалку B, низкая спрашивает причину и ведёт на таймлайн.">
          <State label="Ещё не оценён">
            <div className="rounded-r15 bg-panel"><VideoRatingRow score={null} reasons={[]} onRate={noop} onReasons={noop} /></div>
          </State>
          <State label="Высокая оценка">
            <div className="rounded-r15 bg-panel"><VideoRatingRow score={8} reasons={[]} onRate={noop} onReasons={noop} /></div>
          </State>
          <State label="Низкая оценка: причины и путь к правке">
            <div className="rounded-r15 bg-panel"><VideoRatingRow score={4} reasons={['transitions']} onRate={noop} onReasons={noop} onFix={noop} /></div>
          </State>
        </Group>

        <Group title="Модалка B: оцени ролики и получи безлимит" note="Ветка 7+: оценка, квиз (если пропущен), методичка, питч, два шага, безлимит. Ветка ниже 7: вместо питча «что докрутить».">
          <State label="Оценка роликов">
            <UnlimitedPanel view={view('rate', { ratings: { v1: { score: 8, reasons: [] } } })} on={ON} />
          </State>
          <State label="Низкая ветка: что докрутить">
            <UnlimitedPanel view={view('improve', { reasons: ['footage'], canFix: true })} on={{ ...ON, onFix: noop }} />
          </State>
          <State label="Квиз внутри (если модалку A пропустили)">
            <UnlimitedPanel view={view('quiz', { quiz: { kind: 'question', question: q1, index: 0, total: path.length } })} on={ON} />
          </State>
          <State label="Методичка">
            <UnlimitedPanel view={view('methodology', { bridge: BRIDGE, methodology: 'idle' })} on={ON} />
          </State>
          <State label="Два шага: ничего не сделано">
            <UnlimitedPanel view={view('actions', { channel: 'todo', manager: 'todo' })} on={ON} />
          </State>
          <State label="Подписку не нашли, менеджеру написал">
            <UnlimitedPanel view={view('actions', { channel: 'missing', manager: 'done' })} on={ON} />
          </State>
          <State label="Оба шага готовы">
            <UnlimitedPanel view={view('actions', { channel: 'done', manager: 'done' })} on={ON} />
          </State>
          <State label="Безлимит открыт">
            <UnlimitedPanel view={view('done', { quota: QUOTA_OK })} on={ON} />
          </State>
          <State label="Безлимит открыт, лимит на сейчас исчерпан">
            <UnlimitedPanel view={view('done', { quota: QUOTA_COOLDOWN })} on={ON} />
          </State>
          <State label="Безлимит уже на другом треке: ничего не выбрано">
            <UnlimitedPanel view={view('otherTrack', { otherTrackTitle: 'Последний танец' })} on={ON} />
          </State>
          <State label="Выбран «Любой трек без лимитов»: кнопка «Купить»">
            <UnlimitedPanel view={view('otherTrack', { otherTrackTitle: 'Последний танец', tier: 'tripwire' })} on={ON} />
          </State>
          <State label="Выбран бесплатный безлимит: «Генерировать дальше»">
            <UnlimitedPanel view={view('otherTrack', { otherTrackTitle: 'Последний танец', tier: 'free' })} on={ON} />
          </State>
        </Group>

        <Group title="Питч в модалке B" note="Задача шага: продать Бласт. Довод по ответу на Q3, цифры человека из Q2a (время) и Q2b (деньги). Внизу «Изучить тариф» и стрелка к бесплатному безлимиту.">
          <State label="Q3 «Не хватает времени», Q2a «1–3 часа»">
            <Pitch s={survey({ q2: ['self', 'Сам'], q2a: ['1_3h', '1–3 часа'], q3: ['time', 'Не хватает времени'] }, 'time')} />
          </State>
          <State label="Q3 «Не хватает денег», Q2b «5 000–10 000₽»">
            <Pitch s={survey({ q2: ['helper', 'С монтажёром или сервисом'], q2b: ['5_10k', '5 000–10 000₽'], q3: ['money', 'Не хватает денег на монтаж'] }, 'money')} />
          </State>
          <State label="Q3 «Не хватает идей»">
            <Pitch s={survey({ q3: ['ideas', 'Не хватает идей'] }, 'ideas')} />
          </State>
          <State label="Q3 «Не вижу смысла»">
            <Pitch s={survey({ q3: ['meaning', 'В целом не вижу смысла'] }, 'meaning')} />
          </State>
          <State label="Квиз пропущен: общий довод">
            <Pitch />
          </State>
        </Group>

        <Group title="Кружок лимитов: безлимит на трек" note="Третья строка поповера по ховеру и окно, которое само всплывает у кружка, когда лимит кончился. Один раз на каждое исчерпание.">
          <State label="Поповер: доступно, перезарядка, без лимитов">
            <div className="flex w-[360px] max-w-full flex-col gap-[20px] rounded-r15 bg-grad-soft-20 p-[24px]">
              <span className="text-ui-20 text-text">Лимиты</span>
              <TrackLimitBar trackTitle="Нет любви" quota={QUOTA_OK} now={NOW} />
              <TrackLimitBar trackTitle="Нет любви" quota={QUOTA_COOLDOWN} now={NOW} />
              <TrackLimitBar trackTitle="Нет любви" quota={QUOTA_TRIPWIRE} now={NOW} />
            </div>
          </State>
          <State label="Окно: перезарядка 4 часа + трипваер">
            <PopoutStage>
              <LimitsPopoutCard variant="cooldown" trackTitle="Нет любви" availableAt={IN(161)} rules={RULES} onBuy={noop} onClose={noop} now={NOW} />
            </PopoutStage>
          </State>
          <State label="Окно: лимит на сегодня">
            <PopoutStage>
              <LimitsPopoutCard variant="daily" trackTitle="Нет любви" availableAt={IN(612)} rules={RULES} onBuy={noop} onClose={noop} now={NOW} />
            </PopoutStage>
          </State>
          <State label="Окно: бесплатные ролики кончились, безлимит не открыт">
            <PopoutStage>
              <LimitsPopoutCard variant="creditsOut" rules={RULES} onUnlock={noop} onClose={noop} now={NOW} />
            </PopoutStage>
          </State>
        </Group>
      </div>
    </main>
  );
}
