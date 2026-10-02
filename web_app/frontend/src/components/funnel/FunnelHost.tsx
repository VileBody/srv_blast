import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import type { FunnelState, GenerationJob, RatingReason, VideoRating, VideoVersion } from '../../lib/types';
import { useToast } from '../../contexts/ToastContext';
import { funnelSeen, markFunnelSeen, useFunnelUi, type UnlimitedContext } from '../../stores/funnelUi';
import { startNextBatch } from '../../stores/wizardStore';
import { FunnelDialog } from './FunnelSheet';
import { QuizPanel, UnlimitedPanel, quizPath, type QuizView, type UnlimitedStep } from './panels';
import { VideoRatingRow, type ActionStatus, type MethodologyState } from './parts';
import type { PitchReason, PitchScreen } from './PitchFlow';
import { useFunnelState } from './useFunnel';

/*
 * Хост модалок воронки (docs/BOT_TO_WEB_FLOW.md, раздел 4). Живёт в AppShell, поэтому
 * модалка переживает переходы между страницами генерации и батча.
 *
 * Платящим квиз, питч и безлимит не показываем: воронка конверсионная. Оценку роликов
 * видят все — это обратная связь по продукту.
 */

/** Сколько после готовности батча модалка безлимита ещё открывается сама. */
const OFFER_FRESH_MS = 24 * 3600 * 1000;

export function trackOfJob(job?: GenerationJob | null): { id?: string; title?: string } {
  const track = (job?.stageData?.track ?? null) as { id?: string; filename?: string } | null;
  // название для людей — без расширения файла («Нет любви.mp3» → «Нет любви»)
  return { id: track?.id, title: track?.filename?.replace(/\.[A-Za-z0-9]{1,5}$/, '') };
}

/* ------------------------------------------------------------------ квиз */

function useMethodology() {
  const [state, setState] = useState<MethodologyState>('idle');
  const [url, setUrl] = useState<string | null>(null);
  const [botLink, setBotLink] = useState<string | undefined>();
  const get = useCallback(() => {
    setState('sending');
    api.funnelMethodology()
      .then((res) => {
        if (res.url) { setUrl(res.url); setState('link'); }
        else if (res.sent) setState('sent');
        else { setBotLink(res.botLink); setState('needBot'); }
      })
      .catch(() => setState('error'));
  }, []);
  return { state, url, botLink, get };
}

/** Квиз пошагово по ответам с бэка; общий для модалки A и шага модалки B. */
function useQuiz(funnel: FunnelState | undefined, onDone: (bridge: string | null) => void) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, { id: string }>>({});
  const [current, setCurrent] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const questions = funnel?.questions ?? [];
  const merged = { ...(funnel?.survey.answers ?? {}), ...answers };
  const path = quizPath(questions, merged);
  const questionId = current ?? path.find((id) => !merged[id]) ?? path[0];
  const question = questions.find((q) => q.id === questionId);
  const answer = (answerId: string) => {
    if (!question) return;
    setPendingId(answerId);
    api.funnelSurvey(question.id, answerId)
      .then((res) => {
        setAnswers((prev) => ({ ...prev, [question.id]: { id: answerId } }));
        if (res.done) {
          void queryClient.invalidateQueries({ queryKey: ['funnel-state'] });
          onDone(res.bridge ?? null);
        } else setCurrent(res.next);
      })
      .finally(() => setPendingId(null));
  };
  const view: QuizView | null = question
    ? { kind: 'question', question, index: Math.max(0, path.indexOf(question.id)), total: path.length, pendingId }
    : null;
  return { view, answer };
}

function QuizModal({ jobId, onClose }: { jobId?: string; onClose: () => void }) {
  const titleId = useId();
  const funnel = useFunnelState();
  const methodology = useMethodology();
  const [bridge, setBridge] = useState<string | null | undefined>(undefined);
  const quiz = useQuiz(funnel.data, (b) => setBridge(b));
  useEffect(() => { if (jobId) markFunnelSeen(`quiz:${jobId}`); }, [jobId]);
  const view: QuizView | null = bridge !== undefined
    ? { kind: 'done', bridge, methodology: methodology.state, url: methodology.url, botLink: methodology.botLink }
    : quiz.view;
  if (!view) return null;
  return (
    <FunnelDialog open onClose={onClose} labelledBy={titleId}>
      <QuizPanel titleId={titleId} view={view} onAnswer={quiz.answer} onSkip={onClose} onMethodology={methodology.get} onClose={onClose} />
    </FunnelDialog>
  );
}

/* ------------------------------------------------------------------ безлимит */

function UnlimitedModal({ ctx, onClose }: { ctx: UnlimitedContext; onClose: () => void }) {
  const { t } = useTranslation();
  const titleId = useId();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const funnelQuery = useFunnelState();
  const funnel = funnelQuery.data;
  const ratingsQuery = useQuery({
    queryKey: ['funnel-ratings', ctx.jobId],
    queryFn: () => api.funnelRatings(ctx.jobId ?? ''),
    enabled: Boolean(ctx.jobId)
  });
  const methodology = useMethodology();
  const [ratings, setRatings] = useState<Record<string, VideoRating>>({});
  const [reasons, setReasons] = useState<RatingReason[]>([]);
  const [bridge, setBridge] = useState<string | null>(null);
  const [channel, setChannel] = useState<ActionStatus>('todo');
  const [manager, setManager] = useState<ActionStatus>('todo');
  const [unlockPending, setUnlockPending] = useState(false);
  const [pitchScreen, setPitchScreen] = useState<PitchScreen>('lead');
  const [pitchReason, setPitchReason] = useState<PitchReason | null>(null);
  const [index, setIndex] = useState(0);
  const quiz = useQuiz(funnel, (b) => { setBridge(b); setIndex((i) => i + 1); });

  const allRatings = { ...(ratingsQuery.data?.ratings ?? {}), ...ratings };
  const videos = (ctx.videos ?? []).filter((video) => video.status === 'COMPLETED');

  // Состав шагов фиксируется при открытии: иначе ответ в квизе выкидывал бы шаг из-под ног.
  const plan = useRef<{ rate: boolean; quiz: boolean } | null>(null);
  if (!plan.current && funnel && (!ctx.jobId || ratingsQuery.isFetched)) {
    const rated = ratingsQuery.data?.ratings ?? {};
    plan.current = {
      rate: videos.some((video) => !rated[video.id]),
      quiz: !funnel.survey.completed
    };
  }
  const maxScore = Math.max(0, ...Object.values(allRatings).map((r) => r.score));
  const high = maxScore >= 7 || videos.length === 0;

  const steps: UnlimitedStep[] = useMemo(() => {
    if (!funnel || !plan.current) return [];
    const unl = funnel.unlimited;
    if (unl && ctx.trackId && unl.trackId && unl.trackId !== ctx.trackId) return ['otherTrack'];
    if (unl) return ['done'];
    const out: UnlimitedStep[] = [];
    if (plan.current.rate) out.push('rate');
    if (!high) out.push('improve');
    if (plan.current.quiz) out.push('quiz', 'methodology');
    if (high) out.push('pitch');
    out.push('actions', 'done');
    return out;
  }, [funnel, high, ctx.trackId]);

  useEffect(() => {
    if (!funnel) return;
    if (funnel.actions.channel_subscribed) setChannel('done');
    if (funnel.actions.manager_contacted) setManager('done');
  }, [funnel]);

  if (!funnel || steps.length === 0) return null;
  const step = steps[Math.min(index, steps.length - 1)];
  const trackTitle = ctx.trackTitle ?? funnel.unlimited?.trackTitle ?? '';
  const managerText = t('funnel.actions.managerMessage', { track: trackTitle, code: funnel.links.managerCode });
  const next = () => setIndex((i) => Math.min(i + 1, steps.length - 1));

  return (
    <FunnelDialog open onClose={onClose} labelledBy={titleId}>
      <UnlimitedPanel
        titleId={titleId}
        view={{
          step,
          steps,
          trackTitle,
          rules: funnel.rules,
          videos,
          ratings: allRatings,
          reasons,
          fixHref: ctx.projectId ? startNextBatchHref(ctx.projectId) : undefined,
          quiz: quiz.view ?? undefined,
          bridge: bridge ?? funnel.survey.bridge,
          methodology: methodology.state,
          methodologyUrl: methodology.url,
          botLink: methodology.botLink ?? funnel.links.bot,
          channel,
          manager,
          channelLink: funnel.links.channel,
          managerLink: `${funnel.links.manager}?text=${encodeURIComponent(managerText)}`,
          managerCode: funnel.links.managerCode,
          unlockPending,
          quota: funnel.unlimited?.quota ?? null,
          otherTrackTitle: funnel.unlimited?.trackTitle,
          pitchScreen,
          pitchReason,
          survey: funnel.survey
        }}
        on={{
          onRate: (videoId, score) => {
            setRatings((prev) => ({ ...prev, [videoId]: { score, reasons: prev[videoId]?.reasons ?? [], comment: '' } }));
            void api.funnelRate({ videoId, jobId: ctx.jobId ?? '', projectId: ctx.projectId ?? '', score, reasons: [] });
          },
          onReasons: setReasons,
          onAnswer: quiz.answer,
          onMethodology: methodology.get,
          onNext: next,
          onChannelOpen: () => setChannel((s) => (s === 'done' ? s : 'todo')),
          onChannelCheck: () => {
            setChannel('checking');
            api.funnelChannel()
              .then((res) => setChannel(res.subscribed ? 'done' : 'missing'))
              .catch(() => setChannel('missing'));
          },
          onManager: () => {
            setManager('done');
            void api.funnelManager();
          },
          onUnlock: () => {
            if (!ctx.trackId) {
              push({ variant: 'error', title: t('funnel.errors.noTrack') });
              return;
            }
            setUnlockPending(true);
            api.funnelUnlock(ctx.trackId)
              .then((state) => {
                queryClient.setQueryData(['funnel-state'], state);
                setIndex(steps.indexOf('done'));
              })
              .catch(() => push({ variant: 'error', title: t('funnel.errors.unlock') }))
              .finally(() => setUnlockPending(false));
          },
          onGenerate: () => {
            onClose();
            navigate(ctx.projectId ? startNextBatch(ctx.projectId) : '/app/generate');
          },
          onClose,
          onPitchScreen: setPitchScreen,
          onPitchReason: (reason) => {
            setPitchReason(reason);
            setPitchScreen('reason');
            void api.trackEvent('pitch_objection', { reason }).catch(() => {});
          }
        }}
      />
    </FunnelDialog>
  );
}

function startNextBatchHref(projectId: string): string {
  return `/app/generate?project=${encodeURIComponent(projectId)}`;
}

/* ------------------------------------------------------------------ хост */

export function FunnelHost() {
  const open = useFunnelUi((state) => state.open);
  const close = useFunnelUi((state) => state.close);
  if (!open) return null;
  if (open.kind === 'quiz') return <QuizModal jobId={open.jobId} onClose={close} />;
  return <UnlimitedModal ctx={open.ctx} onClose={close} />;
}

/* ------------------------------------------------------------------ триггеры на страницах */

/**
 * Квиз — через пару секунд после старта генерации, один раз на батч, только бесплатным
 * и только пока квиз не пройден (ответы общие с ботом).
 */
export function useQuizOnGeneration(job: GenerationJob | undefined) {
  const funnel = useFunnelState();
  const openQuiz = useFunnelUi((state) => state.openQuiz);
  useEffect(() => {
    const data = funnel.data;
    if (!job || !data || data.hasPaid || data.survey.completed) return undefined;
    if (job.status === 'COMPLETED' || job.status === 'FAILED' || funnelSeen(`quiz:${job.id}`)) return undefined;
    const timer = window.setTimeout(() => openQuiz(job.id), 2500);
    return () => window.clearTimeout(timer);
  }, [job, funnel.data, openQuiz]);
}

/**
 * Оценка под каждым роликом + момент модалки «безлимит»: первая оценка 7+ открывает её
 * сразу; если 7+ так и не было — открываем, когда готов последний ролик.
 */
export function useVideoRatings(job: GenerationJob | undefined, projectId: string | undefined) {
  const queryClient = useQueryClient();
  const funnel = useFunnelState();
  const openUnlimited = useFunnelUi((state) => state.openUnlimited);
  const ratingsQuery = useQuery({
    queryKey: ['funnel-ratings', job?.id],
    queryFn: () => api.funnelRatings(job?.id ?? ''),
    enabled: Boolean(job?.id)
  });
  const [local, setLocal] = useState<Record<string, VideoRating>>({});
  const ratings = { ...(ratingsQuery.data?.ratings ?? {}), ...local };
  const offerable = Boolean(funnel.data && !funnel.data.hasPaid && !funnel.data.unlimited);
  const track = trackOfJob(job);

  const offer = useCallback(() => {
    if (!job || !offerable || funnelSeen(`unlimited:${job.id}`)) return;
    markFunnelSeen(`unlimited:${job.id}`);
    openUnlimited({
      source: 'results',
      jobId: job.id,
      projectId,
      trackId: track.id,
      trackTitle: track.title,
      videos: job.videos as VideoVersion[]
    });
  }, [job, offerable, openUnlimited, projectId, track.id, track.title]);

  // «Последний ролик готов» — только про свежий батч: старые (в т.ч. сделанные до
  // воронки) модалку сами не открывают, только оценка 7+.
  const fresh = Boolean(job && Date.now() - Date.parse(job.completedAt ?? job.createdAt) < OFFER_FRESH_MS);
  useEffect(() => {
    if (job?.status === 'COMPLETED' && fresh && ratingsQuery.isFetched) offer();
  }, [job?.status, fresh, ratingsQuery.isFetched, offer]);

  const save = (video: VideoVersion, score: number, reasons: RatingReason[]) => {
    setLocal((prev) => ({ ...prev, [video.id]: { score, reasons, comment: '' } }));
    void api.funnelRate({ videoId: video.id, jobId: job?.id ?? '', projectId: projectId ?? '', score, reasons })
      .then(() => queryClient.invalidateQueries({ queryKey: ['funnel-ratings', job?.id] }));
  };

  const render = (video: VideoVersion) => {
    if (video.status !== 'COMPLETED') return null;
    const current = ratings[video.id];
    return (
      <VideoRatingRow
        score={current?.score ?? null}
        reasons={current?.reasons ?? []}
        onRate={(score) => {
          save(video, score, current?.reasons ?? []);
          if (score >= 7) offer();
        }}
        onReasons={(next) => save(video, current?.score ?? 1, next)}
        fixHref={projectId ? startNextBatchHref(projectId) : undefined}
      />
    );
  };
  return { render };
}
