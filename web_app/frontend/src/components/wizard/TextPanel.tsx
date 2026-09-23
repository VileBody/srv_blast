import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActionGuideOverlay, type ActionGuideVariant } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { cn } from '../../lib/cn';
import { useWizardStore } from '../../stores/wizardStore';

/*
 * Правая панель этапа «Трек»: текст ВЫБРАННОГО ОТРЫВКА.
 *
 * Раньше человек вставлял текст трека целиком и выделял припев мышью. Это долго и всё равно
 * не давало точной привязки к таймингу. Теперь он вписывает только те строки, которые звучат
 * в отрывке, — 30 секунд работы, зато синхронизация гарантирована.
 *
 * Поле открывается ТОЛЬКО после того, как задан тайминг: текст относится к конкретному
 * отрывку, и вводить его раньше бессмысленно. Смена тайминга очищает текст (см. StageOne) —
 * рассинхрон строк и звука для lyric-video недопустим.
 */
export function TextPanel({ canContinue, guideVariant = 'visual', highlight, loading, timingReady, timingToComplete, onNext }: {
  canContinue: boolean;
  guideVariant?: ActionGuideVariant;
  highlight?: boolean;
  loading?: boolean;
  /** тайминг «от/до» заполнен — до этого поле текста закрыто */
  timingReady: boolean;
  /** Во втором тайминге заполнены миллисекунды: можно автоматически показать следующий гайд. */
  timingToComplete: boolean;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  const lyrics = useWizardStore((state) => state.lyrics);
  const setField = useWizardStore((state) => state.setField);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const guideTargetRef = useRef<HTMLDivElement>(null);
  const [textGuideRequested, setTextGuideRequested] = useState(false);
  // Гайд «Трек» (track-timing, StageOne) — шаг 1 из 2 этой же страницы, этот —
  // шаг 2. Оба хард-пререквизита (timingReady) достижимы ОДНОВРЕМЕННО, если
  // трек уже полностью настроен, поэтому одного timingReady мало — ждём,
  // когда шаг 1 ЗАКРОЮТ (живой сигнал, не «когда-либо видел» — иначе оба
  // гайда всплывают разом на одном экране).
  const trackTimingDismissed = useGuideLiveDismissed('track-timing');
  const [guideDismissed, setGuideDismissed] = useGuideDismiss(
    'text-lyrics',
    timingReady && (timingToComplete || textGuideRequested) && !lyrics.trim(),
    // visible: поле текста разлочено тем же timingReady — остальное (триггер
    // показа/то, что текст ещё не вписан) не мешает разовому принудительному туру.
    timingReady && trackTimingDismissed
  );

  const title = !timingReady
    ? t('wizard.text.titleLocked')
    : lyrics.trim()
      ? t('wizard.text.titleFilled')
      : t('wizard.text.titleEmpty');

  return (
    <aside className="card-2 wizard-aside flex min-h-0 shrink-0 flex-col px-space-7 py-space-6 max-lg:w-full max-lg:px-space-5">
      {/* key={title}: при смене подсказки заголовок перезаезжает с анимацией */}
      <h2 key={title} className="wizard-h mb-space-5 shrink-0" style={{ animation: 'slideUpFade var(--t-slow) both' }}>
        {title}
      </h2>

      {/* телефон: поле растёт вместе с текстом (rows), а не скроллится внутри узкой рамки */}
      <div ref={guideTargetRef} className={cn('relative min-h-0 flex-1 overflow-hidden', highlight ? 'dash-panel' : 'dash-panel-white')}>
        {!timingReady ? (
          // Заблокировано: объясняем, чего ждём, а не показываем мёртвое поле
          <div className="flex h-full flex-col items-center justify-center gap-space-4 p-space-5 text-center">
            <span className="flex h-[48px] w-[48px] items-center justify-center rounded-full bg-grad-soft-20 opacity-60">
              <img src="/assets/figma/icon-bolt.svg" width="13" height="20" alt="" aria-hidden="true" />
            </span>
            <p className="wizard-body max-w-[263px]">{t('wizard.text.lockedHint')}</p>
          </div>
        ) : (
          <>
            <textarea
              ref={areaRef}
              value={lyrics}
              onChange={(event) => setField('lyrics', event.target.value)}
              onFocus={() => setTextGuideRequested(true)}
              placeholder={t('wizard.text.placeholder')}
              spellCheck={false}
              rows={Math.max(4, lyrics.split('\n').length + 1)}
              className="subtle-scroll h-full w-full resize-none bg-transparent p-space-5 pb-[64px] text-[15px] leading-[1.6] text-text outline-none placeholder:text-text-40 focus-visible:outline-none max-md:h-auto max-md:p-[12px] max-md:pb-[12px] max-md:text-[14px]"
            />
            {/* счётчик строк: столько строк субтитров и уедет в ролик */}
            <p className="pointer-events-none absolute bottom-space-4 left-1/2 w-max -translate-x-1/2 rounded-r15 bg-[var(--card-2)] px-space-4 py-space-2 text-center text-[14px] text-text-60 shadow-soft max-md:hidden">
              {lyrics.trim()
                ? t('wizard.text.lines', { count: lyrics.split('\n').filter((line) => line.trim()).length })
                : t('wizard.text.hint')}
            </p>
          </>
        )}
      </div>

      <ActionGuideOverlay
        open={timingReady && trackTimingDismissed && !guideDismissed}
        targetRef={guideTargetRef}
        title={t('wizard.text.guideTitle')}
        text={t('wizard.text.guideText')}
        dismissLabel={t('wizard.text.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setGuideDismissed(true)}
        variant={guideVariant}
        shell="track-top"
        visual={(
          <div className="flex w-full flex-col gap-[8px]" aria-hidden="true">
            <span className="h-[5px] w-[88%] rounded-full bg-text-60" />
            <span className="flex items-center gap-[5px]">
              <span className="h-[5px] w-[66%] rounded-full bg-accent-light" />
              {/* Классический мигающий текстовый курсор — недвусмысленно читается
                  как «сюда печатают», в отличие от статичной палочки. */}
              <span className="guide-cursor-blink h-[18px] w-[2px] rounded-full bg-text" />
            </span>
            <span className="h-[5px] w-[48%] rounded-full bg-text-40" />
          </div>
        )}
      />

      {timingReady && (
        /* телефон: подсказка/счётчик строк — текстом под рамкой, а не пилюлей поверх поля */
        <p className="mt-[8px] hidden text-[13px] leading-[1.4] text-text-60 max-md:block">
          {lyrics.trim()
            ? t('wizard.text.lines', { count: lyrics.split('\n').filter((line) => line.trim()).length })
            : t('wizard.text.hint')}
        </p>
      )}

      {/* W7: «Продолжить» на всю ширину со стрелкой, без кнопки «Назад» на первом шаге */}
      <div className="mt-space-5 shrink-0 max-md:mt-[12px]">
        <button
          type="button"
          disabled={!canContinue || loading}
          onClick={onNext}
          className={cn('soft-btn h-[60px] w-full gap-space-4', canContinue && 'soft-btn-ready')}
        >
          {loading ? <span className="spinner" /> : (<>
            {t('wizard.continue')}
            <svg viewBox="0 0 26 16" width="25" height="15" fill="none" aria-hidden="true">
              <path d="M1 8h22.5M17 1.5 24.5 8 17 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </>)}
        </button>
      </div>
    </aside>
  );
}
