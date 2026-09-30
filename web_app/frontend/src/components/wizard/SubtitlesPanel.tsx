import { PointerEvent as ReactPointerEvent, RefObject, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { useDragScroll } from './BackgroundPanel';
import { PillsFooter } from './WizardFrame';
import { SubtitleTextSettings, activeTextTab, allBackgroundsWide, textSettingsFor, useWizardStore } from '../../stores/wizardStore';
import {
  HEIGHT_SCALE, POSITION_CENTER_Y, SIZE_SCALE, STYLE_ACCENT_COLOR, accentFontsFor, baseFonts, cssFamily, findFont, fontBlockedFor, fontStyles,
  injectFontFaces, styleIdOf
} from '../../lib/subtitleText';
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

/**
 * Мини-визуал подсказки «Настройки текста»: две вкладки стилей по очереди
 * становятся активными, и вместе с ними меняется образец — у Jakson капс с
 * рукописным акцентом, у Brat строчный узкий с курсивным фокус-словом.
 * Показывает главное: у каждого стиля свой вид, переключение — вкладкой.
 */
function SubtitleTextGuideVisual() {
  return (
    <div className="flex w-full items-center gap-[10px] py-[12px]" aria-hidden="true">
      <div className="flex shrink-0 flex-col gap-[6px]">
        {['Jakson', 'Brat'].map((name, index) => (
          <span key={name} className="relative flex h-[26px] w-[62px] items-center justify-center overflow-hidden rounded-[7px] border border-white/15 text-[11px] leading-none text-text-60">
            <span className={cn('absolute inset-0 rounded-[7px] bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]', index === 0 ? 'guide-style-a' : 'guide-style-b')} />
            <span className="action-guide-optical-text relative">{name}</span>
          </span>
        ))}
      </div>
      <div className="relative h-[58px] flex-1 overflow-hidden rounded-[9px] bg-black/30">
        <span className="guide-style-a absolute inset-0 flex items-center justify-center gap-[5px] px-[6px] text-[15px] font-bold uppercase leading-none tracking-[-0.02em] text-white">
          <span className="action-guide-optical-text">не уйду</span> <span className="text-[19px] font-normal normal-case text-accent-light" style={{ fontFamily: '"blast-PrincessDiana", "blast-Katherine-Plus", cursive' }}>отсюда</span>
        </span>
        <span className="guide-style-b absolute inset-0 flex items-center justify-center gap-[6px] px-[6px] text-[16px] lowercase leading-none text-white"
          style={{ fontFamily: '"Arial Narrow", Arial, sans-serif', letterSpacing: '-0.02em' }}>
          не уйду <i className="font-bold text-accent-light">отсюда</i>
        </span>
      </div>
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
  kind: 'size' | 'height' | 'position' | 'shadow' | 'focus';
  label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void;
}) {
  return <fieldset className="flex min-w-0 items-center justify-between gap-[16px] border-0 border-b border-white/10 px-0 py-[13px] last:border-b-0">
    <legend className="sr-only">{label}</legend>
    <span className="min-w-0 text-[14px] text-text-60">{label}</span>
    <div className="flex shrink-0 gap-[4px] rounded-r10 border border-white/10 bg-black/15 p-[3px]">
      {options.map((item, index) => <button key={item.value} type="button" aria-label={`${label}: ${item.label}`} aria-pressed={value === item.value}
        onClick={() => onChange(item.value)} className={cn('flex h-[34px] w-[42px] items-center justify-center rounded-[7px] transition-colors', value === item.value ? 'bg-accent-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-40 hover:bg-white/5 hover:text-text-80')}>
        <ChoiceGlyph kind={kind} index={index} value={item.value} selected={value === item.value} />
      </button>)}
    </div>
  </fieldset>;
}

function ChoiceGlyph({ kind, index, value, selected }: { kind: 'size' | 'height' | 'position' | 'shadow' | 'focus'; index: number; value: string; selected: boolean }) {
  if (kind === 'position') {
    if (value === 'down') return <span className="relative flex h-[18px] w-[26px] items-end justify-center rounded-[4px] border border-current/55 pb-[2px]"><i className="h-[3px] w-[9px] rounded-full bg-current" /></span>;
    return <span className="relative flex h-[18px] w-[26px] items-center rounded-[4px] border border-current/55 px-[3px]"><i className={cn('h-[7px] w-[3px] rounded-full bg-current transition-all', value === 'left' ? 'mr-auto' : value === 'center' ? 'mx-auto' : 'ml-auto')} /></span>;
  }
  if (kind === 'focus') {
    const style = [{}, { fontStyle: 'italic' }, { fontStyle: 'italic', fontWeight: 800 }, { display: 'inline-block', transform: 'skewX(-12deg)' }][index] ?? {};
    return <span className="text-[16px] leading-none" style={{ fontFamily: '"Arial Narrow", Arial, sans-serif', ...style }}>a</span>;
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

function FontSelect({ label, value, options, onChange, listId }: {
  label: string;
  value: string;
  listId: string;
  options: { value: string; label: string; family: string; disabled?: string; lowercase?: boolean }[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = options.find((option) => option.value === value) ?? options[0];
  return <div className="border-b border-white/10 py-[13px]" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <div className="flex items-center justify-between gap-[16px]">
      <span className="text-[14px] text-text-60">{label}</span>
      <button type="button" aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} onClick={() => setOpen((next) => !next)}
        className="flex h-[38px] w-[min(58%,260px)] min-w-0 items-center justify-between gap-[8px] rounded-r10 border border-white/15 bg-[#17121f] px-[10px] text-left text-[14px] text-text outline-none transition hover:border-white/30 focus:border-accent-light">
        <span className="truncate">{selected?.label}</span>
        <span aria-hidden="true" className="flex h-full w-[14px] shrink-0 items-center justify-center">
          <svg viewBox="0 0 16 16" className={cn('h-[14px] w-[14px] transition-transform', open && 'rotate-180')} fill="none">
            <path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>
    </div>
    {open && <div id={listId} role="listbox" aria-label={label} className="mt-[8px] max-h-[264px] overflow-y-auto rounded-r10 border border-white/10 bg-[#17121f] p-[4px] shadow-[0_14px_34px_rgba(0,0,0,.38)]">
      {options.map((option) => <button key={option.value} type="button" role="option" aria-selected={value === option.value}
        aria-disabled={!!option.disabled} title={option.disabled}
        onClick={() => { if (option.disabled) return; onChange(option.value); setOpen(false); }}
        className={cn('flex min-h-[38px] w-full items-center justify-between gap-[12px] rounded-[7px] px-[10px] text-left text-[13px] transition-colors',
          option.disabled ? 'cursor-not-allowed text-text-40' : value === option.value ? 'bg-accent-20 text-text' : 'text-text-80 hover:bg-white/5 hover:text-text')}>
        <span className="min-w-0">
          <span className="block truncate">{option.label}</span>
          {option.disabled && <span className="block truncate text-[11px] text-text-40">{option.disabled}</span>}
        </span>
        <span aria-hidden="true" className="shrink-0 text-[17px] text-text-60" style={{ fontFamily: option.family }}>{option.lowercase ? 'аа' : 'Аа'}</span>
      </button>)}
    </div>}
  </div>;
}

function SubtitleTextCustomization({ guideTargetRef }: { guideTargetRef: RefObject<HTMLDivElement> }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const background = useWizardStore((state) => state.background);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const catalogQuery = useQuery({ queryKey: ['subtitle-fonts'], queryFn: api.subtitleFonts, staleTime: Infinity });
  const catalog = catalogQuery.data;
  useEffect(() => { injectFontFaces(catalog); }, [catalog]);
  // вкладка = стиль из пула, который сейчас настраивается (у каждого стиля свои настройки)
  const tab = activeTextTab(subtitles);
  const settings = textSettingsFor(subtitles, tab);
  const updateText = (patch: Partial<SubtitleTextSettings>) => {
    if (!tab) return;
    setSubtitles({ textByStyle: { ...subtitles.textByStyle, [tab]: { ...settings, ...patch } } });
  };

  const tabStyle = tab ? styleIdOf(tab) : null;
  const styles = tabStyle ? [tabStyle] : [];
  const pickable = fontStyles(styles, catalog);
  const hasBrat = tabStyle === 'brat';
  const styleAccent = (tabStyle && STYLE_ACCENT_COLOR[tabStyle]) || subtitles.color;
  const base = findFont(catalog, settings.font);
  const accents = accentFontsFor(catalog, base, pickable);
  const wide = allBackgroundsWide(background);
  const blocked = base ? fontBlockedFor(base, pickable) : [];
  const heightAllowed = !!base?.serif;

  const fontOptions = [
    { value: '', label: t('wizard.subs.customization.fontDefault'), family: 'Point, Arial, sans-serif' },
    ...baseFonts(catalog).map((font) => {
      const off = fontBlockedFor(font, pickable);
      return { value: font.ps, label: font.label, family: cssFamily(font), lowercase: font.lowercase,
        disabled: off.length ? t('wizard.subs.customization.fontUnavailable', { styles: off.join(', ') }) : undefined };
    })
  ];
  const onFont = (value: string) => {
    const next = findFont(catalog, value || null);
    updateText({
      font: next ? next.ps : null,
      // пара и высота зависят от основного: несовместимое снимаем вместе со сменой шрифта
      accentFont: settings.accentFont && (next ? next.accents : (tabStyle && catalog?.defaultAccents?.[tabStyle]) || []).includes(settings.accentFont)
        ? settings.accentFont : null,
      height: next?.serif ? settings.height : 'normal'
    });
  };
  const positions = [
    { value: 'left' as const, label: t('wizard.subs.customization.left') },
    { value: 'center' as const, label: t('wizard.subs.customization.center') },
    { value: 'right' as const, label: t('wizard.subs.customization.right') },
    ...(wide ? [{ value: 'down' as const, label: t('wizard.subs.customization.down') }] : [])
  ];

  return <section ref={guideTargetRef} className="mt-[40px] rounded-r15 bg-grad-soft-10 px-[32px] py-[28px] max-md:mt-[16px] max-md:px-[16px] max-md:py-[18px]">
    <div className="mb-[22px]">
      <h3 className="wizard-body">{t('wizard.subs.customization.title')}</h3>
      <p className="mt-[5px] text-[14px] text-text-60">{t('wizard.subs.customization.description')}</p>
    </div>
    {subtitles.pool.length > 1 && <div role="tablist" aria-label={t('wizard.subs.customization.styleTabs')} className="mb-[10px] flex flex-wrap gap-[6px]">
      {subtitles.pool.map((name) => <button key={name} type="button" role="tab" aria-selected={name === tab}
        onClick={() => setSubtitles({ textTab: name })}
        className={cn('h-[34px] rounded-r10 px-[14px] text-[13px] transition-colors', name === tab
          ? 'bg-accent-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'border border-white/10 text-text-60 hover:bg-white/5 hover:text-text')}>{name}</button>)}
    </div>}
    {!tab ? <p className="text-[13px] text-text-40">{t('wizard.subs.customization.pickStyleFirst')}</p> : <div className="flex flex-col">
      {queryDown(catalogQuery) && <InlineError error={catalogQuery.error} offline={catalogQuery.fetchStatus === 'paused'}
        onRetry={() => catalogQuery.refetch()} retrying={catalogQuery.isFetching} />}
      {pickable.length > 0 && <FontSelect listId="subtitle-font-list" label={t('wizard.subs.customization.font')}
        value={settings.font ?? ''} options={fontOptions} onChange={onFont} />}
      {hasBrat && <p className="border-b border-white/10 py-[10px] text-[13px] text-text-40">{t('wizard.subs.customization.fontLockedBrat')}</p>}
      {blocked.length > 0 && <p role="alert" className="py-[8px] text-[13px] text-[#ff8a8a]">{t('wizard.subs.customization.fontInvalid', { styles: blocked.join(', ') })}</p>}
      {accents.length > 0 && <FontSelect listId="subtitle-accent-list" label={t('wizard.subs.customization.accentFont')}
        value={settings.accentFont ?? ''} onChange={(value) => updateText({ accentFont: value || null })} options={[
          { value: '', label: t('wizard.subs.customization.accentNone'), family: cssFamily(base) },
          ...accents.map((font) => ({ value: font.ps, label: font.label, family: cssFamily(font), lowercase: true }))
        ]} />}
      <VisualChoice kind="size" label={t('wizard.subs.customization.size')} value={settings.size} onChange={(size) => updateText({ size })} options={[
        { value: 'small', label: t('wizard.subs.customization.small') }, { value: 'medium', label: t('wizard.subs.customization.medium') }, { value: 'large', label: t('wizard.subs.customization.large') }
      ]} />
      {heightAllowed && <VisualChoice kind="height" label={t('wizard.subs.customization.height')} value={settings.height} onChange={(height) => updateText({ height })} options={[
        { value: 'compact', label: t('wizard.subs.customization.compact') }, { value: 'normal', label: t('wizard.subs.customization.normal') }, { value: 'tall', label: t('wizard.subs.customization.tall') }
      ]} />}
      <VisualChoice kind="position" label={t('wizard.subs.customization.position')} value={settings.position} onChange={(position) => updateText({ position })} options={positions} />
      {settings.position === 'down' && !wide && <p role="alert" className="py-[8px] text-[13px] text-[#ff8a8a]">{t('wizard.subs.customization.downInvalid')}</p>}
      <VisualChoice kind="shadow" label={t('wizard.subs.customization.shadow')} value={settings.shadow} onChange={(shadow) => updateText({ shadow })} options={[
        { value: 'none', label: t('wizard.subs.customization.none') }, { value: 'soft', label: t('wizard.subs.customization.soft') }, { value: 'strong', label: t('wizard.subs.customization.strong') }
      ]} />
      {hasBrat && <VisualChoice kind="focus" label={t('wizard.subs.customization.focusStyle')} value={settings.focusStyle ?? 'none'}
        onChange={(focus) => updateText({ focusStyle: focus === 'none' ? null : focus })} options={[
          { value: 'none', label: t('wizard.subs.customization.focusNone') }, { value: 'italic', label: t('wizard.subs.customization.focusItalic') },
          { value: 'bold_italic', label: t('wizard.subs.customization.focusBoldItalic') }, { value: 'faux_italic', label: t('wizard.subs.customization.focusFaux') }
        ]} />}
      <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-[16px] gap-y-[14px] border-t border-white/10 pt-[14px]">
        <SubtitleColorControl label={t('wizard.subs.customization.color')} value={subtitles.color}
          defaultColor="#f6f5fd" defaultLabel={t('wizard.subs.customization.whiteColor')} onChange={(color) => setSubtitles({ color })} />
        {/* тот же контрол, что у цвета текста: свотч = прод-цвет акцента стиля (у Jakson/Tape
            красный, у остальных фокус как основной текст); клик по нему — «как в стиле» (null) */}
        <SubtitleColorControl label={t('wizard.subs.customization.accentColor')} value={settings.accentColor ?? styleAccent}
          defaultColor={styleAccent} defaultLabel={t('wizard.subs.customization.accentColorDefault')}
          onChange={(color) => updateText({ accentColor: color.toLowerCase() === styleAccent.toLowerCase() ? null : color })} />
      </div>
    </div>}
  </section>;
}

export function StageSubtitles() {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const toggleStyle = useWizardStore((state) => state.toggleSubtitleStyle);
  const stylesQuery = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  const cardsScroll = useDragScroll();
  const timelineGuideTargetRef = useRef<HTMLDivElement>(null);
  const textGuideTargetRef = useRef<HTMLDivElement>(null);
  const stylesGuideTargetRef = useRef<HTMLDivElement>(null);
  const hasStyles = subtitles.pool.length > 0;
  const textConfigured = Object.keys(subtitles.textByStyle ?? {}).length > 0;
  // Маршрут: подгонка слов → выбор стилей → настройки текста. Настройки — ПОСЛЕ
  // выбора: у каждого стиля свои (вкладки), без стиля блоку нечего показать.
  // Хуки объявлены от ПОСЛЕДНЕГО шага цепочки к первому: idle-условие шага N
  // требует dismissed-значения шага N+1 («мы ещё не ушли дальше»), поэтому оно
  // должно быть уже посчитано на момент объявления хука для шага N.
  // visible 2-го/3-го шага — false, показ отмечает useMarkGuideSeen ниже, когда
  // пройдены предыдущие шаги (иначе seen записался бы в момент маунта).
  const [textGuideDismissed, setTextGuideDismissed] = useGuideDismiss('subtitles-text', hasStyles && !textConfigured, false);
  const [stylesGuideDismissed, setStylesGuideDismissed] = useGuideDismiss('subtitles-styles', !hasStyles && !textGuideDismissed, false);
  const [timelineGuideDismissed, setTimelineGuideDismissed] = useGuideDismiss('subtitles-timeline', !hasStyles && !stylesGuideDismissed, true);
  const showTimelineGuide = !timelineGuideDismissed;
  const showStylesGuide = timelineGuideDismissed && !stylesGuideDismissed;
  // цель третьей подсказки (блок настроек стиля) есть только при выбранном стиле
  const showTextGuide = timelineGuideDismissed && stylesGuideDismissed && !textGuideDismissed && hasStyles;
  useMarkGuideSeen('subtitles-styles', showStylesGuide);
  useMarkGuideSeen('subtitles-text', showTextGuide);

  useScrollGuideIntoView(showTimelineGuide, timelineGuideTargetRef);
  useScrollGuideIntoView(showStylesGuide, stylesGuideTargetRef);
  // блок настроек выше экрана — к его началу (заголовок и вкладки), не к середине
  useScrollGuideIntoView(showTextGuide, textGuideTargetRef, 'start');

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

      <SubtitleTextCustomization guideTargetRef={textGuideTargetRef} />

      <ActionGuideOverlay
        open={showStylesGuide}
        targetRef={stylesGuideTargetRef}
        title={t('wizard.subs.guideStyleTitle')}
        text={t('wizard.subs.guideStyleText')}
        dismissLabel={t('wizard.subs.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 3 })}
        onDismiss={() => setStylesGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleStyleGuideVisual />}
      />

      <ActionGuideOverlay
        open={showTextGuide}
        targetRef={textGuideTargetRef}
        title={t('wizard.subs.guideTextTitle')}
        text={t('wizard.subs.guideTextText')}
        dismissLabel={t('wizard.subs.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 3, total: 3 })}
        onDismiss={() => setTextGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleTextGuideVisual />}
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
  const tab = activeTextTab(subtitles);
  const textSettings = textSettingsFor(subtitles, tab);
  const caption = previewLyrics.split(/[\n.!?]+/).filter(Boolean).slice(0, 2).join(' ');
  // Превью по тем же пресетам, что у рендера (lib/subtitleText.ts ↔ app/subtitle_font_layout.py):
  // размер ×1/0.9/0.8, высота — только у шрифтов с засечками, «снизу» — 64% кадра,
  // скрипты — строчными, фокус-слово — акцентным шрифтом пары и акцентным цветом.
  const catalog = useQuery({ queryKey: ['subtitle-fonts'], queryFn: api.subtitleFonts, staleTime: Infinity }).data;
  useEffect(() => { injectFontFaces(catalog); }, [catalog]);
  const tabStyle = tab ? styleIdOf(tab) : null;
  const base = tabStyle === 'brat' ? undefined : findFont(catalog, textSettings.font);
  const accent = findFont(catalog, textSettings.accentFont);
  const defaultPs = tabStyle === 'brat' ? 'ArialNarrow' : catalog?.defaults[tabStyle ?? 'jakson'];
  const family = base ? cssFamily(base) : tabStyle === 'brat' ? '"Arial Narrow", Arial, sans-serif' : cssFamily(findFont(catalog, defaultPs), defaultPs);
  const lowercase = !!base?.lowercase;
  const sizeValue = `${(8.5 * SIZE_SCALE[textSettings.size]).toFixed(2)}cqi`;
  const heightScale = base?.serif ? HEIGHT_SCALE[textSettings.height] : 1;
  const shadow = { none: 'none', soft: '0 2px 7px rgba(0,0,0,.78)', strong: '0 3px 13px rgba(0,0,0,.95)' }[textSettings.shadow];
  const align = textSettings.position === 'left' ? 'text-left' : textSettings.position === 'right' ? 'text-right' : 'text-center';
  const words = (caption || 'Текст появится здесь').split(/\s+/).filter(Boolean);
  const focusIndex = words.length > 1 ? words.length - 1 : -1;
  const accentColor = textSettings.accentColor ?? ((tabStyle && STYLE_ACCENT_COLOR[tabStyle]) || subtitles.color);

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        <h2 className="wizard-h mb-space-5 shrink-0 whitespace-nowrap">{t('wizard.workZone')}</h2>
        {/* телефон: у зоны своя высота (9:16) — иначе в авто-колонке она схлопывается вместе с даш-рамкой */}
        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-r15 bg-grad-soft-10 px-[8%] max-md:aspect-[9/16] max-md:w-full" style={{ containerType: 'inline-size' }}>
          <div className="absolute inset-x-[7%] -translate-y-1/2" style={{ top: `${POSITION_CENTER_Y[textSettings.position] * 100}%` }}>
          <div className={cn('max-w-full whitespace-pre-wrap break-words font-bold leading-[1.05]', align, !lowercase && 'uppercase')}
            style={{ color: subtitles.color, fontFamily: family, fontSize: sizeValue,
              transform: `scaleY(${heightScale})`, transformOrigin: 'center center', textShadow: shadow }}>
            {words.map((word, index) => {
              const isFocus = index === focusIndex && (accent || textSettings.accentColor);
              return <span key={index}>{index > 0 && ' '}{isFocus
                ? <span className={cn(accent && 'normal-case font-normal')} style={{ color: accentColor, fontFamily: accent ? cssFamily(accent) : undefined,
                  fontSize: accent ? '1.25em' : undefined, lineHeight: accent ? 0 : undefined }}>{accent ? word.toLowerCase() : word}</span>
                : word}</span>;
            })}
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
