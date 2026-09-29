import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { cn } from '../../lib/cn';
import { useWizardStore } from '../../stores/wizardStore';
import { Surface } from '../ui/kit';
import { formatClock } from './timing';
import { timingToSeconds } from './useFragmentAudio';
import { AsideCard, WizardActions } from './WizardFrame';
import { useLyricsUndo, useTried } from './wizardAttempt';

/*
 * Правая колонка шага «Трек»: текст ВЫБРАННОГО ОТРЫВКА — только строки, которые в нём
 * звучат (30 секунд работы, зато синхронизация гарантирована).
 *
 * Поле открывается после того, как выделен отрывок: текст относится к конкретному окну.
 * Сдвиг окна текст очищает (рассинхрон строк и звука для lyric-video недопустим), но
 * с «Вернуть» — он возвращает и строки, и прежнее окно. Номера строк слева — проще сверять
 * с тем, как поётся.
 */
export function TextPanel({ ready, loading, timingReady, timingToComplete, onNext }: {
  ready: boolean;
  loading?: boolean;
  /** отрывок выделен и укладывается в лимит — до этого поле закрыто */
  timingReady: boolean;
  /** Окно задано целиком: можно автоматически показать следующий гайд. */
  timingToComplete: boolean;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  const track = useWizardStore((state) => state.track);
  const lyrics = useWizardStore((state) => state.lyrics);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const setField = useWizardStore((state) => state.setField);
  const undo = useLyricsUndo((state) => state.saved);
  const tried = useTried(1);
  const gutterRef = useRef<HTMLDivElement>(null);
  const guideTargetRef = useRef<HTMLDivElement>(null);
  const [textGuideRequested, setTextGuideRequested] = useState(false);
  // Шаг 1 из 2 этой страницы — «Выбери отрывок» (TrackStage); ждём, когда его ЗАКРОЮТ,
  // иначе при уже настроенном треке оба гайда всплывают разом.
  const trackTimingDismissed = useGuideLiveDismissed('track-timing');
  const [guideDismissed, setGuideDismissed] = useGuideDismiss(
    'text-lyrics',
    timingReady && (timingToComplete || textGuideRequested) && !lyrics.trim(),
    timingReady && trackTimingDismissed
  );

  const lineCount = lyrics.split('\n').filter((line) => line.trim()).length;
  const rows = Math.max(1, lyrics.split('\n').length);
  const from = timingToSeconds(timingFrom);
  const to = timingToSeconds(timingTo);
  const restore = () => {
    if (!undo) return;
    setField('timingFrom', undo.timingFrom);
    setField('timingTo', undo.timingTo);
    setField('lyrics', undo.lyrics);
    setField('fragmentLyrics', undo.fragmentLyrics);
    setField('fragmentEnabled', undo.fragmentEnabled);
    useLyricsUndo.getState().drop();
  };

  return (
    <AsideCard className="wizard-aside flex-1 max-lg:w-full">
      <div className="flex items-baseline justify-between gap-[12px]">
        <h2 className="text-ui-20 font-[400] text-text">{t('wizard.text.title')}</h2>
        {timingReady && from !== null && to !== null && <span className="whitespace-nowrap text-ui-14 tabular-nums text-text-40">{formatClock(from)} – {formatClock(to)}</span>}
      </div>

      <Surface
        ref={guideTargetRef}
        className={cn(
          'relative min-h-[180px] flex-1 overflow-hidden transition-[border-color] duration-150 focus-within:border-accent-line',
          tried && timingReady && !lineCount && 'border-warning'
        )}
      >
        {!timingReady ? (
          // Закрыто: объясняем, чего ждём, а не показываем мёртвое поле
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-[14px] p-[24px] text-center">
            <div className="flex w-[70%] flex-col gap-[10px] opacity-50" aria-hidden="true">
              {[86, 64, 74, 48].map((w) => <i key={w} className="h-[6px] rounded-full bg-line-strong" style={{ width: `${w}%` }} />)}
            </div>
            <p className="max-w-[260px] text-ui-14 text-text-60">{t(track ? 'wizard.text.lockedCut' : 'wizard.text.lockedTrack')}</p>
          </div>
        ) : (
          <div className="absolute inset-0 flex max-md:static">
            <div ref={gutterRef} aria-hidden="true" className="w-[40px] shrink-0 overflow-hidden border-r border-line py-[14px] pr-[10px] text-right text-ui-12 tabular-nums leading-[26px] text-text-40">
              {Array.from({ length: rows }, (_, i) => <div key={i}>{i + 1}</div>)}
            </div>
            <textarea
              value={lyrics}
              onChange={(event) => { setField('lyrics', event.target.value); if (event.target.value.trim()) useLyricsUndo.getState().drop(); }}
              onFocus={() => setTextGuideRequested(true)}
              onScroll={(event) => { if (gutterRef.current) gutterRef.current.scrollTop = event.currentTarget.scrollTop; }}
              placeholder={t('wizard.text.placeholder')}
              spellCheck={false}
              rows={Math.max(6, rows + 1)}
              aria-label={t('wizard.text.title')}
              className="subtle-scroll min-w-0 flex-1 resize-none bg-transparent p-[14px] text-ui-16 leading-[26px] text-text caret-accent-light outline-none placeholder:text-text-40"
            />
          </div>
        )}
      </Surface>

      {undo && (
        <div className="flex items-center gap-[10px] rounded-r10 bg-warning-bg px-[12px] py-[10px] text-ui-14 text-warning">
          <span className="flex-1">{t('wizard.text.undoText')}</span>
          <button type="button" onClick={restore} className="text-text underline underline-offset-[3px]">{t('wizard.text.undo')}</button>
        </div>
      )}

      <p className="min-h-[20px] text-ui-14 text-text-40">
        {timingReady && (lineCount
          ? <><span className="text-text-60">{t('wizard.text.lines', { count: lineCount })}</span> · {t('wizard.text.hint')}</>
          : t('wizard.text.hint'))}
      </p>

      <ActionGuideOverlay
        open={timingReady && trackTimingDismissed && !guideDismissed}
        targetRef={guideTargetRef}
        title={t('wizard.text.guideTitle')}
        text={t('wizard.text.guideText')}
        dismissLabel={t('wizard.text.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={(
          <div className="flex w-full flex-col gap-[8px]" aria-hidden="true">
            <span className="h-[5px] w-[88%] rounded-full bg-text-60" />
            <span className="flex items-center gap-[5px]">
              <span className="h-[5px] w-[66%] rounded-full bg-accent-light" />
              <span className="guide-cursor-blink h-[18px] w-[2px] rounded-full bg-text" />
            </span>
            <span className="h-[5px] w-[48%] rounded-full bg-text-40" />
          </div>
        )}
      />

      {/* первый шаг: «Продолжить» на всю ширину, без «Назад» */}
      <WizardActions ready={ready} loading={loading} onNext={onNext} />
    </AsideCard>
  );
}
