import { useTranslation } from 'react-i18next';
import type { FunnelQuestion, FunnelQuota, FunnelRules, FunnelState, RatingReason, VideoVersion } from '../../lib/types';
import { Button, ButtonLink, GLYPH, Icon } from '../ui/kit';
import { FunnelSheet } from './FunnelSheet';
import { PitchFlow } from './PitchFlow';
import {
  FN_GLYPH,
  MethodologyAction,
  PitchLadder,
  QuizQuestion,
  RatingScale,
  ReasonPills,
  UnlockActionRow,
  UnlockedTicket,
  type ActionStatus,
  type LadderTier,
  type MethodologyState
} from './parts';

/*
 * Модалки воронки как функции состояния → UI. Контейнеры (FunnelHost) держат данные
 * и переходы, витрина /dev/funnel рисует эти же панели с готовыми состояниями.
 * Копирайт — в i18n (funnel.*), вопросы квиза и мостики — с бэка (общие с ботом).
 */

/* ------------------------------------------------------------------ квиз (модалка A) */

export type QuizView =
  | { kind: 'question'; question: FunnelQuestion; index: number; total: number; pendingId?: string | null }
  | { kind: 'done'; bridge: string | null; methodology: MethodologyState; url?: string | null; botLink?: string };

/** Сколько вопросов на пути: по уже данным ответам, дальше — по первому варианту. */
export function quizPath(questions: FunnelQuestion[], answers: Record<string, { id: string }>): string[] {
  const byId = new Map(questions.map((q) => [q.id, q]));
  const path: string[] = [];
  let current: string | undefined = questions[0]?.id;
  while (current && byId.has(current) && path.length < 10) {
    path.push(current);
    const q = byId.get(current) as FunnelQuestion;
    const answer: string | undefined = answers[current]?.id ?? q.options[0]?.id;
    current = answer ? q.next[answer] || undefined : undefined;
  }
  return path;
}

export function QuizPanel({
  view,
  onAnswer,
  onSkip,
  onMethodology,
  onClose,
  titleId
}: {
  view: QuizView;
  onAnswer: (answerId: string) => void;
  onSkip: () => void;
  onMethodology: () => void;
  onClose: () => void;
  titleId?: string;
}) {
  const { t } = useTranslation();
  if (view.kind === 'done') {
    return (
      <FunnelSheet
        titleId={titleId}
        stepKey="done"
        title={t('funnel.quiz.doneTitle')}
        description={view.bridge ?? undefined}
        onClose={onClose}
        actions={<MethodologyAction state={view.methodology} url={view.url} botLink={view.botLink} onGet={onMethodology} onOpened={onClose} />}
      >
        <MethodologyStatus state={view.methodology} />
      </FunnelSheet>
    );
  }
  return (
    <FunnelSheet
      titleId={titleId}
      stepKey={view.question.id}
      title={view.question.text}
      // зачем отвечать — только на первом вопросе; дальше в окне один вопрос
      description={view.index === 0 ? t('funnel.quiz.description') : undefined}
      onClose={onClose}
      progress={{ total: view.total, current: view.index }}
      actions={<Button variant="ghost" onClick={onSkip}>{t('funnel.quiz.skip')}</Button>}
    >
      <QuizQuestion question={view.question} onAnswer={onAnswer} pendingId={view.pendingId} />
    </FunnelSheet>
  );
}

/** Строка о методичке под мостиком — только когда есть что сказать (бот не запущен, сбой). */
function MethodologyStatus({ state }: { state: MethodologyState }) {
  const { t } = useTranslation();
  if (state !== 'needBot' && state !== 'error') return null;
  return <p className={state === 'error' ? 'text-ui-14 text-warning' : 'text-ui-14 text-text-60'}>{t(`funnel.methodology.${state}`)}</p>;
}

/* ------------------------------------------------------------------ безлимит (модалка B) */

export type UnlimitedStep = 'rate' | 'improve' | 'quiz' | 'methodology' | 'pitch' | 'actions' | 'done' | 'otherTrack';

export interface UnlimitedView {
  step: UnlimitedStep;
  steps: UnlimitedStep[];
  trackTitle: string;
  rules: FunnelRules;
  /* rate */
  videos?: VideoVersion[];
  ratings?: Record<string, { score: number; reasons: RatingReason[] }>;
  ratingPending?: boolean;
  /* improve */
  reasons?: RatingReason[];
  /** можно открыть этот батч на монтажном столе */
  canFix?: boolean;
  /* quiz / methodology */
  quiz?: QuizView;
  bridge?: string | null;
  methodology?: MethodologyState;
  methodologyUrl?: string | null;
  botLink?: string;
  /* actions */
  channel?: ActionStatus;
  manager?: ActionStatus;
  channelLink?: string;
  managerLink?: string;
  managerCode?: string;
  unlockPending?: boolean;
  /* done */
  quota?: FunnelQuota | null;
  otherTrackTitle?: string | null;
  /* otherTrack: выбранная строка лестницы */
  tier?: LadderTier | null;
  buyPending?: boolean;
  /* pitch: ответы квиза для персонального довода */
  survey?: FunnelState['survey'];
}

export interface UnlimitedHandlers {
  onRate: (videoId: string, score: number) => void;
  onReasons: (next: RatingReason[]) => void;
  onAnswer: (answerId: string) => void;
  onMethodology: () => void;
  onNext: () => void;
  /** пропустить квиз вместе с методичкой — сразу к следующему шагу окна */
  onSkipQuiz?: () => void;
  onChannelOpen: () => void;
  onChannelCheck: () => void;
  onManager: () => void;
  onUnlock: () => void;
  onGenerate: () => void;
  onClose: () => void;
  /** открыть батч на монтажном столе с его настройками */
  onFix?: () => void;
  onTier?: (tier: LadderTier) => void;
  /** купить трипваер на трек этого батча */
  onBuyTripwire?: () => void;
}

export function UnlimitedPanel({ view, on, titleId }: { view: UnlimitedView; on: UnlimitedHandlers; titleId?: string }) {
  const { t } = useTranslation();
  const flow: UnlimitedStep[] = view.steps.filter((s) => s !== 'done' && s !== 'otherTrack');
  const progress = flow.includes(view.step) ? { total: flow.length, current: flow.indexOf(view.step) } : undefined;
  const common = { titleId, stepKey: view.step, onClose: on.onClose, progress };
  const next = (label = t('funnel.next'), disabled = false) => (
    <Button variant="primary" ready={!disabled} disabled={disabled} onClick={on.onNext}>{label}</Button>
  );

  switch (view.step) {
    case 'rate': {
      const rated = Object.keys(view.ratings ?? {}).length;
      return (
        <FunnelSheet {...common} title={t('funnel.rate.title')} description={t('funnel.rate.description')} actions={next(t('funnel.next'), rated === 0)}>
          <ul className="flex flex-col gap-[8px]">
            {(view.videos ?? []).map((video) => (
              <li key={video.id} className="flex items-center justify-between gap-[12px] rounded-r15 bg-panel px-[16px] py-[10px] max-md:flex-col max-md:items-start">
                <span className="text-ui-16 text-text">{t('projectDetail.videoN', { n: video.index })}</span>
                <RatingScale
                  value={view.ratings?.[video.id]?.score ?? null}
                  onChange={(score) => on.onRate(video.id, score)}
                  disabled={view.ratingPending}
                  label={t('projectDetail.videoN', { n: video.index })}
                />
              </li>
            ))}
          </ul>
        </FunnelSheet>
      );
    }
    case 'improve':
      return (
        <FunnelSheet {...common} title={t('funnel.improve.title')} description={t('funnel.improve.description')} actions={next()}>
          <ReasonPills value={view.reasons ?? []} onChange={on.onReasons} />
          <ul className="mt-[16px] flex flex-col">
            {(['footage', 'transitions', 'subtitles'] as const).map((reason) => (
              <li key={reason} className="flex items-start gap-[12px] border-t border-line py-[12px] first:border-t-0">
                <span className="mt-[4px] text-ui-16 text-accent-light"><Icon>{GLYPH.check}</Icon></span>
                <span className="text-ui-14 text-text-80 [text-wrap:pretty]">{t(`funnel.improve.tip.${reason}`)}</span>
              </li>
            ))}
          </ul>
          {view.canFix && on.onFix && (
            <Button variant="secondary" size="sm" className="mt-[8px]" onClick={on.onFix} iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}>
              {t('funnel.improve.open')}
            </Button>
          )}
        </FunnelSheet>
      );
    case 'quiz': {
      const quiz = view.quiz;
      if (!quiz || quiz.kind !== 'question') return null;
      return (
        <FunnelSheet
          {...common}
          stepKey={`quiz-${quiz.question.id}`}
          title={quiz.question.text}
          description={quiz.index === 0 ? t('funnel.quiz.description') : undefined}
          // квиз — не условие безлимита: без «Пропустить» окно держало бы до ответа
          actions={on.onSkipQuiz ? <Button variant="ghost" onClick={on.onSkipQuiz}>{t('funnel.quiz.skip')}</Button> : undefined}
        >
          <QuizQuestion question={quiz.question} onAnswer={on.onAnswer} pendingId={quiz.pendingId} />
        </FunnelSheet>
      );
    }
    case 'methodology':
      return (
        <FunnelSheet
          {...common}
          title={t('funnel.quiz.doneTitle')}
          description={view.bridge ?? undefined}
          actions={
            <>
              {/* Ручка методички падает или бот не запущен (и ссылки на него нет): без «Дальше»
                  до действий и безлимита было бы не дойти */}
              {(view.methodology === 'error' || view.methodology === 'needBot') && (
                <Button variant="secondary" onClick={on.onNext}>{t('funnel.next')}</Button>
              )}
              <MethodologyAction state={view.methodology ?? 'idle'} url={view.methodologyUrl} botLink={view.botLink} onGet={on.onMethodology} onOpened={on.onNext} />
            </>
          }
        >
          <MethodologyStatus state={view.methodology ?? 'idle'} />
        </FunnelSheet>
      );
    case 'pitch':
      return (
        <PitchFlow
          titleId={titleId}
          progress={progress}
          survey={view.survey}
          onNext={on.onNext}
          onClose={on.onClose}
        />
      );
    case 'actions': {
      const both = view.channel === 'done' && view.manager === 'done';
      return (
        <FunnelSheet
          {...common}
          title={t('funnel.actions.title')}
          description={t('funnel.actions.description')}
          actions={
            <Button variant="primary" ready={both} disabled={!both} loading={view.unlockPending} onClick={on.onUnlock} icon={<Icon>{FN_GLYPH.key}</Icon>}>
              {t('funnel.actions.unlock')}
            </Button>
          }
        >
          <ol>
            <UnlockActionRow
              index={1}
              status={view.channel ?? 'todo'}
              title={t('funnel.actions.channelTitle')}
              text={view.channel === 'missing' ? <span className="text-warning">{t('funnel.actions.channelMissing')}</span> : t(view.channel === 'done' ? 'funnel.actions.channelDone' : 'funnel.actions.channelText')}
            >
              {view.channel !== 'done' && (
                <>
                  <ButtonLink variant="secondary" size="sm" href={view.channelLink} target="_blank" rel="noreferrer" onClick={on.onChannelOpen} icon={<Icon>{FN_GLYPH.send}</Icon>}>
                    {t('funnel.actions.channelOpen')}
                  </ButtonLink>
                  <Button variant="ghost" size="sm" loading={view.channel === 'checking'} onClick={on.onChannelCheck}>
                    {t('funnel.actions.channelCheck')}
                  </Button>
                </>
              )}
            </UnlockActionRow>
            <UnlockActionRow
              index={2}
              status={view.manager ?? 'todo'}
              title={t('funnel.actions.managerTitle')}
              text={view.manager === 'done' ? t('funnel.actions.managerDone') : t('funnel.actions.managerText', { code: view.managerCode })}
            >
              {view.manager !== 'done' && (
                <ButtonLink variant="secondary" size="sm" href={view.managerLink} target="_blank" rel="noreferrer" onClick={on.onManager} icon={<Icon>{FN_GLYPH.send}</Icon>}>
                  {t('funnel.actions.managerOpen')}
                </ButtonLink>
              )}
            </UnlockActionRow>
          </ol>
        </FunnelSheet>
      );
    }
    case 'done':
      return (
        <FunnelSheet
          titleId={titleId}
          stepKey="done"
          onClose={on.onClose}
          title={t('funnel.done.title')}
          // «собирай бесплатно» — только когда сейчас есть что собрать; иначе время в билете
          description={t(view.quota && !view.quota.allowed ? 'funnel.done.descriptionWait' : 'funnel.done.description')}
          // одна длинная кнопка: закрыть — крестик в шапке
          actions={<Button variant="primary" className="flex-1" onClick={on.onGenerate}>{t('funnel.done.generate')}</Button>}
        >
          <UnlockedTicket trackTitle={view.trackTitle} rules={view.rules} quota={view.quota ?? null} />
        </FunnelSheet>
      );
    case 'otherTrack':
      return (
        <FunnelSheet
          titleId={titleId}
          stepKey="other"
          onClose={on.onClose}
          title={t('funnel.other.title')}
          description={t('funnel.other.description', { track: view.otherTrackTitle ?? '' })}
          actions={<TierAction tier={view.tier ?? null} pending={view.buyPending} on={on} />}
        >
          <PitchLadder rules={view.rules} selected={view.tier} onSelect={on.onTier} />
        </FunnelSheet>
      );
    default:
      return null;
  }
}

/** Кнопка окна под выбранную строку лестницы: платное — «Купить», бесплатное — собирать дальше. */
function TierAction({ tier, pending, on }: { tier: LadderTier | null; pending?: boolean; on: UnlimitedHandlers }) {
  const { t } = useTranslation();
  if (tier === 'blast') {
    return <ButtonLink variant="primary" className="flex-1" href="/app/pricing?plan=BLAST">{t('funnel.other.buy')}</ButtonLink>;
  }
  if (tier === 'tripwire') {
    return <Button variant="primary" className="flex-1" loading={pending} onClick={on.onBuyTripwire}>{t('funnel.other.buy')}</Button>;
  }
  if (tier === 'free') {
    return <Button variant="primary" className="flex-1" onClick={on.onGenerate}>{t('funnel.other.generate')}</Button>;
  }
  return <Button variant="primary" className="flex-1" onClick={on.onClose}>{t('funnel.other.ok')}</Button>;
}
