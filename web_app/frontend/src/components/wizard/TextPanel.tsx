import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
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
export function TextPanel({ canContinue, highlight, loading, timingReady, onNext }: {
  canContinue: boolean;
  highlight?: boolean;
  loading?: boolean;
  /** тайминг «от/до» заполнен — до этого поле текста закрыто */
  timingReady: boolean;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  const lyrics = useWizardStore((state) => state.lyrics);
  const setField = useWizardStore((state) => state.setField);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  // как только отрывок задан — сразу ставим курсор в поле, чтобы не искать, куда писать
  useEffect(() => {
    if (timingReady && !lyrics.trim()) areaRef.current?.focus();
  }, [timingReady, lyrics]);

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

      <div className={cn('relative min-h-0 flex-1 overflow-hidden', highlight ? 'dash-panel' : 'dash-panel-white')}>
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
              placeholder={t('wizard.text.placeholder')}
              spellCheck={false}
              className="subtle-scroll h-full w-full resize-none bg-transparent p-space-5 pb-[64px] text-[15px] leading-[1.6] text-text outline-none placeholder:text-text-40 focus-visible:outline-none"
            />
            {/* счётчик строк: столько строк субтитров и уедет в ролик */}
            <p className="pointer-events-none absolute bottom-space-4 left-1/2 w-max -translate-x-1/2 rounded-r15 bg-[var(--card-2)] px-space-4 py-space-2 text-center text-[14px] text-text-60 shadow-soft">
              {lyrics.trim()
                ? t('wizard.text.lines', { count: lyrics.split('\n').filter((line) => line.trim()).length })
                : t('wizard.text.hint')}
            </p>
          </>
        )}
      </div>

      {/* W7: «Продолжить» на всю ширину со стрелкой, без кнопки «Назад» на первом шаге */}
      <div className="mt-space-5 shrink-0">
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
