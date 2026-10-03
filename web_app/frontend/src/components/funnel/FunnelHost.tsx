import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../lib/api';
import type { FunnelState, GenerationJob, RatingReason, VideoRating, VideoVersion } from '../../lib/types';
import { useToast } from '../../contexts/ToastContext';
import { bindFunnelUser, funnelSeen, markFunnelSeen, markQuizSkipped, quizSkippedRecently, useFunnelUi, type UnlimitedContext } from '../../stores/funnelUi';
import { startNextBatch } from '../../stores/wizardStore';
import { guardDraft } from '../../stores/draftGuard';
import { FunnelDialog, FunnelSheet } from './FunnelSheet';
import { QuizPanel, UnlimitedPanel, quizPath, type QuizView, type UnlimitedStep } from './panels';
import { FN_GLYPH, VideoRatingRow, type ActionStatus, type LadderTier, type MethodologyState } from './parts';
import { Button, Icon } from '../ui/kit';
import { useCoverCount, useModalCount } from '../ui/Modal';
import { Skeleton } from '../ui/Skeleton';
import { apiErrorCode, isUnlimitedTrack, trackTitleOf, useFunnelState, useQuizCopy, useTripwirePurchase } from './useFunnel';

/*
 * Хост модалок воронки (docs/BOT_TO_WEB_FLOW.md, раздел 4). Живёт в AppShell, поэтому
 * модалка переживает переходы между страницами генерации и батча.
 *
 * Платящим квиз, питч и безлимит не показываем: воронка конверсионная. Оценку роликов
 * видят все — это обратная связь по продукту.
 */

/** Сколько после готовности батча модалка безлимита ещё открывается сама. */
const OFFER_FRESH_MS = 24 * 3600 * 1000;

export function trackOfJob(job?: GenerationJob | null): { id?: string; title?: string; audioHash?: string } {
  const track = (job?.stageData?.track ?? null) as { id?: string; filename?: string; audioHash?: string } | null;
  // название для людей — без расширения файла («Нет любви.mp3» → «Нет любви»)
  return { id: track?.id, title: trackTitleOf(track?.filename), audioHash: track?.audioHash };
}

/** Батч закончен: готов целиком или упал, но хоть один ролик готов (его можно оценить). */
export function batchFinished(job?: GenerationJob | null): boolean {
  if (!job) return false;
  if (job.status === 'COMPLETED') return true;
  return job.status === 'FAILED' && (job.videos ?? []).some((video) => video.status === 'COMPLETED');
}

/** Ошибка запроса воронки — тостом, а не необработанным отказом промиса. */
function useFunnelErrorToast() {
  const { t } = useTranslation();
  const { push } = useToast();
  return useCallback(() => push({ variant: 'error', title: t('funnel.errors.save') }), [push, t]);
}

/**
 * Сохранения оценки по одному ролику — строго по очереди. Два быстрых клика (7, потом 8)
 * уходили параллельно, и бэк мог принять их в обратном порядке: на сервере оставалась 7.
 * `pending` — по ролику идёт сохранение (строка оценки показывает это и ждёт).
 */
function useSerialSaves() {
  const chains = useRef(new Map<string, Promise<unknown>>());
  const [inFlight, setInFlight] = useState<Record<string, number>>({});
  const bump = useCallback((id: string, delta: number) => setInFlight((prev) => {
    const count = (prev[id] ?? 0) + delta;
    const next = { ...prev };
    if (count > 0) next[id] = count;
    else delete next[id];
    return next;
  }), []);
  const run = useCallback((id: string, task: () => Promise<unknown>) => {
    bump(id, 1);
    // упавшее предыдущее сохранение очередь не рвёт: о нём уже сказал свой тост
    const queued = (chains.current.get(id) ?? Promise.resolve()).catch(() => undefined).then(task);
    chains.current.set(id, queued);
    return queued.finally(() => {
      bump(id, -1);
      if (chains.current.get(id) === queued) chains.current.delete(id);
    });
  }, [bump]);
  return { run, pending: (id: string) => Boolean(inFlight[id]) };
}

/**
 * Оценки без привязанного Telegram бэк отдаёт 409 (решение бэка: воронка живёт в боте).
 * Повтор ответил бы тем же — не ретраим; прочие сбои — как по умолчанию (до трёх раз).
 */
function retryRatings(failures: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status === 409) return false;
  return failures < 3;
}

/* ------------------------------------------------------------------ квиз */

/**
 * Методичка одной кнопкой: отправили в Telegram — окно само идёт дальше (`onSent`),
 * а где она, говорит тост. Ссылка на файл и «открой бота» — та же кнопка в другом виде.
 *
 * Бот не может написать первым (`sent: false`) — показываем, какого бота открыть, и после
 * перехода по ссылке ждём повторного «Получить». 503 `methodology_unavailable` — отправка
 * не настроена на сервере: повтор не поможет, окно просто отпускает дальше.
 */
function useMethodology(onSent: () => void) {
  const { t } = useTranslation();
  const { push } = useToast();
  const [state, setState] = useState<MethodologyState>('idle');
  const [url, setUrl] = useState<string | null>(null);
  const [botLink, setBotLink] = useState<string | undefined>();
  const sentRef = useRef(onSent);
  sentRef.current = onSent;
  const get = useCallback(() => {
    setState('sending');
    api.funnelMethodology()
      .then((res) => {
        if (res.url) { setUrl(res.url); setState('link'); }
        else if (res.sent) {
          setState('sent');
          push({ variant: 'success', title: t('funnel.methodology.sentToast') });
          sentRef.current();
        } else if (res.botLink) { setBotLink(res.botLink); setState('needBot'); }
        // бэк обязан дать ссылку на бота вместе с sent:false — без неё это сбой, а не «открой бота»
        else setState('error');
      })
      .catch((error: unknown) => setState(apiErrorCode(error) === 'methodology_unavailable' ? 'unavailable' : 'error'));
  }, [push, t]);
  const botOpened = useCallback(() => setState('botOpened'), []);
  return { state, url, botLink, get, botOpened };
}

/**
 * Квиз пошагово по ответам с бэка; общий для модалки A и шага модалки B.
 * `touched` — человек ответил хоть на один вопрос в этом окне (квиз «его», а не пройден где-то ещё).
 */
function useQuiz(funnel: FunnelState | undefined, onDone: (bridge: string | null) => void) {
  const queryClient = useQueryClient();
  const failed = useFunnelErrorToast();
  const copy = useQuizCopy();
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
          onDone(copy.bridge(res.branch, res.bridge ?? null));
        } else setCurrent(res.next);
      })
      .catch(failed)
      .finally(() => setPendingId(null));
  };
  const view: QuizView | null = question
    ? { kind: 'question', question: copy.question(question), index: Math.max(0, path.indexOf(question.id)), total: path.length, pendingId }
    : null;
  return { view, answer, touched: Object.keys(answers).length > 0 || pendingId !== null };
}

function QuizModal({ jobId, onClose, onDismiss }: { jobId?: string; onClose: () => void; onDismiss: () => void }) {
  const titleId = useId();
  const funnel = useFunnelState();
  const methodology = useMethodology(onClose);
  const [bridge, setBridge] = useState<string | null | undefined>(undefined);
  const quiz = useQuiz(funnel.data, (b) => setBridge(b));
  useEffect(() => { if (jobId) markFunnelSeen(`quiz:${jobId}`); }, [jobId]);
  // закрыли до конца квиза — это пропуск: на следующих батчах неделю не открываем
  const finished = bridge !== undefined;
  const skip = useCallback(() => {
    if (!finished) markQuizSkipped();
    onClose();
  }, [finished, onClose]);
  const view: QuizView | null = bridge !== undefined
    ? { kind: 'done', bridge, methodology: methodology.state, url: methodology.url, botLink: methodology.botLink }
    : quiz.view;
  // Спрашивать нечего (квиз уже пройден — тут или в боте, вопросов нет) или воронка
  // недоступна — окно не держим открытым без UI: иначе стор считал бы квиз открытым,
  // и безлимит ждал бы его вечно. Пройденный квиз заново с первого вопроса не начинаем.
  const passed = Boolean(funnel.data?.survey.completed) && !quiz.touched && bridge === undefined;
  const nothing = (!view || passed) && (funnel.isError || Boolean(funnel.data));
  useEffect(() => { if (nothing) onDismiss(); }, [nothing, onDismiss]);
  if (!view || nothing) return null;
  return (
    <FunnelDialog open onClose={skip} labelledBy={titleId}>
      <QuizPanel titleId={titleId} view={view} onAnswer={quiz.answer} onSkip={skip} onMethodology={methodology.get} onBotOpened={methodology.botOpened} onClose={skip} />
    </FunnelDialog>
  );
}

/* ------------------------------------------------------------------ безлимит */

/** Окно безлимита, пока дочитываем воронку, оценки и ролики (или их не удалось загрузить). */
function UnlimitedPending({ titleId, onClose, onRetry }: { titleId: string; onClose: () => void; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <FunnelDialog open onClose={onClose} labelledBy={titleId}>
      <FunnelSheet
        titleId={titleId}
        stepKey={onRetry ? 'error' : 'loading'}
        title={t('funnel.badge')}
        description={onRetry ? t('funnel.errors.load') : undefined}
        onClose={onClose}
        actions={onRetry ? <Button variant="primary" onClick={onRetry}>{t('funnel.errors.retry')}</Button> : undefined}
      >
        {!onRetry && (
          <div className="flex flex-col gap-[12px]" aria-busy="true" aria-label={t('common.loading')}>
            <Skeleton className="h-[44px]" />
            <Skeleton className="h-[44px]" />
          </div>
        )}
      </FunnelSheet>
    </FunnelDialog>
  );
}

function UnlimitedModal({ ctx, onClose, onDismiss }: { ctx: UnlimitedContext; onClose: () => void; onDismiss: () => void }) {
  const { t } = useTranslation();
  const titleId = useId();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const clearBadge = useFunnelUi((state) => state.clearBadge);
  const funnelQuery = useFunnelState();
  const funnel = funnelQuery.data;
  const ratingsQuery = useQuery({
    queryKey: ['funnel-ratings', ctx.jobId],
    queryFn: () => api.funnelRatings(ctx.jobId ?? ''),
    enabled: Boolean(ctx.jobId),
    retry: retryRatings
  });
  // Ролики — из батча: у плашки после перезагрузки их нет (ссылки подписанные, не храним),
  // а в контексте с оценки 7+ — только готовые на тот момент. Ключ общий со страницей
  // генерации: там батч уже в кэше и обновляется сам.
  const jobQuery = useQuery({
    queryKey: ['job', ctx.jobId],
    queryFn: () => api.job(ctx.jobId ?? ''),
    enabled: Boolean(ctx.jobId)
  });
  const [index, setIndex] = useState(0);
  const methodology = useMethodology(() => setIndex((i) => i + 1));
  const failed = useFunnelErrorToast();
  const tripwire = useTripwirePurchase();
  const openTable = useOpenJobOnTable();
  const saves = useSerialSaves();
  const [ratings, setRatings] = useState<Record<string, VideoRating>>({});
  const [bridge, setBridge] = useState<string | null>(null);
  const [channel, setChannel] = useState<ActionStatus>('todo');
  const [manager, setManager] = useState<ActionStatus>('todo');
  const [unlockPending, setUnlockPending] = useState(false);
  const [tier, setTier] = useState<LadderTier | null>(null);
  const quiz = useQuiz(funnel, (b) => { setBridge(b); setIndex((i) => i + 1); });
  const copy = useQuizCopy();
  // Сама больше не откроется: окно на экране. Ставим здесь, а не в момент «пора открыть» —
  // окно, которое ждало квиз и пропало с перезагрузкой, откроется при следующем заходе.
  useEffect(() => { if (ctx.jobId) markFunnelSeen(`unlimited:${ctx.jobId}`); }, [ctx.jobId]);

  const allRatings = { ...(ratingsQuery.data?.ratings ?? {}), ...ratings };
  const videos = (jobQuery.data?.job.videos ?? ctx.videos ?? []).filter((video) => video.status === 'COMPLETED');

  // Состав шагов фиксируется, когда данные доехали: иначе ответ в квизе выкидывал бы шаг
  // из-под ног. План — состояние, а не ref: шаги пересчитываются, как только он появился
  // (с ref окно, открытое до загрузки оценок, оставалось пустым навсегда).
  const [plan, setPlan] = useState<{ rate: boolean; quiz: boolean } | null>(null);
  const ready = Boolean(funnel)
    && (!ctx.jobId || (ratingsQuery.isFetched && (Boolean(ctx.videos) || jobQuery.isFetched)));
  if (!plan && ready && funnel) {
    const rated = ratingsQuery.data?.ratings ?? {};
    // квиз, пропущенный недавно (в окне квиза или здесь), шагом безлимита не возвращаем
    setPlan({ rate: videos.some((video) => !rated[video.id]), quiz: !funnel.survey.completed && !quizSkippedRecently() });
  }

  // Без привязанного Telegram воронки нет (409 telegram_required): окно не держим и плашку не ставим.
  const unavailable = apiErrorCode(funnelQuery.error) === 'telegram_required';
  useEffect(() => { if (unavailable) onDismiss(); }, [unavailable, onDismiss]);

  useEffect(() => {
    if (!funnel) return;
    if (funnel.actions.channel_subscribed) setChannel('done');
    if (funnel.actions.manager_contacted) setManager('done');
  }, [funnel]);

  if (unavailable) return null;
  if (!funnel || !plan) {
    return (
      <UnlimitedPending
        titleId={titleId}
        onClose={onClose}
        onRetry={funnelQuery.isError ? () => void funnelQuery.refetch() : undefined}
      />
    );
  }

  const maxScore = Math.max(0, ...Object.values(allRatings).map((r) => r.score));
  const high = maxScore >= 7 || videos.length === 0;
  // «Что докрутить»: причины у каждого ролика свои (как в карточке); шаг правит их
  // у роликов с низкой оценкой, а если низких нет — у всех оценённых.
  const lowIds = videos.filter((video) => (allRatings[video.id]?.score ?? 10) <= 6).map((video) => video.id);
  const reasonTargets = lowIds.length ? lowIds : videos.filter((video) => allRatings[video.id]).map((video) => video.id);
  const reasons = Array.from(new Set(reasonTargets.flatMap((id) => allRatings[id]?.reasons ?? [])));
  const saveRating = (videoId: string, score: number, nextReasons: RatingReason[]) => {
    setRatings((prev) => ({ ...prev, [videoId]: { score, reasons: nextReasons, comment: '' } }));
    saves.run(videoId, () => api.funnelRate({ videoId, jobId: ctx.jobId ?? '', projectId: ctx.projectId ?? '', score, reasons: nextReasons }))
      .then(() => queryClient.invalidateQueries({ queryKey: ['funnel-ratings', ctx.jobId] }))
      .catch(failed);
  };

  const steps = ((): UnlimitedStep[] => {
    const unl = funnel.unlimited;
    // Трек сверяем по хэшу: безлимит на другом треке никогда не показываем как «открыт».
    if (unl && isUnlimitedTrack(unl, { id: ctx.trackId, audioHash: ctx.audioHash }) === false) return ['otherTrack'];
    if (unl) return ['done'];
    const out: UnlimitedStep[] = [];
    if (plan.rate) out.push('rate');
    if (!high) out.push('improve');
    if (plan.quiz) out.push('quiz', 'methodology');
    if (high) out.push('pitch');
    out.push('actions', 'done');
    // Квиз прошли в другом месте (модалка A, другая вкладка), пока окно открыто: шаги квиза
    // впереди выкидываем, а не начинаем его заново с первого вопроса. Пройденное не трогаем,
    // чтобы текущий шаг не съехал.
    const quizGone = !quiz.touched && (funnel.survey.completed || !quiz.view);
    return quizGone ? out.filter((step, at) => at < index || (step !== 'quiz' && step !== 'methodology')) : out;
  })();

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
          canFix: Boolean(ctx.projectId),
          quiz: quiz.view ?? undefined,
          bridge: bridge ?? copy.bridge(funnel.survey.branch, funnel.survey.bridge),
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
          tier,
          buyPending: tripwire.isPending,
          survey: funnel.survey
        }}
        on={{
          // причины, выбранные в карточке ролика, не затираем
          onRate: (videoId, score) => saveRating(videoId, score, allRatings[videoId]?.reasons ?? []),
          onReasons: (next) => reasonTargets.forEach((id) => saveRating(id, allRatings[id]?.score ?? 1, next)),
          onAnswer: quiz.answer,
          onMethodology: methodology.get,
          onMethodologyBotOpened: methodology.botOpened,
          onNext: next,
          onSkipQuiz: () => {
            markQuizSkipped();
            // квиз и методичка идут парой: пропуск ведёт сразу за методичку
            const methodologyAt = steps.indexOf('methodology');
            setIndex(methodologyAt >= 0 ? methodologyAt + 1 : index + 1);
          },
          onChannelOpen: () => setChannel((s) => (s === 'done' ? s : 'todo')),
          onChannelCheck: () => {
            setChannel('checking');
            api.funnelChannel()
              .then((res) => setChannel(res.subscribed ? 'done' : 'missing'))
              .catch(() => setChannel('missing'));
          },
          onManager: () => {
            setManager('done');
            // не засчитали переход — возвращаем шаг, чтобы нажать ещё раз
            api.funnelManager().catch(() => {
              setManager('todo');
              failed();
            });
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
                clearBadge();
                setIndex(steps.indexOf('done'));
              })
              .catch(() => push({ variant: 'error', title: t('funnel.errors.unlock') }))
              .finally(() => setUnlockPending(false));
          },
          onGenerate: () => {
            onClose();
            // Упёрлись в лимит посреди настройки или окно открыто прямо в визарде: настройка
            // батча — работа человека, «новый батч» стёр бы фон, хуки, субтитры и правки стола.
            // Просто возвращаем к визарду, «Сгенерировать» он нажмёт сам.
            if (ctx.source === 'gate' || location.pathname.startsWith('/app/generate')) return;
            const projectId = ctx.projectId;
            if (!projectId) {
              navigate('/app/generate');
              return;
            }
            // другой проект в черновике стёрся бы молча — сначала спрашиваем
            guardDraft({ projectId }, () => navigate(startNextBatch(projectId)));
          },
          onClose,
          onFix: () => {
            onClose();
            openTable(ctx.projectId, ctx.jobId);
          },
          onTier: setTier,
          onBuyTripwire: () => {
            if (!ctx.trackId) {
              push({ variant: 'error', title: t('funnel.errors.noTrack') });
              return;
            }
            tripwire.mutate(ctx.trackId, { onError: () => push({ variant: 'error', title: t('funnel.errors.save') }) });
          }
        }}
      />
    </FunnelDialog>
  );
}

/**
 * «Открыть таймлайн»: этот батч на монтажном столе с его настройками (stores/reopenJob).
 * Переход — через navigate, без перезагрузки: флаг «открыть стол» в localStorage не живёт.
 * Батч не загрузился — говорим об этом, а не открываем молча пустой визард.
 */
function useOpenJobOnTable() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { push } = useToast();
  return useCallback((projectId?: string, jobId?: string) => {
    if (!projectId) return;
    if (!jobId) {
      guardDraft({ projectId }, () => navigate(startNextBatch(projectId)));
      return;
    }
    // модуль тянет раскладку «Пула» — грузим по требованию, как импорт из бота
    Promise.all([api.job(jobId), import('../../stores/reopenJob')])
      .then(([{ job }, { openJobOnTable }]) => {
        // Батч заменяет черновик целиком: недоделанную настройку другого трека — только по «Заменить»
        const key = (job.stageData?.final as { idempotencyKey?: unknown } | undefined)?.idempotencyKey;
        guardDraft(
          { projectId: job.projectId, idempotencyKey: typeof key === 'string' ? key : undefined },
          () => navigate(openJobOnTable(job))
        );
      })
      .catch(() => push({ variant: 'error', title: t('funnel.errors.reopen') }));
  }, [navigate, push, t]);
}

/* ------------------------------------------------------------------ хост */

export function FunnelHost() {
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  const userId = meQuery.data?.user.id ?? null;
  // Синхронно, до страниц в этом же проходе рендера: funnelSeen смотрит в ключ этого аккаунта.
  bindFunnelUser(userId);
  const syncUser = useFunnelUi((state) => state.syncUser);
  useEffect(() => { if (userId) syncUser(userId); }, [userId, syncUser]);

  const open = useFunnelUi((state) => state.open);
  const close = useFunnelUi((state) => state.close);
  const dismiss = useFunnelUi((state) => state.dismiss);
  const modals = useModalCount((state) => state.count);
  /*
   * Чужая модалка на экране (обязательный профиль, диалоги визарда) — квиз и безлимит
   * ждут её закрытия: в сторе окно уже открыто, рисуем его, когда экран свободен.
   * `live` — наше окно уже на экране: его собственный FunnelDialog тоже в счётчике,
   * и переход квиз → безлимит (из очереди) не должен снова ждать. Ставится прямо
   * в рендере, а не эффектом: иначе +1 от своего же окна успевал его снять, и окно
   * монтировалось-размонтировалось по кругу.
   */
  const [live, setLive] = useState(false);
  if (open && !live && modals === 0) setLive(true);
  if (!open && live) setLive(false);

  if (!open || !live) return null;
  if (open.kind === 'quiz') return <QuizModal jobId={open.jobId} onClose={close} onDismiss={dismiss} />;
  return <UnlimitedModal key={open.ctx.jobId ?? open.ctx.source} ctx={open.ctx} onClose={close} onDismiss={dismiss} />;
}

/**
 * Плашка «Безлимит на трек» в углу (docs/BOT_TO_WEB_FLOW.md, раздел 4): модалку закрыли,
 * не пройдя, — она остаётся под рукой. Только бесплатным и пока безлимит не открыт.
 */
export function FunnelBadge() {
  const { t } = useTranslation();
  const badge = useFunnelUi((state) => state.badge);
  const open = useFunnelUi((state) => state.open);
  const openUnlimited = useFunnelUi((state) => state.openUnlimited);
  const clearBadge = useFunnelUi((state) => state.clearBadge);
  const location = useLocation();
  const modals = useModalCount((state) => state.count);
  const covers = useCoverCount((state) => state.count);
  const funnel = useFunnelState(Boolean(badge)).data;
  const settled = Boolean(funnel && (funnel.hasPaid || funnel.unlimited));
  useEffect(() => {
    if (badge && settled) clearBadge();
  }, [badge, settled, clearBadge]);
  /*
   * Плашка фиксирована в правом нижнем углу — там же кнопки «Опубликовать» выкладки в TikTok,
   * действия модалок и монтажного стола. Под ними её не показываем: она бы их накрыла
   * (на телефоне особенно) или висела бы поверх диалогов визарда.
   */
  const hidden = location.pathname.endsWith('/post') || modals > 0 || covers > 0;
  const visible = Boolean(badge && funnel && !settled && !open && !hidden);
  // Пока плашка видна, на телефоне у содержимого есть запас снизу (index.css): последние
  // кнопки страницы прокручиваются выше неё, а не прячутся под ней.
  useEffect(() => {
    if (!visible) return undefined;
    document.documentElement.setAttribute('data-funnel-badge', '');
    return () => document.documentElement.removeAttribute('data-funnel-badge');
  }, [visible]);
  if (!visible || !badge) return null;
  return (
    <button
      type="button"
      onClick={() => openUnlimited(badge)}
      className="fn-step fixed bottom-[24px] right-[24px] z-sidebar inline-flex h-ctl-sm items-center gap-[6px] rounded-full bg-accent-strong px-[14px] text-ui-14 text-text shadow-soft transition-transform duration-150 active:scale-[.97] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-light max-md:bottom-[16px] max-md:right-[16px]"
    >
      <Icon>{FN_GLYPH.key}</Icon>
      {t('funnel.badge')}
    </button>
  );
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
    if (!job || !data || data.hasPaid || data.survey.completed || quizSkippedRecently()) return undefined;
    if (job.status === 'COMPLETED' || job.status === 'FAILED' || funnelSeen(`quiz:${job.id}`)) return undefined;
    const timer = window.setTimeout(() => openQuiz(job.id), 2500);
    return () => window.clearTimeout(timer);
  }, [job, funnel.data, openQuiz]);
}

/**
 * Батчи, которые в этой вкладке видели недоделанными. Их готовность — «на глазах»:
 * модуль, а не ref, потому что страница генерации сама уводит на страницу батча,
 * и готовность часто ловит уже другой экземпляр хука.
 */
const watchedRunning = new Set<string>();

/**
 * Оценка под каждым роликом + момент модалки «безлимит»: первая оценка 7+ открывает её
 * сразу; если 7+ так и не было — открываем, когда готов последний ролик.
 */
export function useVideoRatings(job: GenerationJob | undefined, projectId: string | undefined) {
  const openTable = useOpenJobOnTable();
  const queryClient = useQueryClient();
  const funnel = useFunnelState();
  const failed = useFunnelErrorToast();
  const openUnlimited = useFunnelUi((state) => state.openUnlimited);
  const badge = useFunnelUi((state) => state.badge);
  const ratingsQuery = useQuery({
    queryKey: ['funnel-ratings', job?.id],
    queryFn: () => api.funnelRatings(job?.id ?? ''),
    // без состояния воронки строку всё равно не рисуем (см. render) — и не спрашиваем
    enabled: Boolean(job?.id && funnel.data),
    retry: retryRatings
  });
  const [local, setLocal] = useState<Record<string, VideoRating>>({});
  const ratings = { ...(ratingsQuery.data?.ratings ?? {}), ...local };
  const offerable = Boolean(funnel.data && !funnel.data.hasPaid && !funnel.data.unlimited);
  const track = trackOfJob(job);

  /**
   * `again` — новая оценка 7+ в том же батче: модалку, которую закрыли не пройдя,
   * открываем снова, но только пока о ней помнит плашка в углу (без спама).
   */
  const offer = useCallback((again = false) => {
    if (!job || !offerable) return;
    if (funnelSeen(`unlimited:${job.id}`) && !(again && badge?.jobId === job.id)) return;
    // «показано» ставит само окно, когда появилось на экране (UnlimitedModal): пока оно
    // ждёт квиз или чужую модалку, повторный вызов в сторе ничего не меняет
    openUnlimited({
      source: 'results',
      jobId: job.id,
      projectId,
      trackId: track.id,
      audioHash: track.audioHash,
      trackTitle: track.title,
      videos: job.videos as VideoVersion[]
    });
  }, [job, offerable, openUnlimited, projectId, track.id, track.audioHash, track.title, badge?.jobId]);

  /*
   * «Последний ролик готов» открывает модалку сама в двух случаях:
   *  - батч закончился на глазах — в этой вкладке его видели недоделанным;
   *  - это батч, с которым открыли страницу, и он свежий (< суток): ушёл до конца
   *    рендера и вернулся. Старые батчи (в т.ч. сделанные до воронки) сами не открывают.
   * Выбор другого батча пилюлей на странице проекта модалку не открывает — только оценка 7+.
   * Частично упавший батч (FAILED, но часть роликов готова) — тоже «последний ролик готов».
   */
  const entry = useRef<{ projectId?: string; jobId: string } | null>(null);
  if (job && (!entry.current || entry.current.projectId !== projectId)) entry.current = { projectId, jobId: job.id };
  const finished = batchFinished(job);
  useEffect(() => {
    if (job && !finished) watchedRunning.add(job.id);
  }, [job, finished]);
  useEffect(() => {
    if (!job || !finished || !ratingsQuery.isFetched) return;
    const fresh = Date.now() - Date.parse(job.completedAt ?? job.createdAt) < OFFER_FRESH_MS;
    if (watchedRunning.has(job.id) || (fresh && entry.current?.jobId === job.id)) offer();
  }, [job, finished, ratingsQuery.isFetched, offer]);

  const saves = useSerialSaves();
  const save = (video: VideoVersion, score: number, reasons: RatingReason[]) => {
    setLocal((prev) => ({ ...prev, [video.id]: { score, reasons, comment: '' } }));
    saves.run(video.id, () => api.funnelRate({ videoId: video.id, jobId: job?.id ?? '', projectId: projectId ?? '', score, reasons }))
      .then(() => queryClient.invalidateQueries({ queryKey: ['funnel-ratings', job?.id] }))
      .catch(failed);
  };

  // Без привязанного Telegram воронки нет (409 telegram_required): строку оценки не рисуем.
  const unavailable = apiErrorCode(funnel.error) === 'telegram_required';

  const render = (video: VideoVersion) => {
    // Строка — только когда воронка доехала: иначе у аккаунта без Telegram она мелькала
    // до ответа 409 и пропадала.
    if (video.status !== 'COMPLETED' || unavailable || !funnel.data) return null;
    const current = ratings[video.id];
    return (
      <VideoRatingRow
        score={current?.score ?? null}
        reasons={current?.reasons ?? []}
        onRate={(score) => {
          save(video, score, current?.reasons ?? []);
          // повторно — только если этот ролик раньше не был на 7+
          if (score >= 7) offer((current?.score ?? 0) < 7);
        }}
        onReasons={(next) => save(video, current?.score ?? 1, next)}
        onFix={projectId ? () => openTable(projectId, job?.id) : undefined}
        pending={saves.pending(video.id)}
      />
    );
  };
  return { render };
}
