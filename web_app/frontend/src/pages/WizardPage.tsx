import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { importWithReload } from '../lib/chunkReload';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import { ActionBar, Button, Dialog } from '../components/ui/kit';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { backgroundVariations, BackgroundWorkZone, StageBackground } from '../components/wizard/BackgroundPanel';
import { HooksWorkZone, StageHooks } from '../components/wizard/HookPanel';
import { backgroundUnits, hasTrackInput, hookComplete, hookPills, selectedEffectStyles, STAGE_ORDER, subtitleTextProblem } from '../stores/wizardStore';
import { compatibleHookTarget, SliceWorkZone, StageSlice } from '../components/wizard/SlicePanel';
import { useStoryboardBusy } from '../components/wizard/storyboardData';
import { LabWorkZone, useFxLab, useLegacyHooksToVariants } from '../components/wizard/FxLab';
import { StageSubtitles, SubtitlesWorkZone } from '../components/wizard/SubtitlesPanel';
import { TextPanel } from '../components/wizard/TextPanel';
import { dropToSeconds, timingToSeconds } from '../components/wizard/useFragmentAudio';
import { SEGMENT_SECONDS, segmentSeconds } from '../components/wizard/timing';
import { TrackStage } from '../components/wizard/TrackStage';
import { useWizardAttempt } from '../components/wizard/wizardAttempt';
import { useAsrPreview } from '../components/wizard/useAsrPreview';
import { WizardCanvas, WizardHeaderCard } from '../components/wizard/WizardFrame';
import { demoTrackUrl } from '../dev/demoTrack';
import { useToast } from '../contexts/ToastContext';
import { useFunnelUi } from '../stores/funnelUi';
import { trackTitleOf } from '../components/funnel/useFunnel';
import { useWizardStore } from '../stores/wizardStore';
import { useCombos } from '../components/wizard/montage/combos';
import { useFxTimelineOpen } from '../components/wizard/timelineGuides';

// Монтажный стол — тяжёлый полноэкранный экран «Пула»: грузится, когда его открыли
const MontageTable = lazy(() => importWithReload(() => import('../components/wizard/montage/MontageTable')).then((m) => ({ default: m.MontageTable })));

function apiErrorText(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return undefined;
  if (typeof error.detail === 'string') return error.detail;
  if (error.detail && typeof error.detail === 'object') {
    const detail = (error.detail as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
      return String((detail as { message: string }).message);
    }
  }
  return undefined;
}

/* Этап «Фон» вынесен в components/wizard/BackgroundPanel.tsx (Figma W12/3/13/14/15) */

/* Этап «Хук» вынесен в components/wizard/HookPanel.tsx (Figma W18/24–34) */

/* Этап «Текст» вынесен в components/wizard/SubtitlesPanel.tsx (Figma W16/17/23) */

/* Этап «Пул» вынесен в components/wizard/SlicePanel.tsx (Figma W19/W33) */

/** вайбы, для которых лёгкие копии клипов уже заказаны в этой вкладке */
const prewarmed = new Set<string>();

export function WizardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const { push } = useToast();
  const stage = useWizardStore((state) => state.stage);
  const setStage = useWizardStore((state) => state.setStage);
  const projectId = useWizardStore((state) => state.projectId);
  const setProjectId = useWizardStore((state) => state.setProjectId);
  const state = useWizardStore();
  const openUnlimited = useFunnelUi((ui) => ui.openUnlimited);
  // Вайб выбран на «Фоне» — сервер сразу начинает готовить лёгкие копии его клипов: к «Пулу»
  // превью кадров открываются без ожидания. Один раз на вайб и отрывок; это ускорение,
  // поэтому сбой не мешает работе — подбор на «Пуле» всё равно подготовит свои клипы сам.
  useEffect(() => {
    if (!state.timingFrom || !state.timingTo) return;
    for (const group of state.background.footage) {
      const key = `${group}|${state.timingFrom}|${state.timingTo}`;
      if (prewarmed.has(key)) continue;
      prewarmed.add(key);
      void api.prewarmMedia({ group, clipFrom: state.timingFrom, clipTo: state.timingTo })
        .catch((error: unknown) => { prewarmed.delete(key); console.warn('media prewarm failed', group, error); });
    }
  }, [state.background.footage, state.timingFrom, state.timingTo]);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.projects });
  const wizardSessionQuery = useQuery({ queryKey: ['wizard-session'], queryFn: api.wizardSession });
  const restoredServerDraft = useRef(false);
  const qaStage = import.meta.env.DEV ? Number(params.get('qaStage') || 0) : 0;
  const qaGuide = import.meta.env.DEV ? params.get('qaGuide') : null;
  useEffect(() => {
    if (qaStage < 1 || qaStage > 5) return;
    // Explicit development-only visual fixture: every Figma stage is directly auditable
    // without faking browser storage or calling an LLM. It is excluded from production use.
    state.setTrack({
      id: 'qa-track', userId: 'user_1', s3Key: 'qa/track.mp3', filename: 'Название трека.mp3',
      durationS: 204, createdAt: '2026-07-15T00:00:00Z', expiresAt: '2026-07-22T00:00:00Z',
      // синтезированный демо-трек: у волны отрывка настоящая форма, отрывок слышно
      localUrl: import.meta.env.DEV ? demoTrackUrl(204) : undefined
    });
    state.setField('lyrics', qaGuide === 'text' ? '' : 'Я знаю — этот город не уснёт\nПока музыка ведёт нас вперёд');
    state.setField('timingFrom', qaGuide === 'timing' ? '' : '00:10:00');
    state.setField('timingTo', qaGuide === 'timing' ? '' : '00:22:00');
    state.setBackground((qaStage <= 2 && Boolean(qaGuide)) || qaGuide?.startsWith('background-')
      ? { mode: 'footage', footage: [], photo: [], color: undefined, sourceVideos: [], strobe: false, glue: 'Щелчок' }
      : { mode: 'footage', footage: ['Ночной город', 'Неон'], photo: ['Крупный план'], color: '#8b6fe6', strobe: false, glue: 'Щелчок' });
    if (qaGuide === 'hooks-drop') {
      state.setHooks({ dropTime: '' });
    } else if (qaGuide === 'hooks-type') {
      state.setHooks({ dropTime: '00:15:00' });
    } else {
      state.setHooks({ dropTime: '00:15:00', kind: 'warmup', config: { sound: 'Звук' } });
      state.setHooks({ kind: 'object', config: { object: 'Квадрат' } });
      state.setHooks({ kind: 'effects', config: { effectHook: 'Молния', effectGlue: 'Щелчок', effectStyle: 'Глитч' } });
      state.setHooks({ kind: 'motion', config: { motion: 'Зум' } });
      state.setHooks({ kind: 'thought', config: { thought: 'Мысль' } });
      state.setHooks({ kind: 'effects' });
    }
    state.setSubtitles({ color: '#f6f5fd', pool: qaGuide === 'subs-style' ? [] : ['Brat', 'Jakson', 'Impulse'] });
    state.setAllocation(qaGuide === 'pool-total'
      ? { total: 1, background: { 'footage:Ночной город': 1 }, subtitles: { Brat: 1 }, hooks: {}, seeded: false }
      : qaGuide === 'pool-distribute'
        ? {
          total: 5,
          background: { 'footage:Ночной город': 1, 'footage:Неон': 1, 'photo:Крупный план': 1 },
          subtitles: { Brat: 1, Jakson: 1, Impulse: 1 },
          hooks: { sound: 1, object: 1, effects: 1, motion: 1, thought: 1 },
          seeded: true
        }
        : {
          total: 5,
          background: { 'footage:Ночной город': 2, 'footage:Неон': 1, 'photo:Крупный план': 1 },
          subtitles: { Brat: 2, Jakson: 1, Impulse: 1 },
          hooks: { sound: 1, object: 1, effects: 1, motion: 1, thought: 1 },
          seeded: true
        });
    setStage(qaStage);
    // qaStage is the only trigger: store changes above must not re-run this fixture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qaGuide, qaStage]);

  useEffect(() => {
    const session = wizardSessionQuery.data?.session;
    if (!session || restoredServerDraft.current || qaStage) return;
    restoredServerDraft.current = true;
    state.restoreSession(session.projectId, session.stage, session.data);
  }, [qaStage, state, wizardSessionQuery.data?.session]);

  // Без ?project генерируем в ТЕКУЩИЙ проект. Раньше брали projects[0] — самый новый
  // по startedAt, который не обязан быть текущим: визард и «Проекты» расходились в том,
  // над каким проектом идёт работа.
  useEffect(() => {
    const known = projectsQuery.data?.projects;
    if (!known) return;
    const queryProject = params.get('project');
    const fallback = projectsQuery.data?.activeProject?.id ?? known[0]?.id;
    if (queryProject) {
      setProjectId(queryProject);
      return;
    }
    /*
     * Черновик визарда живёт в localStorage вместе с projectId. Проект могли удалить —
     * тогда сохранённый id указывает в пустоту, и батч уходил в несуществующий проект:
     * готовые ролики становились недостижимы. Проверяем id по списку и подменяем текущим.
     */
    const stale = Boolean(projectId) && !known.some((project) => project.id === projectId);
    if (!projectId || stale) {
      if (fallback) setProjectId(fallback);
      else if (stale) setProjectId(null);
    }
  }, [params, projectId, projectsQuery.data?.activeProject?.id, projectsQuery.data?.projects, setProjectId]);

  /*
   * Запись черновика на сервер. Пишут и «Продолжить», и табы этапов — записи идут строго
   * по очереди, иначе более ранняя могла бы доехать последней и откатить черновик. Удачная
   * запись кладётся в кэш ['wizard-session']: при следующем входе на страницу восстановление
   * должно видеть её, а не копию, загруженную до правок.
   */
  const saveChain = useRef<Promise<unknown>>(Promise.resolve());
  const persistDraft = (target: number) => {
    const run = saveChain.current.catch(() => undefined).then(() => {
      const current = useWizardStore.getState();
      return api.saveWizardSession({ projectId: current.projectId, stage: target, data: current.stageData() });
    });
    saveChain.current = run;
    return run.then((data) => {
      queryClient.setQueryData(['wizard-session'], (old: object | undefined) => ({ ...(old ?? {}), session: data.session }));
      return data;
    });
  };
  const saveSessionMutation = useMutation({
    mutationFn: (target: number) => persistDraft(target),
    onError: () => {
      // A failed persistence write must stop the stage transition.  Moving on
      // would leave the next panel rendered from a draft the server never saw.
      push({ variant: 'error', title: t('wizard.page.saveFail'), text: t('wizard.page.saveFailText') });
    }
  });
  const renameProjectMutation = useMutation({
    mutationFn: (name: string) => {
      if (!projectId) throw new Error('Project is not selected');
      return api.updateProject(projectId, { name });
    },
    onSuccess: async (data) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
        queryClient.invalidateQueries({ queryKey: ['project', data.project.id] })
      ]);
    },
    onError: (error) => push({
      variant: 'error',
      title: t('wizard.page.renameFail'),
      text: error instanceof Error ? error.message : undefined
    })
  });
  const submitMutation = useMutation({
    mutationFn: () => api.submitWizard({ projectId, stageData: state.stageData(), videosToGenerate: safeVideosToGenerate, idempotencyKey: state.final.idempotencyKey }),
    // newBatch, а НЕ reset: трек, текст и тайминги — вводные проекта, а не батча.
    // reset стирал их вместе с настройками батча, и «+» на втором батче уводил
    // человека обратно на загрузку файла — хотя ProjectDetailPage.addBatch
    // рассчитывает найти их в сторе и открыть сразу этап «Фон».
    // шапка опрашивает активную генерацию редко, пока её нет, — сообщаем о новой сразу
    onSuccess: (data) => { push({ variant: 'success', title: t('wizard.page.genStarted') }); void queryClient.invalidateQueries({ queryKey: ['active-job'] }); state.newBatch(projectId); state.ackCarriedOver(); navigate(data.redirectTo); },
    // 402 — упёрлись в лимит роликов: причина + путь к решению, а не общий «не удалось»
    onError: (error) => {
      const limitReached = error instanceof ApiError && error.status === 402;
      // 409 asr_preview_pending — субтитры ещё раскладываются: это не сбой, просто рано
      const asrPending = error instanceof ApiError && error.status === 409
        && (error.detail as { detail?: { code?: string } })?.detail?.code === 'asr_preview_pending';
      if (asrPending) {
        push({ variant: 'info', title: t('wizard.page.asrPendingTitle'), text: t('wizard.page.asrPendingText') });
        return;
      }
      // Воронка: бесплатные ролики кончились — открываем безлимит на этот трек; квота трека
      // кончилась — обновляем лимиты, окно перезарядки всплывёт у кружка лимитов.
      const limit = limitReached ? (error.detail as { detail?: { code?: string; unlimitedOffer?: boolean } })?.detail : undefined;
      if (limit?.code === 'credits_exhausted' && limit.unlimitedOffer) {
        openUnlimited({
          source: 'gate',
          projectId: projectId ?? undefined,
          trackId: state.track?.id,
          audioHash: state.track?.audioHash,
          trackTitle: trackTitleOf(state.track?.filename)
        });
        return;
      }
      if (limit?.code && ['cooldown', 'daily_limit', 'track_batch_cap'].includes(limit.code)) {
        void queryClient.invalidateQueries({ queryKey: ['funnel-state'] });
        void queryClient.invalidateQueries({ queryKey: ['me'] });
      }
      push({
        variant: 'error',
        title: limitReached ? t('wizard.page.limitReached') : t('wizard.page.genFail'),
        text: apiErrorText(error) ?? t('wizard.page.genFailText'),
        action: limitReached ? { label: t('wizard.track.limitCta'), href: '/app/pricing' } : undefined
      });
    }
  });

  const creditsLeft = meQuery.data ? meQuery.data.creditsLeft : 1;
  // Этап «Пул»: суммы распределения должны сходиться с общим числом видео
  const fixedColorCount = state.background.color ? 1 : 0;
  // Суммы — только по фонам и стилям, которые сейчас выбраны: доля, оставшаяся у снятого
  // фона или стиля, рендер развернул бы в лишнее видео.
  const unitKeys = backgroundUnits(state.background).map((unit) => unit.key);
  const allocBgSum = unitKeys.reduce((sum, key) => sum + (state.allocation.background[key] ?? 0), 0);
  const allocSubsSum = state.subtitles.pool.reduce((sum, style) => sum + (state.allocation.subtitles[style] ?? 0), 0);
  const allocNoStale = Object.entries(state.allocation.background).every(([key, count]) => !count || unitKeys.includes(key))
    && Object.entries(state.allocation.subtitles).every(([style, count]) => !count || state.subtitles.pool.includes(style));
  const colorFont = state.background.strobe ? state.allocation.strobeFont : state.allocation.colorFont;
  const colorFontOk = !state.background.color || !colorFont || state.subtitles.pool.includes(colorFont);
  const allocHooksSum = Object.values(state.allocation.hooks).reduce((a, b) => a + b, 0);
  const allocStylesSum = Object.values(state.allocation.styles ?? {}).reduce((a, b) => a + b, 0);
  const selectedHooks = hookPills(state.hooks);
  const selectedStyles = selectedEffectStyles(state.hooks);
  const hookTarget = compatibleHookTarget(state.background, state.allocation.background);
  // Режим вариантов FX (по умолчанию; ?fxLab=0 — классический шаг): хуки — это варианты (fxVariants), доли — allocation.variants.
  // Классические hooks.configs/allocation.hooks/styles в этом режиме не участвуют.
  const fxLab = useFxLab();
  useLegacyHooksToVariants(fxLab);
  const labVariants = state.fxVariants.filter((v) => !v.draft);
  const labVariantsComplete = labVariants.length > 0 && labVariants.every((v) => hookComplete(v.kind, v.config));
  const labAllocSum = labVariants.reduce((sum, v) => sum + (state.allocation.variants?.[v.id] ?? 0), 0);
  const fxAllocBalanced = fxLab
    ? (labVariants.length === 0 ? labAllocSum === 0 : labAllocSum === hookTarget)
    : (selectedHooks.length === 0 ? allocHooksSum === 0 : allocHooksSum === hookTarget)
      && (selectedStyles.length === 0 ? allocStylesSum === 0 : allocStylesSum === hookTarget);
  // Пустой пул субтитров — не «сошлось»: роликам нечем подписываться
  const allocBalanced =
    state.allocation.total > 0 &&
    allocBgSum === state.allocation.total - fixedColorCount &&
    state.subtitles.pool.length > 0 &&
    allocSubsSum === state.allocation.total - fixedColorCount &&
    allocNoStale && colorFontOk &&
    fxAllocBalanced;
  const safeVideosToGenerate = Math.max(1, state.allocation.total);

  // Трек и текст — обязательные вводные: без них рендерить lyric-video нечего.
  const trackReady = hasTrackInput(state);
  // Тайминг отрывка задан полностью — до этого текст вписывать не к чему
  const paidPlan = Boolean(meQuery.data?.subscription.isActive && meQuery.data.subscription.tier !== 'TRIAL');
  const maxSegmentSeconds = paidPlan ? SEGMENT_SECONDS.paid : SEGMENT_SECONDS.trial;
  const segment = segmentSeconds(state.timingFrom, state.timingTo);
  // отрывок вне лимита (или «до» раньше «от») — дальше не пускаем, но введённое сохраняем
  const segmentInvalid = segment !== null && (segment <= 0 || segment > maxSegmentSeconds);
  const timingReady = Boolean(state.track)
    && timingToSeconds(state.timingFrom) !== null
    && timingToSeconds(state.timingTo) !== null
    && !segmentInvalid;
  const timingToComplete = timingToSeconds(state.timingTo) !== null;
  const dropSeconds = dropToSeconds(state.hooks.dropTime);
  const clipFromSeconds = timingToSeconds(state.timingFrom);
  const clipToSeconds = timingToSeconds(state.timingTo);
  const dropReady = dropSeconds !== null
    && clipFromSeconds !== null
    && clipToSeconds !== null
    && dropSeconds >= clipFromSeconds
    && dropSeconds <= clipToSeconds;
  const configuredHooks = hookPills(state.hooks);
  const configuredHookCount = configuredHooks.length;
  const configuredHooksNeedDrop = configuredHooks.some((pill) => pill.kind !== 'none');

  // Примерка субтитров: ASR стартует, как только человек ушёл с «Трека», и успевает к «Тексту»
  useAsrPreview(stage !== 1 && trackReady && timingReady);

  // Телефон: шаги идут одной колонкой, и после «Продолжить» страница оставалась внизу —
  // человек видел футер следующего шага, а не его начало. Скроллим контент к началу.
  useEffect(() => {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    document.querySelector('.app-content')?.scrollTo({ top: 0 });
  }, [stage]);

  // Этап «Трек» можно проскочить только мимо UI (персист стора, прямой ?qaStage, старый батч) —
  // возвращаем на него, иначе визард дойдёт до «Сгенерировать» с пустым треком.
  useEffect(() => {
    if (stage !== 1 && !trackReady) setStage(1);
  }, [setStage, stage, trackReady]);

  // «Пул»: генерация ждёт раскадровку и склейки выбранного темпа (см. PoolStoryboard).
  const storyboardBusy = useStoryboardBusy((s) => s.busy);
  const subtitleFontsQuery = useQuery({ queryKey: ['subtitle-fonts'], queryFn: api.subtitleFonts, staleTime: Infinity });
  const textProblem = subtitleTextProblem(state.subtitles, state.background, subtitleFontsQuery.data);
  // Ролики батча: по ним сверяются правки стола (хук со стола тоже требует дроп)
  const combos = useCombos();
  const montageHooks = Object.entries(state.montage.videos)
    .some(([index, video]) => video.edited && video.kind !== 'none' && combos[Number(index)]?.sig === video.sig);
  const bg = state.background;

  /*
   * Чего не хватает каждому шагу (null — шаг готов). Одна таблица и для «Продолжить», и для
   * финальной отправки: табы пускают на любой пройденный шаг, поэтому перед генерацией
   * перепроверяются ВСЕ шаги — снятый стиль, сдвинутый отрывок или пропавший дроп иначе
   * доехали бы до бэка (422) или молча выпали бы из рендера.
   */
  const needDrop = (fxLab ? labVariants.some((v) => v.kind !== 'none') : configuredHooksNeedDrop) || montageHooks;
  const fxConfigured = fxLab ? labVariantsComplete : configuredHookCount > 0;
  const stageProblems: Record<number, string | null> = {
    1: !state.track ? t('wizard.missing.track')
      : !timingReady ? t('wizard.missing.cut')
        : !state.lyrics.trim() ? t('wizard.missing.lyrics') : null,
    // Строб и эффекты на фото без выбора рендер молча выбрасывает — не пускаем дальше,
    // выбирать за человека тоже не стоит
    2: backgroundVariations(bg) === 0 ? t('wizard.missing.background')
      : bg.color && bg.strobe && !bg.glue ? t('wizard.missing.strobeGlue')
        : bg.photo.length > 0 && bg.photoEffects && !bg.photoStyle ? t('wizard.missing.photoStyle') : null,
    // Варианты: все должны быть настроены (у недонастроенного на шаге FX метка «настроить»),
    // дроп внутри отрывка нужен, если хоть один вариант — не «Без хука».
    3: !fxConfigured ? t('wizard.missing.fx')
      : needDrop && !dropReady ? t('wizard.missing.drop') : null,
    // Те же невозможные настройки текста, что горят красным на шаге, — бэк отклонил бы их 422
    4: state.subtitles.pool.length === 0 ? t('wizard.missing.subtitles')
      : textProblem?.kind === 'font' ? t('wizard.missing.textFont', { style: textProblem.style })
        : textProblem?.kind === 'fontUnknown' ? t('wizard.missing.textFontUnknown', { style: textProblem.style })
          : textProblem?.kind === 'down' ? t('wizard.missing.textDown', { style: textProblem.style }) : null,
    // Недонастроенный вариант мог появиться после «Пула» (вернулись на FX и добавили копию) —
    // бэк его не примет, поэтому генерация ждёт, пока его настроят или удалят.
    5: storyboardBusy ? t('wizard.missing.storyboard')
      : fxLab && !labVariantsComplete ? t('wizard.missing.fx')
        : !allocBalanced ? t('wizard.missing.pool') : null
  };
  // «Пул» готов, только когда готовы и все шаги до него
  const ready = stage === 5 ? STAGE_ORDER.every((s) => !stageProblems[s]) : !stageProblems[stage];
  const canContinue = ready;

  /*
   * Метрики прохождения визарда (из ревью): сколько времени человек проводит на этапе —
   * прежде всего на «Пуле», где раскладка самая тяжёлая, — и как часто возвращается назад.
   * Считаем по смене этапа: событие уходит с длительностью ПРЕДЫДУЩЕГО этапа, поэтому
   * отдельного «ушёл со страницы» не нужно.
   */
  const stageEnteredRef = useRef<{ stage: number; at: number }>({ stage, at: Date.now() });
  const trackedStageRef = useRef<number | null>(null);
  useEffect(() => {
    if (trackedStageRef.current === stage) return;
    trackedStageRef.current = stage;
    void api.trackEvent('wizard_stage_view', { stage }).catch(() => {});
  }, [stage]);

  useEffect(() => {
    const previous = stageEnteredRef.current;
    if (previous.stage === stage) return;
    void api.trackEvent('wizard_stage_time', {
      stage: previous.stage,
      seconds: Math.round((Date.now() - previous.at) / 1000),
      // назад или вперёд — по позиции в порядке прохождения, а не по номеру этапа
      back: STAGE_ORDER.indexOf(stage) < STAGE_ORDER.indexOf(previous.stage)
    });
    stageEnteredRef.current = { stage, at: Date.now() };
  }, [stage]);

  // Шаг сменился — прошлая попытка «Продолжить» больше не подсвечивает пропуски. Пометку,
  // поставленную ради перехода на этот шаг (генерацию вернули сюда), оставляем.
  useEffect(() => {
    const attempt = useWizardAttempt.getState();
    if (attempt.stage !== stage) attempt.clear();
  }, [stage]);

  // Причина под кнопкой живёт вместе с шагом: человек исправил одно — показываем следующую
  // проблему, исправил всё — убираем. Иначе «Строб включён — выбери склейку» висела бы и после
  // выбора склейки, до следующего нажатия «Продолжить».
  const markedStage = useWizardAttempt((a) => a.stage);
  const markedMessage = useWizardAttempt((a) => a.message);
  const markedProblem = markedStage === null ? null : stageProblems[markedStage] ?? null;
  useEffect(() => {
    if (markedStage === null || markedMessage === t('wizard.missing.submitting')) return;
    if (!markedProblem) useWizardAttempt.getState().clear();
    else if (markedProblem !== markedMessage) useWizardAttempt.getState().mark(markedStage, markedProblem);
  }, [markedStage, markedMessage, markedProblem, t]);

  /** Пометить шаг пропусками; не текущий — открыть его, чтобы человек увидел, что чинить. */
  const block = (target: number, reason: string) => {
    if (target !== stage) setStage(target);
    // проблема в стиле, чья вкладка не открыта, — открываем её: красная строка видна там
    if (target === 4 && textProblem) state.setSubtitles({ textTab: textProblem.style });
    useWizardAttempt.getState().mark(target, reason);
  };

  // Повторный вызов, пока идёт сохранение или отправка, игнорируется: двойной клик или
  // «Сгенерировать» на столе поверх уже отправленной формы иначе запускали бы второй батч.
  const submittingRef = useRef(false);
  /** Почему сейчас нельзя дальше (и пометить это); null — можно. */
  const blocker = (): string | null => {
    if (submittingRef.current) return t('wizard.missing.submitting');
    if (STAGE_ORDER.indexOf(stage) === STAGE_ORDER.length - 1) {
      for (const s of STAGE_ORDER) {
        const problem = stageProblems[s];
        if (problem) { block(s, problem); return problem; }
      }
      return null;
    }
    const problem = stageProblems[stage];
    if (problem) { block(stage, problem); return problem; }
    return null;
  };

  const next = async (): Promise<string | null> => {
    const reason = blocker();
    if (reason) return reason;
    submittingRef.current = true;
    try {
      useWizardAttempt.getState().clear();
      const idx = STAGE_ORDER.indexOf(stage);
      const last = idx === STAGE_ORDER.length - 1;
      try {
        await saveSessionMutation.mutateAsync(last ? stage : STAGE_ORDER[idx + 1]);
      } catch {
        return t('wizard.page.saveFail');
      }
      if (!last) {
        setStage(STAGE_ORDER[idx + 1]);
        return null;
      }
      try {
        await submitMutation.mutateAsync();
      } catch {
        // причину уже показал onError мутации
        return t('wizard.page.genFail');
      }
      return null;
    } finally {
      submittingRef.current = false;
    }
  };

  // Табы этапов сохраняют черновик, как «Продолжить», — иначе серверная копия отставала бы.
  // Переход не ждёт записи: сбой записи — тост, как у «Продолжить».
  const goToStage = (target: number) => {
    if (target === stage) return;
    setStage(target);
    persistDraft(target).catch(() => push({ variant: 'error', title: t('wizard.page.saveFail'), text: t('wizard.page.saveFailText') }));
  };

  // «Пул» и монтажный стол смотрят на одно видео батча: листалка «Комбинаций» и
  // переключатель стола двигают один номер.
  const [poolIndex, setPoolIndex] = useState(0);
  const [tableOpen, setTableOpen] = useState(false);
  const montageVideos = useWizardStore((s) => s.montage.videos);
  const storyboardVideos = useWizardStore((s) => s.storyboard.videos);
  const setTimelineFlag = useFxTimelineOpen((s) => s.setOpen);
  useEffect(() => { setTimelineFlag(tableOpen && stage === 5); }, [tableOpen, stage, setTimelineFlag]);
  // ушли с «Пула» — стол закрыт: возврат на «Пул» не должен сам открывать его поверх
  useEffect(() => { if (stage !== 5) setTableOpen(false); }, [stage]);
  // «Докрутить на сайте»: ролик из бота открывается сразу на монтажном столе (флаг разовый)
  const openTableOnLoad = useWizardStore((s) => s.openTableOnLoad);
  const consumeOpenTable = useWizardStore((s) => s.consumeOpenTable);
  useEffect(() => {
    if (!openTableOnLoad || stage !== 5) return;
    consumeOpenTable();
    setPoolIndex(0);
    setTableOpen(true);
  }, [openTableOnLoad, stage, consumeOpenTable]);
  useEffect(() => () => setTimelineFlag(false), [setTimelineFlag]);
  const safePoolIndex = Math.min(poolIndex, Math.max(0, combos.length - 1));
  const poolCombo = combos[safePoolIndex];
  const poolEdited = Boolean(poolCombo && ((montageVideos[safePoolIndex]?.edited && montageVideos[safePoolIndex]?.sig === poolCombo.sig)
    || Object.keys(storyboardVideos[poolCombo.slotIndex]?.pins ?? {}).length));

  /*
   * Генерировать некуда — сразу открываем создание проекта. Раньше здесь был экран
   * «Какой проект?» с кнопкой на список проектов: лишний шаг, который вёл на такой же
   * пустой экран, да ещё и со своим фоном мимо каркаса.
   */
  if (!projectId && projectsQuery.data?.projects.length === 0) {
    return <Navigate to="/app/projects?new=1" replace />;
  }

  const track = state.track;
  const currentProject = projectsQuery.data?.projects.find((project) => project.id === projectId);
  const headerTitle = currentProject?.name
    ?? (track ? track.filename.replace(/\.[^.]+$/, '') : t('wizard.track.nameFallback'));
  const artist = meQuery.data?.user.artistNick || meQuery.data?.user.name || undefined;
  const back = () => {
    const idx = STAGE_ORDER.indexOf(stage);
    // Возврат назад — отдельное событие: по нему видно, какой этап заставляет переделывать
    void api.trackEvent('wizard_back', { stage });
    if (idx > 0) setStage(STAGE_ORDER[idx - 1]);
    else navigate(-1);
  };
  const busy = submitMutation.isPending || saveSessionMutation.isPending;


  // Тот же fill-height, что у Dashboard/Projects/ProjectDetail: верх контента = лого сайдбара,
  // низ = аватар. Внутри — холст по модели макета (WizardCanvas): ширина 1240, высота по месту.
  return (
    <div className="w12-zone">
    <WizardCanvas>
      {/*
        Новый батч наследует трек, текст и тайминги прошлого — но молча подменять
        вводные нельзя: человек либо не заметит, что генерит по старому отрывку,
        либо решит, что визард потерял шаг. Спрашиваем один раз, при входе.
      */}
      <Dialog
        open={state.carriedOverInputs}
        title={t('wizard.page.carriedTitle')}
        onClose={() => state.ackCarriedOver()}
        footer={(
          <ActionBar>
            <Button variant="ghost" onClick={() => { state.ackCarriedOver(); setStage(1); }}>{t('wizard.page.carriedEdit')}</Button>
            <Button variant="primary" onClick={() => state.ackCarriedOver()}>{t('wizard.page.carriedKeep')}</Button>
          </ActionBar>
        )}
      >
        <p className="text-ui-16 text-text-80">{t('wizard.page.carriedText')}</p>
      </Dialog>
      <div className="w12-col-main">
        <WizardHeaderCard
          title={headerTitle}
          artist={artist}
          onStageSelect={goToStage}
          onRename={projectId ? (value) => {
            const name = value.trim();
            if (name && name !== currentProject?.name) renameProjectMutation.mutate(name);
          } : undefined}
        />
        {/* data-limits-dim: хост затемнения для LimitsIndicator (Figma W46 — на всю карточку) */}
        <section data-limits-dim className="w12-card w12-stage" style={stage === 5 ? { overflow: 'hidden' } : undefined}>
          {queryDown(projectsQuery) ? (
            /* без списка проектов визарду некуда сабмитить — честно говорим и даём повтор */
            <QueryError query={projectsQuery} className="!bg-transparent min-h-[420px]" />
          ) : projectsQuery.isLoading ? (
            <Skeleton className="h-[420px]" />
          ) : (
            <>
              {stage === 1 && <TrackStage creditsLeft={creditsLeft} maxSegmentSeconds={maxSegmentSeconds} paidPlan={paidPlan} />}
              {stage === 2 && <StageBackground qaGuide={qaGuide} />}
              {stage === 3 && <StageHooks />}
              {stage === 4 && <StageSubtitles />}
              {stage === 5 && <StageSlice />}
            </>
          )}
        </section>
      </div>
      {stage === 1 ? (
        <TextPanel
          ready={ready}
          // поле текста открывается только после отрывка: текст относится к нему
          timingReady={timingReady}
          timingToComplete={timingToComplete}
          loading={busy}
          onNext={next}
        />
      ) : stage === 2 ? (
        <BackgroundWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : stage === 3 ? (
        fxLab ? <LabWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} /> : <HooksWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : stage === 4 ? (
        <SubtitlesWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : (
        // Пока открыт стол, раскадровка «Пула» под ним не живёт: у неё свой звук и свои
        // кнопки кадра. Сама раскадровка лежит в сторе — стол работает с ней.
        tableOpen ? <aside className="w12-col-aside" aria-hidden="true" /> : <SliceWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next}
          index={safePoolIndex} onIndex={setPoolIndex} edited={poolEdited}
          onOpenTimeline={(index) => { setPoolIndex(index); setTableOpen(true); }} />
      )}
    </WizardCanvas>
    {stage === 5 && tableOpen && (
      <Suspense fallback={null}>
        <MontageTable index={safePoolIndex} onIndex={setPoolIndex} onClose={() => setTableOpen(false)} busy={busy} onGenerate={() => { const reason = blocker(); if (reason) return reason; void next(); return null; }} />
      </Suspense>
    )}
    </div>
  );
}
