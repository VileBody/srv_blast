import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { cn } from '../../lib/cn';
import { useWizardStore } from '../../stores/wizardStore';
import { formatTimeRange } from '../../lib/timeFormat';
import { timingToSeconds } from './useFragmentAudio';
import { WizardActions } from './WizardFrame';
import { useLyricsUndo, useTried } from './wizardAttempt';

/*
 * Правая колонка шага «Трек» (макет wizard12 v3): текст ВЫБРАННОГО ОТРЫВКА — только строки,
 * которые в нём звучат. Поле открывается, когда отрывок выделен: текст относится к окну.
 * Сдвиг окна текст очищает (рассинхрон строк и звука недопустим), но с «Вернуть» — он
 * возвращает и строки, и прежнее окно. Номера строк слева — проще сверять с тем, как поётся.
 */
export function TextPanel({ ready, loading, timingReady, timingToComplete, onNext }: {
  ready: boolean;
  loading?: boolean;
  /** отрывок выделен и укладывается в лимит — до этого поле закрыто */
  timingReady: boolean;
  /** окно задано целиком: можно автоматически показать следующий гайд */
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
  // Шаг 1 из 2 этой страницы — «Выдели отрывок» (TrackStage); ждём, когда его ЗАКРОЮТ
  const trackTimingDismissed = useGuideLiveDismissed('track-timing');
  // Подсказка «впиши текст» ждёт, пока окно отрывка УЛЯЖЕТСЯ: первая («выдели отрывок»)
  // закрывается сама на первом же выделении, и вторая выскакивала посреди протяжки по волне.
  const [timingSettled, setTimingSettled] = useState(false);
  useEffect(() => {
    setTimingSettled(false);
    if (!timingReady) return undefined;
    const timer = window.setTimeout(() => setTimingSettled(true), 1500);
    return () => window.clearTimeout(timer);
  }, [timingReady, timingFrom, timingTo]);
  const [guideDismissed, setGuideDismissed] = useGuideDismiss(
    'text-lyrics',
    timingReady && timingSettled && (timingToComplete || textGuideRequested) && !lyrics.trim(),
    timingReady && timingSettled && trackTimingDismissed
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
    <aside className="w12-col-aside">
      <div className="w12-card w12-aside">
        <div className="w12-aside-head">
          <h2>{t('wizard.text.title')}</h2>
          <span className="w12-meta w12-num">{timingReady && from !== null && to !== null ? formatTimeRange(from, to) : ''}</span>
        </div>
        <div ref={guideTargetRef} className={cn('w12-lyr', tried && timingReady && !lineCount && 'w12-invalid')}>
          {!timingReady ? (
            <div className="w12-locked">
              <div className="w12-ghost-lines" aria-hidden="true">{[86, 64, 74, 48].map((w) => <i key={w} style={{ width: `${w}%` }} />)}</div>
              <p>{t(track ? 'wizard.text.lockedCut' : 'wizard.text.lockedTrack')}</p>
            </div>
          ) : (
            <div className="w12-editor">
              <div ref={gutterRef} className="w12-gutter w12-num" aria-hidden="true">
                {Array.from({ length: rows }, (_, i) => <div key={i}>{i + 1}</div>)}
              </div>
              <textarea
                value={lyrics}
                onChange={(event) => { setField('lyrics', event.target.value); if (event.target.value.trim()) useLyricsUndo.getState().drop(); }}
                onFocus={() => setTextGuideRequested(true)}
                onScroll={(event) => { if (gutterRef.current) gutterRef.current.scrollTop = event.currentTarget.scrollTop; }}
                placeholder={t('wizard.text.placeholder')}
                spellCheck={false}
                aria-label={t('wizard.text.title')}
              />
            </div>
          )}
        </div>
        {undo && (
          <div className="w12-undo">
            <span>{t('wizard.text.undoText')}</span>
            <button type="button" onClick={restore}>{t('wizard.text.undo')}</button>
          </div>
        )}
        <p className="w12-lyr-foot">
          {timingReady && (lineCount ? <><b>{t('wizard.text.lines', { count: lineCount })}</b> · {t('wizard.text.hint')}</> : t('wizard.text.hintFull'))}
        </p>
        <WizardActions ready={ready} loading={loading} onNext={onNext} />
      </div>

      <ActionGuideOverlay
        open={timingReady && timingSettled && trackTimingDismissed && !guideDismissed}
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
    </aside>
  );
}
