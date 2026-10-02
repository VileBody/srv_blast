import type { ReactNode } from 'react';
import type { FunnelQuestion, FunnelQuota, FunnelRules, VideoVersion } from '../lib/types';
import { LimitsPopoutCard, TrackLimitBar } from '../components/funnel/LimitsPopout';
import { QuizPanel, UnlimitedPanel, type UnlimitedHandlers, type UnlimitedStep, type UnlimitedView } from '../components/funnel/panels';
import { VideoRatingRow } from '../components/funnel/parts';

/*
 * Витрина воронки после генерации (docs/BOT_TO_WEB_FLOW.md, разделы 4–5): каждая модалка
 * и каждое состояние рядом, теми же компонентами, что в приложении. Только dev-сборка
 * (/dev/funnel), данные ненастоящие. Строки витрины не переводятся — как в /dev/kit.
 */

const NOW = Date.parse('2026-10-03T12:00:00Z');
const IN = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

const RULES: FunnelRules = { batchCap: 5, cooldownHours: 4, firstDayBatches: 2, dailyVideos: 5, tripwirePriceRub: 399, tripwireBatchCap: 25 };

const Q1: FunnelQuestion = {
  id: 'q1',
  text: 'Сколько роликов в месяц у тебя выходит в TikTok?',
  options: [
    { id: 'none', label: 'Не выкладываю совсем' },
    { id: '1_10', label: '1–10' },
    { id: '10_30', label: '10–30' },
    { id: '30_plus', label: '30+' }
  ],
  next: { none: 'q2', '1_10': 'q2', '10_30': 'q2', '30_plus': 'q2' }
};
const Q2: FunnelQuestion = {
  id: 'q2',
  text: 'Монтируешь как — сам, с чьей-то помощью, или пока вообще не монтируешь?',
  options: [
    { id: 'self', label: 'Сам' },
    { id: 'helper', label: 'С помощью монтажёра/сервиса' },
    { id: 'no_edit', label: 'Не монтирую — ролики не делаю' }
  ],
  next: { self: 'q2a', helper: 'q2b', no_edit: 'q3' }
};
const BRIDGE = 'Артисты, которые сейчас растут в стримах, выкладывают 1–2 ролика в день. Не потому что сидят в монтаже часами — а потому что нашли способ делать это быстро. Держи — как это устроено.';

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
  onRate: noop, onReasons: noop, onAnswer: noop, onMethodology: noop, onNext: noop, onChannelOpen: noop,
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

export function FunnelShowcasePage() {
  return (
    <main className="min-h-dvh bg-bg px-[16px] py-[48px] text-text">
      <div className="mx-auto max-w-[1220px]">
        <h1 className="text-ui-32 font-[400]">Воронка после генерации</h1>
        <p className="mt-[8px] max-w-[72ch] text-ui-16 text-text-60">
          Все шаги и состояния из docs/BOT_TO_WEB_FLOW.md теми же компонентами, что на сайте. В приложении это окна поверх страницы.
        </p>

        <Group title="Модалка A: как делаешь контент" note="Через 2,5 с после запуска генерации, один раз на батч, только бесплатным и пока квиз не пройден. Ответ одним нажатием, вопрос меняется сам.">
          <State label="Вопрос 1 из 4">
            <QuizPanel view={{ kind: 'question', question: Q1, index: 0, total: 4 }} onAnswer={noop} onSkip={noop} onMethodology={noop} onClose={noop} />
          </State>
          <State label="Вопрос 2, ответ отправляется">
            <QuizPanel view={{ kind: 'question', question: Q2, index: 1, total: 4, pendingId: 'self' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onClose={noop} />
          </State>
          <State label="Финал: мостик по ветке и методичка">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'idle' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onClose={noop} />
          </State>
          <State label="Методичка отправлена в Telegram">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'sent' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onClose={noop} />
          </State>
          <State label="Бот не запущен: просим открыть бота">
            <QuizPanel view={{ kind: 'done', bridge: BRIDGE, methodology: 'needBot', botLink: '#' }} onAnswer={noop} onSkip={noop} onMethodology={noop} onClose={noop} />
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
            <div className="rounded-r15 bg-panel"><VideoRatingRow score={4} reasons={['transitions']} onRate={noop} onReasons={noop} fixHref="#" /></div>
          </State>
        </Group>

        <Group title="Модалка B: оцени ролики и получи безлимит" note="Ветка 7+: оценка, квиз (если пропущен), методичка, питч, два шага, безлимит. Ветка ниже 7: вместо питча «что докрутить».">
          <State label="Оценка роликов">
            <UnlimitedPanel view={view('rate', { ratings: { v1: { score: 8, reasons: [] } } })} on={ON} />
          </State>
          <State label="Низкая ветка: что докрутить">
            <UnlimitedPanel view={view('improve', { reasons: ['footage'], fixHref: '#' })} on={ON} />
          </State>
          <State label="Квиз внутри (если модалку A пропустили)">
            <UnlimitedPanel view={view('quiz', { quiz: { kind: 'question', question: Q1, index: 0, total: 4 } })} on={ON} />
          </State>
          <State label="Методичка">
            <UnlimitedPanel view={view('methodology', { bridge: BRIDGE, methodology: 'idle' })} on={ON} />
          </State>
          <State label="Питч: лестница бесплатно, 399 ₽, подписка">
            <UnlimitedPanel view={view('pitch')} on={ON} />
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
          <State label="Безлимит уже на другом треке">
            <UnlimitedPanel view={view('otherTrack', { otherTrackTitle: 'Последний танец' })} on={ON} />
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
