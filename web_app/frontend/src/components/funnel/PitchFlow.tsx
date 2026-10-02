import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import type { FunnelRules, FunnelState } from '../../lib/types';
import { Button, ButtonLink, GLYPH, Icon, Pill } from '../ui/kit';
import { FunnelSheet } from './FunnelSheet';
import { PitchLadder } from './parts';

/*
 * Питч в модалке B — перенос веток питча из бота (services/tg_bot_public/app.py:
 * высокая оценка → «поток роликов», _handle_sales_pitch → Бласт против монтажёра,
 * _handle_packages_offer → «Подробнее», _handle_why_not → возражения и кейсы).
 *
 * Персонализация — по ответам квиза (общие с ботом): Q3 выбирает главный довод
 * (время / деньги / идеи / смысл), Q2a/Q2b подставляют собственные цифры человека.
 * Приглашение друга из бота сюда не переносим: выход из питча — бесплатный безлимит.
 */

export type PitchScreen = 'lead' | 'details' | 'whyNot' | 'reason';
export type PitchReason = 'noRelease' | 'noMoney' | 'quality' | 'doubt';
export const PITCH_REASONS: PitchReason[] = ['noRelease', 'noMoney', 'quality', 'doubt'];

type Survey = FunnelState['survey'];

/** пункты списков питча в i18n: points.p1…p3 (массивы проверка переводов не понимает) */
const POINTS = ['p1', 'p2', 'p3'] as const;

/** Довод по ответам квиза: ключ i18n и подстановки. Без квиза — общий довод из бота. */
function personalLine(survey: Survey | undefined): { key: string; vars: Record<string, string> } {
  const answers = survey?.answers ?? {};
  const branch = survey?.branch || answers.q3?.id || '';
  if (branch === 'time' && answers.q2a) return { key: 'funnel.pitch.personal.timeSpent', vars: { spent: answers.q2a.label.toLowerCase() } };
  if (branch === 'money' && answers.q2b) return { key: 'funnel.pitch.personal.moneySpent', vars: { spent: answers.q2b.label } };
  if (['time', 'money', 'ideas', 'meaning'].includes(branch)) return { key: `funnel.pitch.personal.${branch}`, vars: {} };
  if (answers.q2?.id === 'no_edit') return { key: 'funnel.pitch.personal.noEdit', vars: {} };
  return { key: 'funnel.pitch.personal.default', vars: {} };
}

export function PitchFlow({
  screen,
  reason,
  survey,
  rules,
  trackTitle,
  progress,
  channelLink,
  onScreen,
  onReason,
  onUnlock,
  onClose,
  titleId
}: {
  screen: PitchScreen;
  reason?: PitchReason | null;
  survey?: Survey;
  rules: FunnelRules;
  trackTitle: string;
  progress?: { total: number; current: number };
  channelLink?: string;
  onScreen: (screen: PitchScreen) => void;
  onReason: (reason: PitchReason) => void;
  /** дальше к двум шагам бесплатного безлимита */
  onUnlock: () => void;
  onClose: () => void;
  titleId?: string;
}) {
  const { t } = useTranslation();
  const common = { titleId, onClose, progress };
  const unlock = (
    <Button variant="primary" onClick={onUnlock}>{t('funnel.pitch.cta')}</Button>
  );

  if (screen === 'details') {
    return (
      <FunnelSheet
        {...common}
        stepKey="pitch-details"
        title={t('funnel.pitch.details.title')}
        description={t('funnel.pitch.details.description')}
        actions={
          <>
            <ButtonLink variant="secondary" href="/app/pricing?plan=BLAST">{t('funnel.pitch.buyBlast')}</ButtonLink>
            {unlock}
          </>
        }
      >
        <div className="grid grid-cols-2 gap-[12px] max-md:grid-cols-1">
          {(['now', 'later'] as const).map((col) => (
            <section key={col} className="rounded-r15 bg-panel p-[16px]">
              <h3 className="text-ui-16 text-text">{t(`funnel.pitch.details.${col}Title`)}</h3>
              <ul className="mt-[10px] flex flex-col gap-[8px]">
                {POINTS.map((k) => t(`funnel.pitch.details.${col}.${k}`)).map((item) => (
                  <li key={item} className="flex gap-[8px] text-ui-14 text-text-80">
                    <span className="mt-[4px] text-ui-12 text-accent-light"><Icon>{GLYPH.check}</Icon></span>
                    <span className="[text-wrap:pretty]">{item}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
        <div className="mt-[16px]">
          <PitchLadder rules={rules} />
        </div>
        <ButtonLink variant="ghost" size="sm" className="mt-[8px]" href="/app/pricing" iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}>
          {t('funnel.pitch.allPlans')}
        </ButtonLink>
      </FunnelSheet>
    );
  }

  if (screen === 'whyNot' || screen === 'reason') {
    const picked = screen === 'reason' ? reason : null;
    const cases = picked === 'quality' || picked === 'doubt';
    return (
      <FunnelSheet
        {...common}
        stepKey={picked ? `pitch-reason-${picked}` : 'pitch-why'}
        title={t('funnel.pitch.whyNot.title')}
        description={picked ? undefined : t('funnel.pitch.whyNot.description')}
        actions={picked ? unlock : <Button variant="ghost" onClick={() => onScreen('lead')}>{t('common.back')}</Button>}
      >
        <div className="flex flex-wrap gap-[8px]" role="radiogroup" aria-label={t('funnel.pitch.whyNot.title')}>
          {PITCH_REASONS.map((id) => (
            <Pill key={id} pressed={picked === id} role="radio" aria-checked={picked === id} onClick={() => onReason(id)}>
              {t(`funnel.pitch.whyNot.reason.${id}`)}
            </Pill>
          ))}
        </div>
        {picked && (
          <div className="fn-step mt-[16px] rounded-r15 bg-panel p-[16px]">
            <p className="text-ui-16 text-text [text-wrap:pretty]">{t(`funnel.pitch.whyNot.answer.${cases ? 'cases' : 'later'}`)}</p>
            <p className="mt-[8px] text-ui-14 text-text-60 [text-wrap:pretty]">{t(`funnel.pitch.whyNot.answer.${cases ? 'casesTail' : 'laterTail'}`, { track: trackTitle })}</p>
            {cases && channelLink && (
              <ButtonLink variant="ghost" size="sm" className="mt-[10px]" href={channelLink} target="_blank" rel="noreferrer" iconEnd={<Icon>{GLYPH.external}</Icon>}>
                {t('funnel.pitch.whyNot.casesLink')}
              </ButtonLink>
            )}
          </div>
        )}
      </FunnelSheet>
    );
  }

  const personal = personalLine(survey);
  return (
    <FunnelSheet
      {...common}
      stepKey="pitch-lead"
      title={t('funnel.pitch.lead.title')}
      description={t('funnel.pitch.lead.description')}
      actions={
        <>
          <Button variant="secondary" onClick={() => onScreen('details')}>{t('funnel.pitch.more')}</Button>
          {unlock}
        </>
      }
    >
      <p className="rounded-r15 border border-accent-line bg-accent-soft px-[16px] py-[14px] text-ui-16 text-text [text-wrap:pretty]">
        {t(personal.key, personal.vars)}
      </p>
      {/* Бласт против монтажёра на те же деньги: 100 роликов против ~7 */}
      <div className="mt-[16px] rounded-r15 bg-panel p-[16px]">
        <p className="text-ui-14 text-text-60">{t('funnel.pitch.lead.compareTitle')}</p>
        {([['blast', 100], ['editor', 7]] as const).map(([who, n]) => (
          <div key={who} className="mt-[12px]">
            <div className="flex items-baseline justify-between gap-[12px] text-ui-14">
              <span className={who === 'blast' ? 'text-text' : 'text-text-60'}>{t(`funnel.pitch.lead.${who}`)}</span>
              <span className={cn('tabular-nums', who === 'blast' ? 'text-accent-light' : 'text-text-60')}>{t(`funnel.pitch.lead.${who}Count`)}</span>
            </div>
            <div className="mt-[6px] h-[6px] overflow-hidden rounded-full bg-field" aria-hidden="true">
              <div className={cn('fn-meter-fill h-full rounded-full', who === 'blast' ? 'bg-accent-light' : 'bg-text-40')} style={{ width: `${n}%` }} />
            </div>
          </div>
        ))}
      </div>
      <ul className="mt-[16px] flex flex-col gap-[8px]">
        {POINTS.map((k) => t(`funnel.pitch.lead.points.${k}`)).map((point) => (
          <li key={point} className="flex gap-[8px] text-ui-14 text-text-80">
            <span className="mt-[4px] text-ui-12 text-accent-light"><Icon>{GLYPH.check}</Icon></span>
            <span className="[text-wrap:pretty]">{point}</span>
          </li>
        ))}
      </ul>
      <Button variant="ghost" size="sm" className="mt-[12px]" onClick={() => onScreen('whyNot')}>{t('funnel.pitch.notNow')}</Button>
    </FunnelSheet>
  );
}
