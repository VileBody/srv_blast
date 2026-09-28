import { PointerEvent as ReactPointerEvent, RefObject, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { useDragScroll } from './BackgroundPanel';
import { PillsFooter } from './WizardFrame';
import { DEFAULT_SUBTITLE_TEXT_SETTINGS, SubtitleTextSettings, useWizardStore } from '../../stores/wizardStore';
import { SubtitleTimeline } from './SubtitleTimeline';
import { CatalogMedia } from './CatalogPreview';
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

function nearestHuePercent(hex: string): number {
  const target = Number.parseInt(hex.replace('#', ''), 16);
  if (!Number.isFinite(target)) return 50;
  const rgb = (value: number) => [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  const targetRgb = rgb(target);
  let best = 50;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let pct = 0; pct <= 100; pct++) {
    const candidate = Number.parseInt(hueAt(pct).slice(1), 16);
    const [r, g, b] = rgb(candidate);
    const distance = (r - targetRgb[0]) ** 2 + (g - targetRgb[1]) ** 2 + (b - targetRgb[2]) ** 2;
    if (distance < bestDistance) { best = pct; bestDistance = distance; }
  }
  return best;
}

function VisualChoice<T extends string>({ kind, label, value, options, onChange }: {
  kind: 'size' | 'height' | 'position' | 'shadow' | 'outline';
  label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void;
}) {
  return <fieldset className="flex min-w-0 items-center justify-between gap-[16px] border-0 border-b border-white/10 px-0 py-[13px] last:border-b-0">
    <legend className="sr-only">{label}</legend>
    <span className="min-w-0 text-[14px] text-text-60">{label}</span>
    <div className="flex shrink-0 gap-[4px] rounded-r10 border border-white/10 bg-black/15 p-[3px]">
      {options.map((item, index) => <button key={item.value} type="button" aria-label={`${label}: ${item.label}`} aria-pressed={value === item.value}
        onClick={() => onChange(item.value)} className={cn('flex h-[34px] w-[42px] items-center justify-center rounded-[7px] transition-colors', value === item.value ? 'bg-accent-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-40 hover:bg-white/5 hover:text-text-80')}>
        <ChoiceGlyph kind={kind} index={index} selected={value === item.value} />
      </button>)}
    </div>
  </fieldset>;
}

function ChoiceGlyph({ kind, index, selected }: { kind: 'size' | 'height' | 'position' | 'shadow' | 'outline'; index: number; selected: boolean }) {
  if (kind === 'position') {
    return <span className="relative flex h-[18px] w-[26px] items-center rounded-[4px] border border-current/55 px-[3px]"><i className={cn('h-[7px] w-[3px] rounded-full bg-current transition-all', index === 0 ? 'mr-auto' : index === 1 ? 'mx-auto' : 'ml-auto')} /></span>;
  }
  if (kind === 'size') return <span className="relative font-light leading-none" style={{ fontSize: [11, 15, 19][index], transform: `translateY(${[0, 1, 2][index]}px)` }}>A</span>;
  if (kind === 'height') return <span className="flex w-[11px] items-center justify-center rounded-[3px] border border-current/70" style={{ height: [8, 13, 18][index] }}><i className="h-[1px] w-[5px] bg-current/65" /></span>;
  if (kind === 'shadow') return <span className="relative h-[20px] w-[24px]" aria-hidden="true"><i className="absolute left-[5px] top-[3px] h-[12px] w-[12px] rounded-[3px] border border-current/90" /><i className="absolute rounded-[3px] border border-current/50" style={{ left: [5, 7, 10][index], top: [3, 5, 7][index], height: 12, width: 12, opacity: [0, .5, .8][index] }} /></span>;
  return <span className="font-normal leading-none" style={{ WebkitTextStroke: ['0 transparent', '1px currentColor', `2px ${selected ? '#f6f5fd' : 'rgba(246,245,253,.38)'}`][index], color: index === 2 ? 'transparent' : undefined }}>T</span>;
}

function SubtitleColorControl({ label, value, defaultColor, defaultLabel, onChange, guideRef }: {
  label: string; value: string; defaultColor: string; defaultLabel: string; onChange: (color: string) => void;
  guideRef?: RefObject<HTMLDivElement>;
}) {
  const [huePct, setHuePct] = useState(() => nearestHuePercent(value));
  const barRef = useRef<HTMLDivElement>(null);
  const isDefault = value.toLowerCase() === defaultColor.toLowerCase();

  useEffect(() => {
    if (!isDefault) setHuePct(nearestHuePercent(value));
  }, [isDefault, value]);

  const pickFromBar = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pct = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    onChange(hueAt(pct));
  };
  const onBarDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    try { barRef.current.setPointerCapture(event.pointerId); } catch { /* synthetic pointer */ }
    pickFromBar(event);
    barRef.current.onpointermove = ((nativeEvent: PointerEvent) => {
      if (nativeEvent.buttons) pickFromBar(nativeEvent as unknown as ReactPointerEvent<HTMLDivElement>);
    }) as never;
    barRef.current.onpointerup = () => {
      if (barRef.current) { barRef.current.onpointermove = null; barRef.current.onpointerup = null; }
    };
  };

  return <>
    <span className="text-[14px] text-text-60">{label}</span>
    <div ref={guideRef} className="flex min-w-0 items-center gap-[12px]">
      <button type="button" aria-label={defaultLabel} title={defaultLabel} className="h-[40px] w-[40px] shrink-0 rounded-r10 border border-white/25"
        style={{ backgroundColor: defaultColor, boxShadow: isDefault ? '0 0 0 2px var(--accent-light)' : undefined }} onClick={() => onChange(defaultColor)} />
      <div ref={barRef} role="slider" aria-label={label} aria-valuetext={value} title={value.toUpperCase()} aria-valuenow={Math.round(huePct)} tabIndex={0}
        aria-valuemin={0} aria-valuemax={100} className="color-slider h-[40px] flex-1 touch-none rounded-r10"
        style={{ background: HUE_GRADIENT }} onPointerDown={onBarDown}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
          event.preventDefault();
          const nextPct = Math.max(0, Math.min(100, huePct + (event.key === 'ArrowRight' ? 1 : -1)));
          setHuePct(nextPct);
          onChange(hueAt(nextPct));
        }}>
        {!isDefault && <span className="color-slider-thumb" style={{ left: `${huePct}%` }} />}
      </div>
    </div>
  </>;
}

function SubtitleTextCustomization({ colorGuideTargetRef }: { colorGuideTargetRef: RefObject<HTMLDivElement> }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const settings = { ...DEFAULT_SUBTITLE_TEXT_SETTINGS, ...(subtitles.text ?? {}) };
  const updateText = (patch: Partial<SubtitleTextSettings>) => setSubtitles({ text: { ...settings, ...patch } });
  const [fontOpen, setFontOpen] = useState(false);
  const fonts = [
    { value: 'Point-SemiBold', label: t('wizard.subs.customization.fontPointSemi'), family: 'Point, Arial, sans-serif' },
    { value: 'Point-ExtraBold', label: t('wizard.subs.customization.fontPointExtra'), family: 'Point, Arial, sans-serif' },
    { value: 'Montserrat-Bold', label: t('wizard.subs.customization.fontMontserrat'), family: 'Montserrat, Arial, sans-serif' },
    { value: 'Arial-Bold', label: t('wizard.subs.customization.fontArial'), family: 'Arial, sans-serif' },
    { value: 'Impact', label: t('wizard.subs.customization.fontImpact'), family: 'Impact, Arial Narrow, sans-serif' },
    { value: 'Georgia-Bold', label: t('wizard.subs.customization.fontGeorgia'), family: 'Georgia, serif' },
    { value: 'Trebuchet-Bold', label: t('wizard.subs.customization.fontTrebuchet'), family: 'Trebuchet MS, sans-serif' },
    { value: 'Courier-Bold', label: t('wizard.subs.customization.fontCourier'), family: 'Courier New, monospace' }
  ] as const;
  const selectedFont = fonts.find((font) => font.value === settings.font) ?? fonts[0];

  return <section className="mt-[40px] rounded-r15 bg-grad-soft-10 px-[32px] py-[28px] max-md:mt-[16px] max-md:px-[16px] max-md:py-[18px]">
    <div className="mb-[22px]">
      <h3 className="wizard-body">{t('wizard.subs.customization.title')}</h3>
      <p className="mt-[5px] text-[14px] text-text-60">{t('wizard.subs.customization.description')}</p>
    </div>
    <div className="flex flex-col">
      <div className="border-b border-white/10 py-[13px]" onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFontOpen(false);
      }}>
        <div className="flex items-center justify-between gap-[16px]">
          <span className="text-[14px] text-text-60">{t('wizard.subs.customization.font')}</span>
          <button type="button" aria-haspopup="listbox" aria-expanded={fontOpen} aria-controls="subtitle-font-list" onClick={() => setFontOpen((open) => !open)}
            className="flex h-[38px] w-[min(58%,260px)] min-w-0 items-center justify-between gap-[8px] rounded-r10 border border-white/15 bg-[#17121f] px-[10px] text-left text-[14px] text-text outline-none transition hover:border-white/30 focus:border-accent-light">
            <span className="truncate">{selectedFont.label}</span>
            <span aria-hidden="true" className="flex h-full w-[14px] shrink-0 items-center justify-center">
              <svg viewBox="0 0 16 16" className={cn('h-[14px] w-[14px] transition-transform', fontOpen && 'rotate-180')} fill="none">
                <path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </button>
        </div>
        {fontOpen && <div id="subtitle-font-list" role="listbox" aria-label={t('wizard.subs.customization.font')} className="mt-[8px] max-h-[224px] overflow-y-auto rounded-r10 border border-white/10 bg-[#17121f] p-[4px] shadow-[0_14px_34px_rgba(0,0,0,.38)]">
          {fonts.map((font) => <button key={font.value} type="button" role="option" aria-selected={settings.font === font.value}
            onClick={() => { updateText({ font: font.value }); setFontOpen(false); }}
            className={cn('flex min-h-[38px] w-full items-center justify-between gap-[12px] rounded-[7px] px-[10px] text-left text-[13px] transition-colors', settings.font === font.value ? 'bg-accent-20 text-text' : 'text-text-80 hover:bg-white/5 hover:text-text')}>
            <span className="truncate">{font.label}</span><span aria-hidden="true" className="shrink-0 text-[16px] font-bold text-text-60" style={{ fontFamily: font.family }}>Aa</span>
          </button>)}
        </div>}
      </div>
      <VisualChoice kind="size" label={t('wizard.subs.customization.size')} value={settings.size} onChange={(size) => updateText({ size })} options={[
        { value: 'small', label: t('wizard.subs.customization.small') }, { value: 'medium', label: t('wizard.subs.customization.medium') }, { value: 'large', label: t('wizard.subs.customization.large') }
      ]} />
      <VisualChoice kind="height" label={t('wizard.subs.customization.height')} value={settings.height} onChange={(height) => updateText({ height })} options={[
        { value: 'compact', label: t('wizard.subs.customization.compact') }, { value: 'normal', label: t('wizard.subs.customization.normal') }, { value: 'tall', label: t('wizard.subs.customization.tall') }
      ]} />
      <VisualChoice kind="position" label={t('wizard.subs.customization.position')} value={settings.position} onChange={(position) => updateText({ position })} options={[
        { value: 'left', label: t('wizard.subs.customization.left') }, { value: 'center', label: t('wizard.subs.customization.center') }, { value: 'right', label: t('wizard.subs.customization.right') }
      ]} />
      <VisualChoice kind="shadow" label={t('wizard.subs.customization.shadow')} value={settings.shadow} onChange={(shadow) => updateText({ shadow })} options={[
        { value: 'none', label: t('wizard.subs.customization.none') }, { value: 'soft', label: t('wizard.subs.customization.soft') }, { value: 'strong', label: t('wizard.subs.customization.strong') }
      ]} />
      <VisualChoice kind="outline" label={t('wizard.subs.customization.outline')} value={settings.outline} onChange={(outline) => updateText({ outline })} options={[
        { value: 'none', label: t('wizard.subs.customization.none') }, { value: 'thin', label: t('wizard.subs.customization.thin') }, { value: 'thick', label: t('wizard.subs.customization.thick') }
      ]} />
      <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-[16px] border-t border-white/10 pt-[14px]">
        <SubtitleColorControl guideRef={colorGuideTargetRef} label={t('wizard.subs.customization.color')} value={subtitles.color}
          defaultColor="#f6f5fd" defaultLabel={t('wizard.subs.customization.whiteColor')} onChange={(color) => setSubtitles({ color })} />
        <div aria-hidden="true" className="col-span-2 my-[10px] border-t border-white/10" />
        <SubtitleColorControl label={t('wizard.subs.customization.outlineColor')} value={settings.outlineColor}
          defaultColor="#000000" defaultLabel={t('wizard.subs.customization.defaultOutlineColor')} onChange={(outlineColor) => updateText({ outlineColor })} />
      </div>
    </div>
  </section>;
}

export function StageSubtitles() {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const toggleStyle = useWizardStore((state) => state.toggleSubtitleStyle);
  const stylesQuery = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  const cardsScroll = useDragScroll();
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

      <SubtitleTextCustomization colorGuideTargetRef={colorGuideTargetRef} />

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
      <div aria-hidden="true" className="h-[40px] shrink-0 max-md:h-[32px]" />
    </div>
  );
}

export function SubtitlesWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const lyrics = useWizardStore((state) => state.lyrics);
  const fragmentLyrics = useWizardStore((state) => state.fragmentLyrics);
  const pillsScroll = useDragScroll();
  const previewLyrics = (fragmentLyrics.trim() || lyrics.trim()) || t('wizard.subs.lyricsPlaceholder');
  const textSettings = { ...DEFAULT_SUBTITLE_TEXT_SETTINGS, ...(subtitles.text ?? {}) };
  const caption = previewLyrics.split(/[\n.!?]+/).filter(Boolean).slice(0, 2).join(' ');
  const fontFamilies: Record<SubtitleTextSettings['font'], string> = {
    'Point-SemiBold': 'Point, Arial, sans-serif', 'Point-ExtraBold': 'Point, Arial, sans-serif', 'Montserrat-Bold': 'Montserrat, Arial, sans-serif',
    'Arial-Bold': 'Arial, sans-serif', Impact: 'Impact, Arial Narrow, sans-serif', 'Georgia-Bold': 'Georgia, serif',
    'Trebuchet-Bold': 'Trebuchet MS, sans-serif', 'Courier-Bold': 'Courier New, monospace'
  };
  const sizeValue = { small: '5.2cqi', medium: '7cqi', large: '8.5cqi' }[textSettings.size];
  const heightScale = { compact: 0.8, normal: 1, tall: 1.3 }[textSettings.height];
  const shadow = { none: 'none', soft: '0 2px 7px rgba(0,0,0,.78)', strong: '0 3px 13px rgba(0,0,0,.95)' }[textSettings.shadow];
  const stroke = { none: '0 transparent', thin: `1px ${textSettings.outlineColor}`, thick: `2px ${textSettings.outlineColor}` }[textSettings.outline];
  const horizontal = textSettings.position === 'left' ? 'justify-start text-left' : textSettings.position === 'right' ? 'justify-end text-right' : 'justify-center text-center';

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        <h2 className="wizard-h mb-space-5 shrink-0 whitespace-nowrap">{t('wizard.workZone')}</h2>
        {/* телефон: у зоны своя высота (9:16) — иначе в авто-колонке она схлопывается вместе с даш-рамкой */}
        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-r15 bg-grad-soft-10 px-[8%] max-md:aspect-[9/16] max-md:w-full" style={{ containerType: 'inline-size' }}>
          <div className={cn('flex h-full w-full items-center', horizontal)}>
          <div className="max-w-full whitespace-pre-wrap break-words font-bold uppercase leading-[1.05]"
            style={{ color: subtitles.color, fontFamily: fontFamilies[textSettings.font], fontSize: sizeValue,
              transform: `scaleY(${heightScale})`, transformOrigin: 'center center', textShadow: shadow,
              WebkitTextStroke: stroke }}>
            {caption || 'Текст появится здесь'}
          </div>
          </div>
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
        onPill={() => undefined}
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
