import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { cn } from '../lib/cn';
import { FigIcon } from '../components/ui/FigIcon';
import { TiktokButton } from '../components/ui/TiktokButton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { useToast } from '../contexts/ToastContext';
import type { AnalysisDimension, IterationAnalysis } from '../lib/types';
import {
  DIMENSION_KEY,
  DIMENSION_TEST_PARAM,
  averageViews,
  leaderBars,
  leadingDimension,
  nextToTest,
  verdictText,
  verdictTone
} from '../lib/analysis';

/*
 * Аналитика (Figma W48 — данных ещё нет, W60 — есть) — один экран, два состояния.
 * Две карточки 1192: «Статистика» 379 и «Эволюция контента» 505 (шаг 20; 60..964).
 *
 * Смысл экрана: Blast сравнивает настройки роликов между собой и предлагает следующую
 * итерацию — что зафиксировать (сработало) и что тестировать дальше. Пока роликов мало,
 * сравнивать нечего → показываем сбор данных (W48).
 *
 * Цифры приходят из TikTok Display API (scope video.list), поэтому без подключённого
 * аккаунта экран пустой by design — предлагаем подключить.
 */

const gradLight = {
  backgroundImage: 'linear-gradient(184deg, #f6f5fd 8.5%, rgba(246,245,253,0.8) 94.6%)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

const gradMainText = {
  backgroundImage: 'var(--grad-main)',
  WebkitBackgroundClip: 'text',
  backgroundClip: 'text'
} as const;

/*
 * Общего порога «10 роликов» больше нет: измерения созревают неодновременно (где-то человек
 * варьировал футаж при одном стиле субтитров, где-то наоборот), и сколько роликов нужно,
 * считает бэк по каждому измерению отдельно — `analysis.videosNeeded`. Это число и есть
 * ответ на «сколько ждать».
 */

/* Бейдж тренда: h30 r8 белый; стрелка мельче (10) и близко к цифре (gap 4), содержимое
   центрируется по ширине пила — фикс-ширины нет, пил облегает контент с симметричным паддингом. */
function TrendBadge({ value }: { value: string }) {
  return (
    <span className="inline-flex h-[30px] items-center justify-center gap-[4px] rounded-[8px] bg-[#f6f5fd] px-[12px]">
      <FigIcon name="st-trend-arrow.svg" h={10} className="-rotate-90" />
      <span className="text-[16px] font-[400] leading-none text-[#04ba38]">{value}</span>
    </span>
  );
}

/** Карточка стата 357×192 (Figma 770:127): иконка+заголовок, число 96, единица, бейдж тренда */
function StatCard({ icon, title, value, unit, trend }: {
  icon: string;
  title: string;
  value: string;
  unit: string;
  trend?: string;
}) {
  return (
    <div className="relative h-[192px] min-w-0 flex-1 rounded-r15 bg-grad-soft-20">
      <span className="absolute left-[28px] top-[28px] flex items-center gap-[8px]">
        <FigIcon name={icon} h={14} />
        <span className="whitespace-nowrap text-[24px] font-[350] leading-none text-transparent" style={gradLight}>{title}</span>
      </span>
      {trend && <span className="absolute right-[28px] top-[28px]"><TrendBadge value={trend} /></span>}
      {/* цифра центрируется в зоне под заголовком (top-[72px]..низ): отступы сверху/снизу примерно
          равны. items-baseline + leading-none раньше уводили глиф вверх, к заголовку. */}
      <span className="absolute inset-x-[28px] bottom-0 top-[72px] flex items-center gap-[12px]">
        <span className="text-[96px] font-[350] leading-[0.86] text-transparent" style={gradLight}>{value}</span>
        <span className="translate-y-[18px] text-[24px] font-[350] leading-none text-transparent" style={gradLight}>{unit}</span>
      </span>
    </div>
  );
}

/**
 * Данных пока мало (Figma W48). Ровно две строки: что мешает прямо сейчас и что человек
 * получит, когда данные наберутся. Плюс чипы-счётчики.
 *
 * Состояний три, и текст у каждого свой — общая формулировка «осталось N» врала в двух
 * случаях из трёх:
 * - ничего не выложено: сравнивать нечего в принципе, просмотры появляются только после
 *   публикации в TikTok;
 * - выложено, но все ролики на одинаковых настройках: нужны не ролики, а разнообразие;
 * - выложено с вариациями, но какого-то значения ещё мало: вот тут «осталось N» честно.
 *
 * Срок в днях НЕ показываем: каденс публикаций задаёт человек, а не сервис, и «примерно
 * N дней» было выдумкой на ровном месте.
 */
function CollectingPanel({ analysis, created }: { analysis?: IterationAnalysis | null; created: number }) {
  const { t } = useTranslation();
  const posted = analysis?.videosAnalyzed ?? 0;
  const left = analysis?.videosNeeded ?? 0;
  const perValue = analysis?.minVideosPerValue ?? 2;
  const sameSetup = posted > 0 && Boolean(analysis?.dimensions.every((item) => item.blockedBy === 'single_value'));
  const state = posted === 0 ? 'nothing' : sameSetup ? 'same' : 'left';

  const headline = {
    nothing: t('stats.collectingNothingTitle'),
    same: t('stats.collectingSameTitle'),
    left: t('stats.collectingLeftTitle', { count: left })
  }[state];
  const explain = {
    nothing: t('stats.collectingNothingText'),
    same: t('stats.collectingSameText', { count: perValue }),
    left: t('stats.collectingLeftText', { count: perValue })
  }[state];

  return (
    <div className="flex h-[279px] flex-col items-center justify-center rounded-r15 bg-grad-soft-10 px-[40px] text-center">
      <p className="flex items-center gap-[10px] text-[24px] font-[400] leading-[29px] text-transparent" style={gradLight}>
        {t('stats.collecting')}
        <FigIcon name="icon-bolt.svg" h={18} />
      </p>
      <p className="mt-[18px] max-w-[560px] text-balance text-[20px] font-[400] leading-[26px] text-text">{headline}</p>
      <p className="mt-[12px] max-w-[560px] text-balance text-[16px] font-[400] leading-[21px] text-text-60">{explain}</p>
      <span className="mt-[22px] flex h-[43px] items-center gap-[16px] rounded-r10 bg-grad-soft-20 px-[15px] text-[16px] font-[400] leading-none text-text-80">
        <span>{t('stats.chipCreated', { n: created })}</span>
        <span className="text-text-60">|</span>
        <span>{t('stats.chipPublished', { n: posted })}</span>
        {/* «осталось N» показываем только когда это правда про количество */}
        {state === 'left' && (
          <>
            <span className="text-text-60">|</span>
            <span>{t('stats.chipLeft', { n: left })}</span>
          </>
        )}
      </span>
    </div>
  );
}

/** Сравнение (Figma 770:229): бары прижаты к низу, высота ∝ просмотрам; средний (белый)
    бар нахлёстывает на победителя (фиолетовый) справа. Имя — над баром, просмотры — внутри;
    подписи центрируются по своему бару → корректно в любой локали. */
export type InsightBar = { label: string; views: number; winner?: boolean };

const MAX_BAR_H = 98; // px высоты бара-победителя (Figma); остальные пропорциональны

export function InsightChart({ bars, trend, unitKey = 'stats.views' }: { bars: InsightBar[]; trend: string; unitKey?: string }) {
  const { t } = useTranslation();
  const max = Math.max(1, ...bars.map((b) => b.views));
  return (
    <div className="relative mt-[19px] h-[147px] overflow-hidden rounded-r15 bg-grad-soft-20">
      <span className="absolute right-[20px] top-[20px] z-20"><TrendBadge value={trend} /></span>
      {bars.map((b) => {
        // победитель — слева (фиолетовый, z-0), средний — справа с нахлёстом (белый, z-1 поверх)
        const pos = b.winner ? { left: '4%', z: 0 } : { left: '48%', z: 1 };
        const height = Math.max(24, (b.views / max) * MAX_BAR_H);
        return (
          <div key={b.label} className="absolute bottom-0 w-[48%]" style={{ left: pos.left, height, zIndex: pos.z }}>
            <span className="absolute -top-[26px] left-1/2 -translate-x-1/2 whitespace-nowrap text-center text-[16px] font-[400] leading-[19px] text-transparent" style={gradLight}>{b.label}</span>
            <div aria-hidden="true" className={cn('flex h-full w-full items-start justify-center rounded-t-[15px] pt-[12px]', b.winner ? 'bg-grad-main' : 'bg-grad-whitey backdrop-blur-[50px]')}>
              <span className="text-[16px] font-[400] leading-[19px] text-transparent" style={b.winner ? gradLight : gradMainText}>{t(unitKey, { n: b.views })}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * W60 слева: что прострелило. Ответ — ВЕДУЩИЙ ПАРАМЕТР, а не победившая связка: сравнение
 * идёт по каждому измерению отдельно (все ролики с Brat против остальных), поэтому вывод
 * можно применить — зафиксировать значение и крутить дальше остальное.
 *
 * Если ни одно измерение не дало отрыва — так и говорим. «Разницы нет» это тоже результат,
 * и он честнее выдуманного победителя.
 */
function InsightPanel({ analysis }: { analysis: IterationAnalysis }) {
  const { t } = useTranslation();
  const leading = leadingDimension(analysis);
  const bars = leading ? leaderBars(t, leading, analysis.minVideosPerValue) : null;
  const trend = leading ? `${Math.round(leading.liftPercent)}%` : '';
  return (
    <div className="h-[279px] min-w-0 flex-1 rounded-r15 bg-grad-soft-10 p-[28px]">
      <p className="flex items-center gap-[10px] text-[24px] font-[400] leading-[29px] text-transparent" style={gradLight}>
        {leading ? t('stats.leadingTitle', { dimension: t(DIMENSION_KEY[leading.dimension]) }) : t('stats.noSignalTitle')}
        <FigIcon name="icon-bolt.svg" h={18} />
      </p>
      <p className="mt-[16px] text-[16px] font-[400] leading-[19px] text-text-80">
        {leading && leading.leader
          ? t('stats.leadingHint', { value: leading.leader.value, lift: Math.round(leading.liftPercent), videos: analysis.videosAnalyzed })
          : t('stats.noSignalHint', { videos: analysis.videosAnalyzed })}
      </p>
      {bars ? (
        <InsightChart bars={bars} trend={trend} unitKey="stats.viewsPerVideo" />
      ) : (
        // Без лидера бары рисовать нечем — вместо них перечисляем, что сравнивали
        <div className="mt-[19px] flex h-[147px] flex-col justify-center gap-[10px] rounded-r15 bg-grad-soft-20 px-[20px]">
          {(analysis.dimensions.find((item) => item.verdict === 'no_difference')?.values ?? []).slice(0, 3).map((value) => (
            <div key={value.value} className="flex items-center justify-between gap-[16px] text-[16px] leading-none">
              <span className="truncate text-text-80">{value.value}</span>
              <span className="shrink-0 text-text-60">{t('stats.viewsPerVideo', { n: averageViews(value.videos, value.views) })}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Вердикты по всем измерениям одной строкой чипов. Три состояния из спеки: «есть сигнал»,
 * «данных мало», «проверить нельзя». Отдельного блока не заводим — на карточке нет места,
 * а строка чипов помещается под панелями.
 */
function VerdictChips({ analysis }: { analysis: IterationAnalysis }) {
  const { t } = useTranslation();
  return (
    // Живут в строке итераций, а не под панелями: карточка «Эволюция контента» ровно 505 по
    // макету, и отдельная строка снизу вылезала за её край на 10px. Справа в этой строке
    // свободно, и вердикты относятся именно к показанной итерации.
    <div className="ml-auto flex min-w-0 items-center gap-[10px] overflow-hidden">
      {analysis.dimensions.map((item) => (
        <span
          key={item.dimension}
          className="flex h-[36px] shrink-0 items-center gap-[10px] whitespace-nowrap rounded-r10 bg-grad-soft-20 px-[14px] text-[15px] leading-none"
          title={item.verdict === 'blocked' ? t('stats.verdictBlockedHint') : undefined}
        >
          <span className="h-[8px] w-[8px] shrink-0 rounded-full" style={{ background: verdictTone(item.verdict) }} />
          <span className="text-text-80">{t(DIMENSION_KEY[item.dimension])}</span>
          <span className="text-text-60">{verdictText(t, item)}</span>
        </span>
      ))}
    </div>
  );
}

/** Чип параметра итерации: иконбокс 25 + подпись (Figma 770:199) */
function ParamChip({ icon, label }: { icon: 'bg' | 'sub'; label: string }) {
  return (
    <span className="flex h-[25px] items-center rounded-[5px] bg-grad-soft-20 pr-[10px]">
      <span className="flex h-[25px] w-[25px] shrink-0 items-center justify-center rounded-[5px] border border-accent-light bg-grad-soft-20">
        {icon === 'sub'
          ? <span className="text-[14px] font-[800] italic leading-none text-text-80">T</span>
          : <FigIcon name="pd-chip-bg.svg" h={12} />}
      </span>
      <span className="ml-[8px] whitespace-nowrap text-[16px] font-[350] leading-none text-text-80">{label}</span>
    </span>
  );
}

/**
 * W60 справа: следующая итерация — что фиксируем, что тестируем, сколько роликов.
 *
 * Чипы больше не зашиты: фиксируем то, где есть сигнал (его уже знаем), тестируем то, что
 * «проверить нельзя» или где не хватает роликов. То есть вердикт «проверить нельзя»
 * напрямую превращается в план следующего батча — за этим он и нужен.
 */
function IterationPanel({ analysis, onCreate, creating, disabled }: {
  analysis?: IterationAnalysis | null;
  onCreate: (count: number, dimension: AnalysisDimension) => void;
  creating: boolean;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [count, setCount] = useState(5);
  const leading = leadingDimension(analysis);
  const test = nextToTest(analysis);
  const fixLabel = leading && leading.leader
    ? `${t(DIMENSION_KEY[leading.dimension])}: ${leading.leader.value}`
    : t('stats.paramSources');
  const testLabel = test ? t(DIMENSION_KEY[test.dimension]) : t('stats.paramSubtitles');
  const stepBtn = 'flex h-[25px] w-[25px] shrink-0 items-center justify-center rounded-[8px] bg-[#f6f5fd] text-[20px] leading-none transition hover:brightness-95';
  return (
    <div className="h-[279px] min-w-0 flex-1 rounded-r15 bg-grad-soft-10 p-[28px]">
      <button type="button" disabled={disabled || creating} onClick={() => onCreate(count, test?.dimension ?? 'subtitles')} className="group flex items-center gap-[12px] text-[24px] font-[400] leading-[29px] text-transparent transition disabled:cursor-not-allowed disabled:opacity-55" style={gradLight}>
        {t('stats.iterationTitle')}
        <FigIcon name="home-arrow.svg" h={16} className="transition-transform group-hover:translate-x-[3px]" />
      </button>
      <p className="mt-[16px] text-[16px] font-[400] leading-[19px] text-text-80">
        {test?.verdict === 'blocked' ? t('stats.iterationHintBlocked', { dimension: t(DIMENSION_KEY[test.dimension]) }) : t('stats.iterationHint')}
      </p>

      <div className="mt-[19px] h-[147px] rounded-r15 bg-grad-soft-20 p-[20px]">
        <div className="flex h-[25px] items-center gap-[16px]">
          <span className="shrink-0 text-[16px] font-[400] leading-none text-transparent" style={gradLight}>{t('stats.fix')}</span>
          <ParamChip icon="bg" label={fixLabel} />
        </div>
        <div className="mt-[16px] flex h-[25px] items-center gap-[16px]">
          <span className="shrink-0 text-[16px] font-[400] leading-none text-transparent" style={gradLight}>{t('stats.test')}</span>
          <ParamChip icon="sub" label={testLabel} />
        </div>
        <div className="mt-[16px] flex h-[25px] items-center gap-[12px]">
          <span className="shrink-0 text-[16px] font-[350] leading-none text-text-80">{t('stats.videoCount')}</span>
          <button type="button" aria-label={t('wizard.pool.less')} onClick={() => setCount((c) => Math.max(1, c - 1))} className={stepBtn}>
            <span className="text-transparent" style={gradMainText}>−</span>
          </button>
          <span className="w-[40px] text-center text-[16px] font-[350] leading-none text-text">{count}</span>
          <button type="button" aria-label={t('wizard.pool.more')} onClick={() => setCount((c) => c + 1)} className={stepBtn}>
            <span className="text-transparent" style={gradMainText}>+</span>
          </button>
        </div>
      </div>
    </div>
  );
}

export function StatsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { push } = useToast();
  const [params] = useSearchParams();
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.projects });
  // тянем большой период один раз, фильтруем по выбору на клиенте
  const videosQuery = useQuery({
    queryKey: ['tiktok-videos', 'all'],
    queryFn: () => api.tiktokVideos(3650),
    enabled: Boolean(meQuery.data?.tiktok),
    staleTime: 60_000
  });
  // выборка по времени (кнопка в правом верхнем углу): неделя / месяц / всё время
  const [period, setPeriod] = useState<'week' | 'month' | 'all'>('month');
  const [periodOpen, setPeriodOpen] = useState(false);
  const periodDays = period === 'week' ? 7 : period === 'month' ? 30 : Infinity;

  const tiktok = meQuery.data?.tiktok;
  const projects = projectsQuery.data?.projects ?? [];
  // Deliberate development-only visual state for the complete W60 layout.
  const previewData = import.meta.env.DEV && params.get('state') === 'data';
  const hasStats = Boolean(tiktok) || previewData;
  /* ?project=<id> — с экрана выкладки после последнего ролика: разбор считается по проекту,
     и человек должен увидеть тот батч, который только что выложил, а не текущий проект. */
  const requestedProject = params.get('project');
  const iterationProject =
    projects.find((project) => project.id === requestedProject) ?? projectsQuery.data?.activeProject ?? projects[0];
  /*
   * Разбор по измерениям считает бэк (`analyze_iterations`). Тянем его для текущего проекта:
   * анализ живёт на уровне проекта, а не аккаунта — сравнивать ролики разных треков между
   * собой бессмысленно.
   */
  const analysisQuery = useQuery({
    queryKey: ['iterations', iterationProject?.id],
    queryFn: () => api.iterations(iterationProject?.id ?? ''),
    enabled: Boolean(iterationProject?.id),
    staleTime: 30_000
  });
  const analysis = analysisQuery.data?.analysis;
  // Номер текущей итерации: базовый батч — №1, каждая созданная итерация добавляет свой
  const iterationNumber = (analysisQuery.data?.iterations?.length ?? 0) + 1;
  const createIteration = useMutation({
    mutationFn: ({ count, dimension }: { count: number; dimension: AnalysisDimension }) =>
      api.createIteration(iterationProject?.id ?? '', { videosToGenerate: count, testParameter: DIMENSION_TEST_PARAM[dimension] }),
    onSuccess: (data) => {
      push({ variant: 'success', title: t('stats.iterationStarted') });
      navigate(data.redirectTo);
    },
    onError: (error) => push({
      variant: 'error',
      title: t('stats.iterationFailed'),
      text: error instanceof Error && error.message.includes('completed batch') ? t('stats.iterationNeedsBatch') : error instanceof Error ? error.message : undefined
    })
  });
  const created = projects.reduce((sum, p) => sum + (p.generated ?? 0), 0);
  const nowSeconds = Date.now() / 1000;
  const allVideos = videosQuery.data?.videos ?? [];
  const windowSec = periodDays === Infinity ? Infinity : periodDays * 86400;
  const currentVideos = windowSec === Infinity ? allVideos : allVideos.filter((video) => video.create_time >= nowSeconds - windowSec);
  // предыдущий такой же интервал — для процента роста
  const previousVideos = windowSec === Infinity ? [] : allVideos.filter((video) => video.create_time < nowSeconds - windowSec && video.create_time >= nowSeconds - 2 * windowSec);
  const views = currentVideos.reduce((sum, video) => sum + (video.view_count ?? 0), 0);
  const previousViews = previousVideos.reduce((sum, video) => sum + (video.view_count ?? 0), 0);
  const interactions = currentVideos.reduce(
    (sum, video) => sum + (video.like_count ?? 0) + (video.comment_count ?? 0) + (video.share_count ?? 0),
    0
  );
  const engagement = views > 0 ? (interactions / views) * 100 : 0;
  const trend = (current: number, previous: number) =>
    previous > 0 ? `${Math.round(((current - previous) / previous) * 100)}%` : current > 0 ? '100%' : undefined;
  // «Достаточно данных» решает разбор, а не общий счётчик роликов: измерения читаются
  // по отдельности, и ждать, пока созреют все, незачем.
  const enough = Boolean(analysis?.enoughData) || previewData;

  const fmtViews = (n: number) => (n >= 1000 ? (n / 1000).toFixed(n >= 10_000 ? 0 : 1).replace(/\.0$/, '') : String(n));

  /* Статистика целиком строится на /api/me + /api/projects: если они не пришли,
     показывать «0 просмотров» нельзя — это враньё, а не пустое состояние. */
  if (queryDown(meQuery) || queryDown(projectsQuery)) {
    return <QueryError query={queryDown(meQuery) ? meQuery : projectsQuery} className="min-h-[560px]" />;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:min-h-[calc(100dvh_-_2*var(--space-6))] lg:flex-none lg:py-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      <div className="flex min-h-0 flex-1 flex-col gap-[20px]">
      {/* «Статистика» 1192×379 */}
      <section className="card-2 h-auto min-h-[379px] shrink-0 p-[24px] sm:p-[32px] lg:h-[379px] lg:p-[40px]">
        <div className="flex flex-col items-start justify-between gap-[20px] sm:flex-row sm:gap-space-4">
          <div>
            <h1 className="text-[32px] font-[400] leading-none text-text">{t('stats.title')}</h1>
            {/*
              Строка «@ник» — это ПОДПИСЬ ПОДКЛЮЧЁННОГО TikTok-аккаунта, поэтому без него её нет.
              Раньше здесь стоял фолбэк на artistNick/name, и на экране статистики висел «@»
              с ником из аккаунта Blast — читалось как «подключён чужой тикток».
            */}
            {tiktok?.handle && (
              <span className="mt-[16px] flex items-center gap-[10px]">
                <span className="h-[21px] w-[21px] shrink-0 overflow-hidden rounded-full bg-accent-20">
                  {meQuery.data?.user.avatarUrl && <img src={meQuery.data.user.avatarUrl} alt="" className="h-full w-full object-cover" />}
                </span>
                <span className="text-[16px] font-[400] leading-none text-text-80">@{tiktok.handle}</span>
              </span>
            )}
          </div>
          {/* без подключённого TikTok цифр нет в принципе — предлагаем подключить */}
          {hasStats ? (
            <div className="relative shrink-0">
              <button
                type="button"
                onClick={() => setPeriodOpen((v) => !v)}
                aria-haspopup="listbox"
                aria-expanded={periodOpen}
                className="flex h-[60px] w-[180px] items-center justify-center gap-[10px] rounded-r15 bg-grad-soft-20 text-[24px] font-[400] leading-[29px] text-text-80 transition hover:text-text"
              >
                {t(`stats.period.${period}`)}
                <FigIcon name="home-arrow.svg" h={9} className={cn('transition-transform', periodOpen ? '-rotate-90' : 'rotate-90')} />
              </button>
              {periodOpen && (
                <>
                  <span className="fixed inset-0 z-[9]" onClick={() => setPeriodOpen(false)} aria-hidden="true" />
                  <ul role="listbox" className="absolute right-0 top-[64px] z-[10] w-[180px] overflow-hidden rounded-r15 bg-card-2 p-[6px] shadow-[0_20px_60px_rgba(0,0,0,.5)]">
                    {(['week', 'month', 'all'] as const).map((p) => (
                      <li key={p}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={period === p}
                          onClick={() => { setPeriod(p); setPeriodOpen(false); }}
                          className={cn('flex h-[44px] w-full items-center rounded-r10 px-[14px] text-[18px] font-[400] transition', period === p ? 'bg-grad-soft-20 text-text' : 'text-text-80 hover:bg-grad-soft-10 hover:text-text')}
                        >
                          {t(`stats.period.${p}`)}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : (
            <TiktokButton connected={false} />
          )}
        </div>

        <div className="mt-[28px] flex flex-col gap-[20px] lg:flex-row">
          <StatCard icon="home-eye.svg" title={t('stats.viewsTitle')} value={previewData ? '122' : tiktok ? fmtViews(views) : '—'} unit={t('stats.thousand')} trend={previewData ? '37.8%' : tiktok ? trend(views, previousViews) : undefined} />
          <StatCard icon="icon-bolt.svg" title={t('stats.engagement')} value={previewData ? '3.6' : tiktok ? engagement.toFixed(engagement >= 10 ? 0 : 1) : '—'} unit="%" trend={previewData ? '1.8%' : undefined} />
          {/* «Опубликовано» — только реально выложенное. Без подключённого TikTok показываем
              прочерк, а не число сгенерированных: генерация ≠ публикация, и подстановка
              `created` превращала счётчик в неправду. */}
          <StatCard
            icon="tt-posted.svg"
            title={t('stats.videos')}
            value={previewData ? '123' : tiktok ? String(currentVideos.length) : '—'}
            unit={t('stats.pieces')}
            trend={previewData ? '30%' : tiktok ? trend(currentVideos.length, previousVideos.length) : undefined}
          />
        </div>
      </section>

      {/* «Эволюция контента» 1192×505 */}
      <section className="card-2 min-h-[505px] flex-none p-[24px] sm:p-[32px] lg:flex-1 lg:p-[40px]">
        <h2 className="text-[32px] font-[400] leading-none text-text">{t('stats.evolution')}</h2>

        {/* таб-бар итераций: номер берётся из реальных итераций проекта (был захардкожен «№1»);
            «+» — свой пил с обводкой ПОД основным (нахлёст 33px, тот же приём, что «+» у батчей) */}
        <div className="mt-[28px] flex h-[60px] items-center rounded-r15 bg-grad-soft-10 pr-[20px]">
          <span className="relative z-10 flex h-[60px] shrink-0 items-center whitespace-nowrap rounded-r15 border-2 border-accent-light bg-grad-soft-20 px-[21px] text-[24px] font-[400] leading-[29px] text-text [backdrop-filter:blur(40px)]">
            {t('stats.iterationN', { n: iterationNumber })}
          </span>
          {enough && (
            /* Раньше кнопка ничего не делала. Теперь она запускает следующую итерацию —
               тем же путём, что и панель справа: фиксируем сработавшее, тестируем то,
               что проверить нельзя. */
            <button
              type="button"
              aria-label={t('stats.addIteration')}
              title={t('stats.addIteration')}
              disabled={!iterationProject || createIteration.isPending}
              onClick={() => createIteration.mutate({ count: 5, dimension: nextToTest(analysis)?.dimension ?? 'subtitles' })}
              className="relative z-0 -ml-[33px] flex h-[60px] w-[78px] items-center justify-center rounded-r15 border-2 border-accent bg-grad-soft-20 pl-[33px] text-[24px] leading-none text-text-80 transition hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
            >
              +
            </button>
          )}
          {enough && analysis && <VerdictChips analysis={analysis} />}
        </div>

        <div className="mt-[20px] flex gap-[20px]">
          {enough && analysis ? (
            <>
              <InsightPanel analysis={analysis} />
              <IterationPanel
                analysis={analysis}
                onCreate={(count, dimension) => createIteration.mutate({ count, dimension })}
                creating={createIteration.isPending}
                disabled={!iterationProject}
              />
            </>
          ) : (
            <div className="min-w-0 flex-1">
              <CollectingPanel analysis={analysis} created={created} />
            </div>
          )}
        </div>
      </section>
      </div>
    </div>
  );
}
