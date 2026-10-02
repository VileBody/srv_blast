import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import type { FunnelState } from '../../lib/types';
import { Button, ButtonLink, GLYPH, Icon } from '../ui/kit';
import { FunnelSheet } from './FunnelSheet';

/*
 * Питч в модалке B — выжимка веток питча из бота (services/tg_bot_public/app.py:
 * высокая оценка → «поток роликов», _handle_sales_pitch → Бласт против монтажёра,
 * задача шага — продать Бласт). Должен помещаться в один экран телефона: одна фраза
 * по ответам квиза, сравнение полосками и одна строка действий — «Изучить тариф» и
 * квадрат со стрелкой к следующему шагу (бесплатный безлимит, если не купил).
 *
 * Персонализация — по ответам квиза (общие с ботом): Q3 выбирает довод
 * (время / деньги / идеи / смысл), Q2a/Q2b подставляют собственные цифры человека.
 */

type Survey = FunnelState['survey'];

const TIME_IDS = new Set(['lt_hour', '1_3h', 'half_day', 'a_lot']);
/** «Ничего не трачу» — без своей фразы: общий довод про деньги */
const MONEY_IDS = new Set(['2_5k', '5_10k', '10k_plus']);

/** Довод по ответам квиза: ключ i18n и подстановки. Без квиза — общий довод из бота. */
function personalLine(survey: Survey | undefined): { key: string; vars: Record<string, string> } {
  const answers = survey?.answers ?? {};
  const branch = survey?.branch || answers.q3?.id || '';
  // своя фраза на каждый ответ: ботовые label («Не считал, но точно много») в предложение не встают
  if (branch === 'time' && answers.q2a && TIME_IDS.has(answers.q2a.id)) return { key: `funnel.pitch.personal.timeBy.${answers.q2a.id}`, vars: {} };
  if (branch === 'money' && answers.q2b && MONEY_IDS.has(answers.q2b.id)) return { key: `funnel.pitch.personal.moneyBy.${answers.q2b.id}`, vars: {} };
  if (['time', 'money', 'ideas', 'meaning'].includes(branch)) return { key: `funnel.pitch.personal.${branch}`, vars: {} };
  if (answers.q2?.id === 'no_edit') return { key: 'funnel.pitch.personal.noEdit', vars: {} };
  return { key: 'funnel.pitch.personal.default', vars: {} };
}

export function PitchFlow({
  survey,
  progress,
  onNext,
  onClose,
  titleId
}: {
  survey?: Survey;
  progress?: { total: number; current: number };
  /** дальше — к двум шагам бесплатного безлимита */
  onNext: () => void;
  onClose: () => void;
  titleId?: string;
}) {
  const { t } = useTranslation();
  const personal = personalLine(survey);
  return (
    <FunnelSheet
      titleId={titleId}
      onClose={onClose}
      progress={progress}
      stepKey="pitch-lead"
      title={t('funnel.pitch.lead.title')}
      description={t(personal.key, personal.vars)}
      actions={
        <>
          <ButtonLink variant="primary" className="flex-1" href="/app/pricing?plan=BLAST">{t('funnel.pitch.study')}</ButtonLink>
          <Button variant="secondary" iconOnly aria-label={t('funnel.pitch.skipToFree')} title={t('funnel.pitch.skipToFree')} onClick={onNext}>
            <Icon>{GLYPH.arrowRight}</Icon>
          </Button>
        </>
      }
    >
      {/* Бласт против монтажёра на те же деньги: 100 роликов против ~7 */}
      <div className="rounded-r15 bg-panel p-[16px]">
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
    </FunnelSheet>
  );
}
