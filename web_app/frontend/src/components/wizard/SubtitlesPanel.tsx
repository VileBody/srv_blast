import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { useDragScroll } from './BackgroundPanel';
import { PillsFooter } from './WizardFrame';
import { useWizardStore } from '../../stores/wizardStore';
import { SubtitleTimeline } from './SubtitleTimeline';
import { CatalogMedia, SubtitleCatalogPreview } from './CatalogPreview';
import { FigIcon } from '../ui/FigIcon';
import { InlineError, queryDown } from '../ui/ErrorState';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';

/**
 * Мини-визуал подгонки субтитров: два отдельных слова-пилюли, каждое показывает
 * СВОЮ механику — не общий шов между ними.
 * Слово A («Я»): всегда одето в пилюлю, едет ТОЛЬКО по X (без морфинга) — тянешь
 * слово по дорожке — а затем сама пилюля на миг вспыхивает акцентным цветом и
 * гаснет («тапнул — выбрал»). Слово B («знаю») стоит на месте, но его пилюля
 * растёт вширь (scaleX от левого края, с обратным контрскейлом подписи внутри,
 * чтобы текст не плющило) — «длина слова увеличивается».
 */
function SubtitleFitGuideVisual() {
  return (
    <div className="flex w-full items-center justify-center gap-[10px]" aria-hidden="true">
      <span className="guide-fit-drag relative flex h-[32px] w-[52px] shrink-0 items-center justify-center overflow-hidden rounded-[7px] bg-white/15 text-[11px] leading-none text-white">
        <span className="guide-fit-select absolute inset-0 rounded-[7px] bg-accent-light" aria-hidden="true" />
        <span className="relative z-[1]">Я</span>
      </span>
      <span className="guide-fit-grow flex h-[32px] w-[66px] shrink-0 items-center justify-center overflow-hidden rounded-[7px] bg-white/15 text-[11px] leading-none text-white/70">
        <span className="guide-fit-grow-label">знаю</span>
      </span>
    </div>
  );
}

/** Мини-визуал первой подсказки субтитров: свотч + цветовая шкала, как в самой панели. */
function SubtitleColorGuideVisual() {
  return (
    <div className="flex w-full items-center gap-[8px]" aria-hidden="true">
      <span className="guide-track-piece guide-mode-delay-1 h-[34px] w-[34px] shrink-0 rounded-r9 bg-[#f6f5fd] shadow-[0_0_0_2px_var(--accent-light)]" />
      <span className="guide-track-piece guide-mode-delay-2 h-[34px] flex-1 rounded-r9" style={{ background: 'linear-gradient(90deg,#ff5c5c,#ffd15c,#5cff8f,#5ccbff,#a55cff,#ff5cc9)' }} />
    </div>
  );
}

/** Мини-визуал второй подсказки субтитров: карточки стилей, одна выбрана. */
function SubtitleStyleGuideVisual() {
  return (
    <div className="grid w-full grid-cols-3 gap-[7px]" aria-hidden="true">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className={cn(
            'guide-mode-reveal relative flex h-[46px] items-center justify-center overflow-hidden rounded-[8px] bg-gradient-to-b from-[#42335e] to-[#181126]',
            index === 0 ? 'guide-mode-delay-1' : index === 1 ? 'guide-mode-delay-2' : 'guide-mode-delay-3',
            index === 1 && 'ring-2 ring-inset ring-accent-light'
          )}
        >
          <em className="font-bold italic text-[16px] leading-none text-white/85">T</em>
        </span>
      ))}
    </div>
  );
}

/*
 * Этап «Текст» (Figma W16 → 17 → 23): стили субтитров включаются/выключаются
 * кликом по карточке, футер — живое отражение выбранного. Белый свотч — дефолтный
 * цвет; выбор с полосы показывает точку, клик по свотчу возвращает белый.
 */


function StyleCard({ name, previewUrl, inPool, onClick }: { name: string; previewUrl: string; inPool: boolean; onClick: () => void }) {

  return (
    <button type="button" onClick={onClick} aria-pressed={inPool} className="media-card h-full" style={{ aspectRatio: '4 / 3' }}>
      <CatalogMedia url={previewUrl} className="absolute inset-0 h-full w-full" />
      <span className="absolute bottom-2 left-0 right-0 z-[2] text-center text-sm text-text" style={{textShadow: '0 1px 4px black'}}>{name}</span>
      {/* Выбор показывает только обводка — галочку с примеров убрали (как у фото и футажа):
          она закрывала кадр и дублировала и без того заметную рамку. */}
      {inPool && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[3] rounded-r10" style={{ boxShadow: 'inset 0 0 0 2px var(--accent-light)' }} />
      )}
    </button>
  );
}

export function StageSubtitles() {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const toggleStyle = useWizardStore((state) => state.toggleSubtitleStyle);
  const stylesQuery = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  const cardsScroll = useDragScroll();
  const barRef = useRef<HTMLDivElement>(null);
  const [huePct, setHuePct] = useState(50);
  const timelineGuideTargetRef = useRef<HTMLDivElement>(null);
  const colorGuideTargetRef = useRef<HTMLDivElement>(null);
  const stylesGuideTargetRef = useRef<HTMLDivElement>(null);
  const hasStyles = subtitles.pool.length > 0;
  // Хуки объявлены от ПОСЛЕДНЕГО шага цепочки к первому: idle-условие шага N
  // требует dismissed-значения шага N+1 («мы ещё не ушли дальше»), поэтому оно
  // должно быть уже посчитано на момент объявления хука для шага N.
  // Ни у одного из трёх шагов нет жёсткого пререквизита, кроме своего места в
  // цепочке (таргеты всегда отрисованы) — visible = «предыдущие шаги уже
  // пройдены», БЕЗ учёта того, выбран ли уже стиль: принудительный тур
  // проходит все три по очереди, даже если стиль субтитров уже выбран.
  // ВАЖНО: visible не может быть просто true для 2-го/3-го шага — иначе
  // seen записался бы в момент маунта, раньше, чем юзер реально дошёл до
  // этого шага цепочки.
  const [stylesGuideDismissed, setStylesGuideDismissed] = useGuideDismiss('subtitles-styles', !hasStyles, false);
  const [colorGuideDismissed, setColorGuideDismissed] = useGuideDismiss('subtitles-color', !hasStyles && !stylesGuideDismissed, false);
  const [timelineGuideDismissed, setTimelineGuideDismissed] = useGuideDismiss('subtitles-timeline', !hasStyles && !colorGuideDismissed, true);
  const showTimelineGuide = !timelineGuideDismissed;
  const showColorGuide = timelineGuideDismissed && !colorGuideDismissed;
  const showStylesGuide = timelineGuideDismissed && colorGuideDismissed && !stylesGuideDismissed;
  useMarkGuideSeen('subtitles-color', timelineGuideDismissed);
  useMarkGuideSeen('subtitles-styles', timelineGuideDismissed && colorGuideDismissed);

  useScrollGuideIntoView(showTimelineGuide, timelineGuideTargetRef);
  useScrollGuideIntoView(showColorGuide, colorGuideTargetRef);
  useScrollGuideIntoView(showStylesGuide, stylesGuideTargetRef);

  const pickFromBar = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    const rect = barRef.current.getBoundingClientRect();
    const pct = Math.min(100, Math.max(0, ((e.clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    setSubtitles({ color: hueAt(pct) });
  };
  const onBarDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    try {
      barRef.current.setPointerCapture(e.pointerId);
    } catch {
      /* синтетический pointerId */
    }
    pickFromBar(e);
    barRef.current.onpointermove = ((ev: PointerEvent) => {
      if (ev.buttons) pickFromBar(ev as unknown as ReactPointerEvent<HTMLDivElement>);
    }) as never;
    barRef.current.onpointerup = () => {
      if (barRef.current) { barRef.current.onpointermove = null; barRef.current.onpointerup = null; }
    };
  };

  const isWhite = subtitles.color === '#f6f5fd';

  return (
    <div className="flex h-full flex-col">
      <h2 className="wizard-h flex items-center gap-space-3">
        <em className="inline-block bg-grad-text bg-clip-text pb-[3px] pr-[3px] font-bold italic text-[30px] leading-[1.15] text-transparent">Т</em>
        {t('wizard.subs.title')}
      </h2>

      {/* Примерка: как ASR разложил слова по треку — подвинуть/пометить фокус ДО выбора стиля */}
      <div ref={timelineGuideTargetRef}>
        <SubtitleTimeline />
      </div>

      <ActionGuideOverlay
        open={showTimelineGuide}
        targetRef={timelineGuideTargetRef}
        title={t('wizard.subs.guideFitTitle')}
        text={t('wizard.subs.guideFitText')}
        dismissLabel={t('wizard.subs.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 3 })}
        onDismiss={() => setTimelineGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleFitGuideVisual />}
      />

      {/* Белый свотч — дефолт; обводка на нём = включён белый. Клик возвращает белый. */}
      {/* Цвет: одинаковый отступ от проверки субтитров сверху и до типов снизу */}
      <div ref={colorGuideTargetRef} className="mt-[40px] flex items-center gap-[28px] max-md:mt-[16px] max-md:gap-[12px]">
        <button
          type="button"
          aria-label={t('wizard.subs.whiteColor')}
          className="h-[60px] w-[60px] shrink-0 rounded-r15 bg-[#f6f5fd] transition max-md:h-[40px] max-md:w-[40px] max-md:rounded-r10"
          style={{ boxShadow: isWhite ? '0 0 0 2px var(--accent-light)' : undefined }}
          onClick={() => setSubtitles({ color: '#f6f5fd' })}
        />
        <div
          ref={barRef}
          className="color-slider h-[60px] flex-1 max-md:h-[40px] max-md:touch-none"
          style={{ background: HUE_GRADIENT }}
          onPointerDown={onBarDown}
          role="slider"
          aria-label={t('wizard.subs.color')}
          aria-valuenow={Math.round(huePct)}
        >
          {!isWhite && <span className="color-slider-thumb" style={{ left: `${huePct}%` }} />}
        </div>
      </div>

      <ActionGuideOverlay
        open={showColorGuide}
        targetRef={colorGuideTargetRef}
        title={t('wizard.subs.guideColorTitle')}
        text={t('wizard.subs.guideColorText')}
        dismissLabel={t('wizard.subs.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 3 })}
        onDismiss={() => setColorGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleColorGuideVisual />}
      />

      <div ref={stylesGuideTargetRef} className="relative mt-[40px] flex min-h-[382px] w-full flex-1 flex-col overflow-hidden rounded-r15 bg-grad-soft-10 pb-[40px] pt-[40px] max-md:mt-[16px] max-md:min-h-0 max-md:flex-none max-md:pb-[14px] max-md:pt-[14px]">
        <div className="px-[40px] max-md:px-[14px]">
          <span className="wizard-body">{t('wizard.subs.chooseType')}</span>
        </div>
        {stylesQuery.isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <span className="spinner !h-[48px] !w-[48px] !border-[3px] !border-accent-20 !border-t-accent-light" />
          </div>
        ) : queryDown(stylesQuery) ? (
          /* список стилей не пришёл — раньше здесь оставалась пустая полоса без объяснения */
          <div className="flex flex-1 items-center justify-center">
            <InlineError error={stylesQuery.error} offline={stylesQuery.fetchStatus === 'paused'} onRetry={() => stylesQuery.refetch()} retrying={stylesQuery.isFetching} />
          </div>
        ) : (
          <div className="relative mt-[12px] min-h-[253px] flex-1 max-md:h-[180px] max-md:min-h-0 max-md:flex-none">
            <span className="scroll-fade-l" />
            <span className="scroll-fade-r" />
            <div
              ref={cardsScroll.ref}
              className="media-row cursor-grab select-none items-stretch gap-[20px] px-[40px] active:cursor-grabbing max-md:gap-[10px] max-md:px-[14px]"
              {...cardsScroll.handlers}
            >
              {stylesQuery.data?.styles.map((item) => (
                <StyleCard
                  key={item.id}
                  name={item.name}
                  previewUrl={item.previewUrl}
                  inPool={subtitles.pool.includes(item.name)}
                  onClick={() => { if (!cardsScroll.moved()) toggleStyle(item.name); }}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <ActionGuideOverlay
        open={showStylesGuide}
        targetRef={stylesGuideTargetRef}
        title={t('wizard.subs.guideStyleTitle')}
        text={t('wizard.subs.guideStyleText')}
        dismissLabel={t('wizard.subs.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 3, total: 3 })}
        onDismiss={() => setStylesGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleStyleGuideVisual />}
      />
    </div>
  );
}

export function SubtitlesWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const lyrics = useWizardStore((state) => state.lyrics);
  const fragmentLyrics = useWizardStore((state) => state.fragmentLyrics);
  const pillsScroll = useDragScroll();
  const [previewName, setPreviewName] = useState<string | undefined>(undefined);

  const currentName = previewName && subtitles.pool.includes(previewName) ? previewName : subtitles.pool[subtitles.pool.length - 1];
  const previewLyrics = (fragmentLyrics.trim() || lyrics.trim()) || t('wizard.subs.lyricsPlaceholder');

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        <h2 className="wizard-h mb-space-5 shrink-0 whitespace-nowrap">{t('wizard.workZone')}</h2>
        {/* телефон: у зоны своя высота (9:16) — иначе в авто-колонке она схлопывается вместе с даш-рамкой */}
        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10 max-md:aspect-[9/16] max-md:w-full">
          {currentName && <SubtitleCatalogPreview className="absolute inset-0" name={currentName} />}
          <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          {!currentName && (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[223px] text-center">{t('wizard.subs.empty')}</p>
            </div>
          )}
        </div>
      </div>

      <PillsFooter
        pills={subtitles.pool.map((name) => ({
          key: name,
          label: name,
          icon: <em className="font-bold italic text-[24px] leading-none text-text-80">T</em>
        }))}
        activeKey={undefined}
        emptyLabel={t('wizard.subs.footerEmpty')}
        onPill={(key) => setPreviewName(key)}
        onPlus={undefined}
        ready={ready}
        canContinue={canContinue}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
        dragScroll={pillsScroll}
      />
    </aside>
  );
}
