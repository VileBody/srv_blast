import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, ApiError } from '../lib/api';
import type { Vibe } from '../lib/types';
import { Button } from '../components/ui/Button';
import { Card, FlatCard } from '../components/ui/Card';
import { FieldError, Input, Textarea } from '../components/ui/Input';
import { Modal } from '../components/ui/Modal';
import { Skeleton } from '../components/ui/Skeleton';
import { QueryError, queryDown } from '../components/ui/ErrorState';
import { StatusBadge } from '../components/ui/StatusBadge';
import { backgroundVariations, BackgroundWorkZone, StageBackground } from '../components/wizard/BackgroundPanel';
import { HooksWorkZone, StageHooks } from '../components/wizard/HookPanel';
import { hasTrackInput, hookPills } from '../stores/wizardStore';
import { SliceWorkZone, StageSlice } from '../components/wizard/SlicePanel';
import { StageSubtitles, SubtitlesWorkZone } from '../components/wizard/SubtitlesPanel';
import { TextPanel } from '../components/wizard/TextPanel';
import { timingToSeconds } from '../components/wizard/useFragmentAudio';
import { BackSquareButton, WizardHeaderCard } from '../components/wizard/WizardFrame';
import { useToast } from '../contexts/ToastContext';
import { cn } from '../lib/cn';
import { useWizardStore } from '../stores/wizardStore';
import { FigIcon } from '../components/ui/FigIcon';

/* Строгий формат тайминга мм:сс:мс — двоеточие ставится само после каждых двух цифр */
function maskTiming(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 6);
  return digits.replace(/(\d{2})(?=\d)/g, '$1:');
}

/* timingToSeconds переехал в useFragmentAudio: разбор тайминга нужен и превью с треком */

function secondsToTiming(seconds: number): string {
  const total = Math.max(0, seconds);
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(Math.floor(total % 60)).padStart(2, '0');
  const cc = String(Math.round((total % 1) * 100)).padStart(2, '0');
  return `${mm}:${ss}:${cc}`;
}

/** Тайминг не может выходить за реальную длительность трека (правка ревью) */
export function clampTiming(value: string, durationS?: number): string {
  const masked = maskTiming(value);
  if (!durationS) return masked;
  const sec = timingToSeconds(masked);
  if (sec !== null && sec > durationS) return secondsToTiming(durationS);
  return masked;
}

function parseTime(value: string): number | null {
  const match = /^(\d+):([0-5]\d)$/.exec(value.trim());
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

/** Максимальная длина отрывка: 15 с на триале, 30 с на платном тарифе. */
export const SEGMENT_SECONDS = { trial: 15, paid: 30 } as const;

export function segmentSeconds(from: string, to: string): number | null {
  const a = timingToSeconds(from);
  const b = timingToSeconds(to);
  return a === null || b === null ? null : b - a;
}

/* Этап «Трек» по Figma Wireframe 7–8: вводные трека + тайминг отрывка */
function StageOne({ creditsLeft, maxSegmentSeconds, paidPlan }: { creditsLeft: number | null; maxSegmentSeconds: number; paidPlan: boolean }) {
  const { t } = useTranslation();
  const { push } = useToast();
  const track = useWizardStore((state) => state.track);
  const setTrack = useWizardStore((state) => state.setTrack);
  const lyrics = useWizardStore((state) => state.lyrics);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const setField = useWizardStore((state) => state.setField);
  const projectId = useWizardStore((state) => state.projectId);
  const reset = useWizardStore((state) => state.reset);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const timingToInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const previousQuery = useQuery({
    queryKey: ['wizard-previous-track'],
    queryFn: api.previousTrack,
    enabled: !track,
    staleTime: 30_000
  });

  const uploadMutation = useMutation({
    mutationFn: api.uploadTrack,
    onSuccess: (data, file) => {
      setTrack(data.track);
      audioRef.current?.pause();
      audioRef.current = null;
      setPlaying(false);
      const url = URL.createObjectURL(file);
      setAudioUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
      // Реальная длительность трека из метаданных файла (mock возвращает заглушку)
      const probe = new Audio(url);
      probe.onloadedmetadata = () => {
        if (Number.isFinite(probe.duration)) setTrack({ ...data.track, durationS: probe.duration });
      };
      push({ variant: 'success', title: t('wizard.track.loaded'), text: data.track.filename });
    },
    // 402 — исчерпан лимит треков: показываем причину и куда идти, а не общий «не загрузилось»
    onError: (error) => {
      const limitReached = error instanceof ApiError && error.status === 402;
      push({
        variant: 'error',
        title: limitReached ? t('wizard.track.limitReached') : t('wizard.track.loadFail'),
        text: limitReached ? String((error.detail as { detail?: string })?.detail ?? '') : undefined,
        action: limitReached ? { label: t('wizard.track.limitCta'), href: '/app/pricing' } : undefined
      });
    }
  });

  const handleFile = (file?: File | null) => {
    if (file) uploadMutation.mutate(file);
  };
  const onUpload = (event: ChangeEvent<HTMLInputElement>) => handleFile(event.target.files?.[0]);
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    handleFile(event.dataTransfer.files?.[0]);
  };

  const togglePlay = () => {
    if (!audioUrl) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(audioUrl);
      audioRef.current.onended = () => setPlaying(false);
    }
    const audio = audioRef.current;
    if (playing) {
      audio.pause();
      setPlaying(false);
      return;
    }
    // Проигрываем именно выбранный отрезок — для верификации тайминга
    const from = timingToSeconds(timingFrom);
    const to = timingToSeconds(timingTo);
    audio.ontimeupdate = to !== null && (from === null || to > from)
      ? () => { if (audio.currentTime >= to) { audio.pause(); setPlaying(false); audio.ontimeupdate = null; } }
      : null;
    audio.currentTime = from ?? 0;
    void audio.play();
    setPlaying(true);
  };

  // Только пауза при размонтировании. Blob-URL не отзываем здесь: в dev StrictMode
  // cleanup эффекта срабатывает сразу после mount и убивает ссылку до воспроизведения.
  // Старая ссылка отзывается в setAudioUrl при замене файла.
  useEffect(() => () => {
    audioRef.current?.pause();
  }, []);

  /*
   * Раньше превышение лимита ловилось ПОСЛЕ ввода: тайминг обнулялся, и человек начинал
   * заново, часто не поняв почему. Теперь лимит написан над полями, длина отрывка видна
   * вживую, а перебор просто подсвечивается — введённое не стирается.
   */
  const commitTiming = (field: 'timingFrom' | 'timingTo', next: string) => {
    /*
     * Текст привязан к конкретному отрывку: если границы поехали, старые строки уже не
     * совпадут со звуком. Для lyric-video рассинхрон недопустим, поэтому текст очищаем
     * и прямо просим вписать новый — это ~30 секунд, зато результат всегда синхронный.
     */
    const changed = (field === 'timingFrom' ? timingFrom : timingTo) !== next;
    if (changed && lyrics.trim()) {
      setField('lyrics', '');
      setField('fragmentLyrics', '');
      setField('fragmentEnabled', false);
      push({ variant: 'warning', title: t('wizard.text.resetTitle'), text: t('wizard.text.resetText') });
    }
    setField(field, next);
    return true;
  };

  const ext = track ? (track.filename.split('.').pop() ?? 'mp3').toLowerCase() : null;
  const baseName = track ? track.filename.replace(/\.[^.]+$/, '') : null;

  const segment = segmentSeconds(timingFrom, timingTo);
  const overLimit = segment !== null && segment > maxSegmentSeconds;
  const backwards = segment !== null && segment <= 0;
  const roundSeconds = (value: number) => Math.round(value * 10) / 10;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-space-4">
        <h2 className="wizard-h flex items-center gap-space-3">
          <FigIcon name="icon-note.svg" h={19} />
          {t('wizard.track.intro')}
        </h2>
        <span className="flex items-center gap-space-4">
          <span className="wizard-body">{creditsLeft === null ? t('wizard.track.availableUnlimited') : t('wizard.track.available', { count: creditsLeft })}</span>
          <button
            type="button"
            onClick={() => { audioRef.current?.pause(); audioRef.current = null; setPlaying(false); setAudioUrl(null); reset(projectId); }}
            className="text-[14px] text-text-40 underline decoration-dotted underline-offset-4 transition hover:text-text-60"
          >
            {t('wizard.track.reset')}
          </button>
        </span>
      </div>

      <input ref={fileInputRef} className="sr-only" type="file" accept="audio/*" onChange={onUpload} />
      {!track ? (
        <div className="mt-space-6 grid gap-space-3">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn('dash-panel flex h-[100px] w-full items-center justify-center gap-space-5 px-space-7 transition max-lg:px-space-5', dragOver && 'brightness-150')}
          >
            <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-r10 bg-text">
              <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
                <defs>
                  <linearGradient id="plusGrad" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#8b6fe6" />
                    <stop offset="1" stopColor="#5f42b9" />
                  </linearGradient>
                </defs>
                <path d="M12 4v16M4 12h16" stroke="url(#plusGrad)" strokeWidth="2.4" strokeLinecap="round" />
              </svg>
            </span>
            <span className="wizard-body">{uploadMutation.isPending ? t('wizard.track.uploading') : t('wizard.track.dropHint')}</span>
          </button>
          {previousQuery.data?.track && (
            <button
              type="button"
              className="flex h-[56px] items-center justify-between rounded-r15 bg-grad-soft-10 px-space-5 text-left transition hover:brightness-125"
              onClick={() => {
                const previous = previousQuery.data.track;
                if (!previous) return;
                setTrack(previous);
                setAudioUrl(previous.localUrl || previous.s3Key);
              }}
            >
              <span className="truncate text-[16px] text-text-80">{t('wizard.track.previous')}</span>
              <span className="ml-space-4 truncate text-[16px] text-text">{previousQuery.data.track.filename}</span>
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          title={t('wizard.track.replaceFile')}
          className="dash-panel mt-space-6 flex h-[90px] w-full items-center justify-between gap-space-4 px-space-5"
        >
          <span className="wizard-body truncate text-left">{baseName}</span>
          <span className="flex shrink-0 items-center gap-space-3">
            <span className="soft-chip">{`${String(Math.floor(track.durationS / 60)).padStart(2, '0')}:${String(Math.round(track.durationS % 60)).padStart(2, '0')}`}</span>
            <span className="soft-chip">{ext}</span>
          </span>
        </button>
      )}

      {/* Лимит длины отрывка виден ДО ввода — рядом с заголовком, а не тостом постфактум */}
      <div className="mt-space-6 flex flex-wrap items-baseline justify-between gap-space-3">
        <h2 className="wizard-h">{t('wizard.track.timing')}</h2>
        <span className="flex items-center gap-space-3">
          <span className={cn('soft-chip', overLimit && '!text-[var(--warning)]')}>{t('wizard.track.segmentCap', { seconds: maxSegmentSeconds })}</span>
          {!paidPlan && (
            <a href="/app/pricing" className="text-[14px] text-text-40 underline decoration-dotted underline-offset-4 transition hover:text-text-60">
              {t('wizard.track.segmentUpgrade', { seconds: SEGMENT_SECONDS.paid })}
            </a>
          )}
        </span>
      </div>
      {/* Акцентная обводка с момента загрузки трека и дальше — пройденный/активный этап */}
      <div className={cn('mt-space-5 flex h-[190px] w-full items-center justify-center gap-space-4 px-space-5', track ? 'dash-panel' : 'dash-panel-white', (overLimit || backwards) && 'shadow-[inset_0_0_0_1.5px_var(--warning)]')}>
        <button
          type="button"
          aria-label={playing ? t('wizard.track.pause') : t('wizard.track.play')}
          disabled={!audioUrl}
          onClick={togglePlay}
          className="soft-btn h-[60px] w-[60px] shrink-0"
        >
          {playing ? (
            <span className="flex gap-[6px]" aria-hidden="true"><span className="h-[20px] w-[5px] rounded-[2px] bg-text" /><span className="h-[20px] w-[5px] rounded-[2px] bg-text" /></span>
          ) : (
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="currentColor" /></svg>
          )}
        </button>
        <span className="wizard-body">{t('wizard.track.from')}</span>
        <input
          value={timingFrom}
          onChange={(e) => {
            const next = clampTiming(e.target.value, track?.durationS);
            const accepted = commitTiming('timingFrom', next);
            if (accepted && /^\d{2}:\d{2}:\d{2}$/.test(next)) window.requestAnimationFrame(() => timingToInputRef.current?.focus());
          }}
          inputMode="numeric"
          maxLength={8}
          aria-label={t('wizard.track.segStart')}
          placeholder="00:00:00"
          className="soft-input"
        />
        <span className="wizard-body">{t('wizard.track.to')}</span>
        <input ref={timingToInputRef} value={timingTo} onChange={(e) => commitTiming('timingTo', clampTiming(e.target.value, track?.durationS))} inputMode="numeric" maxLength={8} aria-label={t('wizard.track.segEnd')} placeholder="00:00:00" className="soft-input" />
      </div>
      {/* Живая длина отрывка: перебор виден сразу, введённое не стирается */}
      <p className={cn('mt-space-5 max-w-[520px] text-[15px] leading-[1.5]', overLimit || backwards ? 'text-[var(--warning)]' : 'wizard-body')}>
        {backwards
          ? t('wizard.track.segmentBackwards')
          : overLimit
            ? t('wizard.track.segmentOver', { seconds: roundSeconds(segment), max: maxSegmentSeconds })
            : segment !== null
              ? t('wizard.track.segmentLen', { seconds: roundSeconds(segment) })
              : `${t('wizard.track.timingHint')} · ${t(paidPlan ? 'wizard.track.segmentCapPaid' : 'wizard.track.segmentCapTrial', { seconds: maxSegmentSeconds, paid: SEGMENT_SECONDS.paid })}`}
      </p>
    </div>
  );
}

/* Этап «Фон» вынесен в components/wizard/BackgroundPanel.tsx (Figma W12/3/13/14/15) */

/* Этап «Хук» вынесен в components/wizard/HookPanel.tsx (Figma W18/24–34) */

/* Этап «Текст» вынесен в components/wizard/SubtitlesPanel.tsx (Figma W16/17/23) */

/* Этап «Пул» вынесен в components/wizard/SlicePanel.tsx (Figma W19/W33) */

export function WizardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { push } = useToast();
  const stage = useWizardStore((state) => state.stage);
  const setStage = useWizardStore((state) => state.setStage);
  const projectId = useWizardStore((state) => state.projectId);
  const setProjectId = useWizardStore((state) => state.setProjectId);
  const state = useWizardStore();
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me });
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: api.projects });
  const wizardSessionQuery = useQuery({ queryKey: ['wizard-session'], queryFn: api.wizardSession });
  const restoredServerDraft = useRef(false);
  const qaStage = import.meta.env.DEV ? Number(params.get('qaStage') || 0) : 0;
  useEffect(() => {
    if (qaStage < 1 || qaStage > 5) return;
    // Explicit development-only visual fixture: every Figma stage is directly auditable
    // without faking browser storage or calling an LLM. It is excluded from production use.
    state.setTrack({
      id: 'qa-track', userId: 'user_1', s3Key: 'qa/track.mp3', filename: 'Название трека.mp3',
      durationS: 204, createdAt: '2026-07-15T00:00:00Z', expiresAt: '2026-07-22T00:00:00Z'
    });
    state.setField('lyrics', 'Я знаю — этот город не уснёт\nПока музыка ведёт нас вперёд');
    state.setField('timingFrom', '00:10:00');
    state.setField('timingTo', '00:22:00');
    state.setBackground({ mode: 'footage', footage: ['Ночной город', 'Неон'], photo: ['Крупный план'], color: '#8b6fe6', strobe: false, glue: 'Щелчок' });
    state.setHooks({ dropTime: '00:15:00', kind: 'sound', config: { sound: 'Звук' } });
    state.setHooks({ kind: 'object', config: { object: 'Квадрат' } });
    state.setHooks({ kind: 'effects', config: { effectHook: 'Молния', effectGlue: 'Щелчок', effectStyle: 'Глитч' } });
    state.setHooks({ kind: 'motion', config: { motion: 'Зум' } });
    state.setHooks({ kind: 'thought', config: { thought: 'Мысль' } });
    state.setHooks({ kind: 'effects' });
    state.setSubtitles({ color: '#f6f5fd', pool: ['Brat', 'Jakson', 'Impulse'] });
    state.setAllocation({
      total: 5,
      background: { 'footage:Ночной город': 2, 'footage:Неон': 1, 'photo:Крупный план': 1 },
      subtitles: { Brat: 2, Jakson: 1, Impulse: 1 },
      hooks: { sound: 1, object: 1, effects: 1, motion: 1, thought: 1 },
      seeded: true
    });
    setStage(qaStage);
    // qaStage is the only trigger: store changes above must not re-run this fixture.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qaStage]);

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

  const saveSessionMutation = useMutation({ mutationFn: () => api.saveWizardSession({ projectId, stage, data: state.stageData() }) });
  const submitMutation = useMutation({
    mutationFn: () => api.submitWizard({ projectId, stageData: state.stageData(), videosToGenerate: safeVideosToGenerate, idempotencyKey: state.final.idempotencyKey }),
    // newBatch, а НЕ reset: трек, текст и тайминги — вводные проекта, а не батча.
    // reset стирал их вместе с настройками батча, и «+» на втором батче уводил
    // человека обратно на загрузку файла — хотя ProjectDetailPage.addBatch
    // рассчитывает найти их в сторе и открыть сразу этап «Фон».
    onSuccess: (data) => { push({ variant: 'success', title: t('wizard.page.genStarted') }); state.newBatch(projectId); state.ackCarriedOver(); navigate(data.redirectTo); },
    // 402 — упёрлись в лимит роликов: причина + путь к решению, а не общий «не удалось»
    onError: (error) => {
      const limitReached = error instanceof ApiError && error.status === 402;
      push({
        variant: 'error',
        title: limitReached ? t('wizard.page.limitReached') : t('wizard.page.genFail'),
        text: limitReached
          ? String((error.detail as { detail?: string })?.detail ?? '')
          : t('wizard.page.genFailText'),
        action: limitReached ? { label: t('wizard.track.limitCta'), href: '/app/pricing' } : undefined
      });
    }
  });

  const creditsLeft = meQuery.data ? meQuery.data.creditsLeft : 1;
  // Этап «Пул»: суммы распределения должны сходиться с общим числом видео
  const fixedColorCount = state.background.color ? 1 : 0;
  const allocBgSum = Object.values(state.allocation.background).reduce((a, b) => a + b, 0);
  const allocSubsSum = Object.values(state.allocation.subtitles).reduce((a, b) => a + b, 0);
  const allocBalanced =
    state.allocation.total > 0 &&
    allocBgSum === state.allocation.total - fixedColorCount &&
    (state.subtitles.pool.length === 0 || allocSubsSum === state.allocation.total - fixedColorCount);
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

  // Этап «Трек» можно проскочить только мимо UI (персист стора, прямой ?qaStage, старый батч) —
  // возвращаем на него, иначе визард дойдёт до «Сгенерировать» с пустым треком.
  useEffect(() => {
    if (stage !== 1 && !trackReady) setStage(1);
  }, [setStage, stage, trackReady]);

  // «Продолжить» подсвечивается только при непустом выборе; кликабельность — отдельно
  const ready = useMemo(() => {
    if (stage === 1) return trackReady && !segmentInvalid;
    if (stage === 2) return backgroundVariations(state.background) > 0;
    if (stage === 3) return hookPills(state.hooks).length > 0;
    if (stage === 4) return state.subtitles.pool.length > 0;
    if (stage === 5) return allocBalanced && trackReady;
    return false;
  }, [allocBalanced, segmentInvalid, stage, state.background, state.hooks, state.subtitles.pool, trackReady]);

  const canContinue = useMemo(() => {
    // Хук опционален — с этапа можно уйти без выбора
    if (stage === 3) return true;
    return ready;
  }, [ready, stage]);

  // Порядок прохождения этапов по макету: Трек → Фон → Текст → Хук → Пул
  const STAGE_ORDER = [1, 2, 4, 3, 5];

  /*
   * Метрики прохождения визарда (из ревью): сколько времени человек проводит на этапе —
   * прежде всего на «Пуле», где раскладка самая тяжёлая, — и как часто возвращается назад.
   * Считаем по смене этапа: событие уходит с длительностью ПРЕДЫДУЩЕГО этапа, поэтому
   * отдельного «ушёл со страницы» не нужно.
   */
  const stageEnteredRef = useRef<{ stage: number; at: number }>({ stage, at: Date.now() });
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
    // STAGE_ORDER — константа модуля по смыслу, пересобирается каждый рендер
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const next = async () => {
    if (!canContinue) return;
    await saveSessionMutation.mutateAsync();
    const idx = STAGE_ORDER.indexOf(stage);
    if (idx < STAGE_ORDER.length - 1) setStage(STAGE_ORDER[idx + 1]);
    else submitMutation.mutate();
  };

  /*
   * Генерировать некуда — сразу открываем создание проекта. Раньше здесь был экран
   * «Какой проект?» с кнопкой на список проектов: лишний шаг, который вёл на такой же
   * пустой экран, да ещё и со своим фоном мимо каркаса.
   */
  if (!projectId && projectsQuery.data?.projects.length === 0) {
    return <Navigate to="/app/projects?new=1" replace />;
  }

  const track = state.track;
  const headerTitle = track ? track.filename.replace(/\.[^.]+$/, '') : t('wizard.track.nameFallback');
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
  // низ = аватар. Раньше визард жил на своём паттерне (-m-space-6 + h-dvh) и вставал по 32px,
  // из-за чего ужимался не так, как остальные страницы.
  return (
    <div className="flex min-h-0 flex-1 gap-[20px] max-lg:h-auto max-lg:flex-col md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]">
      {/*
        Новый батч наследует трек, текст и тайминги прошлого — но молча подменять
        вводные нельзя: человек либо не заметит, что генерит по старому отрывку,
        либо решит, что визард потерял шаг. Спрашиваем один раз, при входе.
      */}
      <Modal open={state.carriedOverInputs} title={t('wizard.page.carriedTitle')} onClose={() => state.ackCarriedOver()}>
        <p className="text-text-80">{t('wizard.page.carriedText')}</p>
        <div className="mt-space-5 flex flex-wrap gap-space-3">
          <Button onClick={() => state.ackCarriedOver()}>{t('wizard.page.carriedKeep')}</Button>
          <Button variant="ghost" onClick={() => { state.ackCarriedOver(); setStage(1); }}>
            {t('wizard.page.carriedEdit')}
          </Button>
        </div>
      </Modal>
      <section className="flex min-w-0 flex-1 flex-col gap-[20px]">
        <WizardHeaderCard
          title={headerTitle}
          artist={artist}
          onRename={track ? (value) => value && state.setTrack({ ...track, filename: `${value}.${track.filename.split('.').pop()}` }) : undefined}
        />
        {/* data-limits-dim: хост затемнения для LimitsIndicator (Figma W46 — на всю карточку) */}
        <div data-limits-dim className={cn('card-2 relative min-h-0 flex-1 px-space-7 py-space-6 max-lg:px-space-5', stage === 5 ? 'overflow-hidden' : 'subtle-scroll overflow-y-auto')}>
          {queryDown(projectsQuery) ? (
            /* без списка проектов визарду некуда сабмитить — честно говорим и даём повтор */
            <QueryError query={projectsQuery} className="!bg-transparent min-h-[420px]" />
          ) : projectsQuery.isLoading ? (
            <Skeleton className="h-[420px]" />
          ) : (
            <>
              {stage === 1 && <StageOne creditsLeft={creditsLeft} maxSegmentSeconds={maxSegmentSeconds} paidPlan={paidPlan} />}
              {stage === 2 && <StageBackground />}
              {stage === 3 && <StageHooks />}
              {stage === 4 && <StageSubtitles />}
              {stage === 5 && <StageSlice />}
            </>
          )}
        </div>
      </section>
      {stage === 1 ? (
        <TextPanel
          canContinue={canContinue}
          highlight={timingReady}
          // поле текста открывается только после тайминга: текст относится к отрывку
          timingReady={timingReady}
          loading={busy}
          onNext={next}
        />
      ) : stage === 2 ? (
        <BackgroundWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : stage === 3 ? (
        <HooksWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : stage === 4 ? (
        <SubtitlesWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      ) : (
        <SliceWorkZone ready={ready} canContinue={canContinue} loading={busy} onBack={back} onNext={next} />
      )}
    </div>
  );
}
