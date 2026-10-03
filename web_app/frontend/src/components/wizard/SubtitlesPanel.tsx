import { ReactNode, RefObject, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { nearestHuePercent } from '../../lib/huePosition';
import { MediaCard, Rail, useBackdrop } from './BackgroundPanel';
import { PreviewVideo } from './CatalogPreview';
import { catalogPosterOf, useInView } from '../../lib/media';
import { PAUSE, PLAY, PillsFooter, Svg, W12 } from './WizardFrame';
import { SubtitleTextSettings, activeTextTab, allBackgroundsWide, textSettingsFor, useWizardStore } from '../../stores/wizardStore';
import {
  HEIGHT_SCALE, POSITION_CENTER_Y, SIZE_SCALE, styleAccentColor, accentFontsFor, baseFonts, cssFamily, findFont, fontBlockedFor, fontStyles,
  styleIdOf, isFixedStyle, type SubtitleStyleId
} from '../../lib/subtitleText';
import { SubtitleTimeline } from './SubtitleTimeline';
import { SubtitleCanvas, type SubtitleCanvasProps } from './SubtitleCanvas';
import { useSubtitleClock } from '../../lib/subtitleClock';
import { useSubtitleFonts } from '../../lib/useSubtitleFonts';

const clockLabel = (s: number) => {
  const v = Math.max(0, s);
  const m = Math.floor(v / 60);
  return `${m}:${(v - m * 60).toFixed(1).padStart(4, '0')}`;
};
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
      {/* ui-allow: иллюстрация гайда */}
      <span className="guide-fit-drag relative flex h-[32px] w-[52px] shrink-0 items-center justify-center overflow-hidden rounded-[7px] bg-white/15 text-[11px] leading-none text-white">
        {/* ui-allow: иллюстрация гайда */}
        <span className="guide-fit-select absolute inset-0 rounded-[7px] bg-accent-light" aria-hidden="true" />
        <span className="relative z-[1]">Я</span>
      </span>
      {/* ui-allow: иллюстрация гайда */}
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
          <span /* ui-allow: иллюстрация гайда */ key={name} className="relative flex h-[26px] w-[62px] items-center justify-center overflow-hidden rounded-[7px] border border-white/15 text-[11px] leading-none text-text-60">
            {/* ui-allow: иллюстрация гайда */}
            <span className={cn('absolute inset-0 rounded-[7px] bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]', index === 0 ? 'guide-style-a' : 'guide-style-b')} />
            {/* Point сидит высоко: для 11px хватает 1px (3px из action-guide-optical-text — для кнопок) */}
            <span className="relative translate-y-[1px]">{name}</span>
          </span>
        ))}
      </div>
      {/* ui-allow: иллюстрация гайда */}
      <div className="relative h-[58px] flex-1 overflow-hidden rounded-[9px] bg-black/30">
        {/* ui-allow: иллюстрация гайда */}
        <span className="guide-style-a absolute inset-0 flex items-center justify-center gap-[5px] px-[6px] text-[15px] font-bold uppercase leading-none tracking-[-0.02em] text-white">
          {/* ui-allow: иллюстрация гайда */}
          <span className="translate-y-[1px]">не уйду</span> <span className="text-[19px] font-normal normal-case text-accent-light" style={{ fontFamily: '"blast-PrincessDiana", "blast-Katherine-Plus", cursive' }}>отсюда</span>
        </span>
        {/* ui-allow: иллюстрация гайда */}
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
            // ui-allow: иллюстрация гайда
            'guide-mode-reveal relative flex h-[46px] items-center justify-center overflow-hidden rounded-[8px] bg-gradient-to-b from-[#42335e] to-[#181126]',
            index === 0 ? 'guide-mode-delay-1' : index === 1 ? 'guide-mode-delay-2' : 'guide-mode-delay-3',
            index === 1 && 'ring-2 ring-inset ring-accent-light'
          )}
        >
          {/* ui-allow: иллюстрация гайда */}
          <em className="font-bold italic text-[16px] leading-none text-white/85">T</em>
        </span>
      ))}
    </div>
  );
}

/*
 * Шаг «Текст» — в языке макета wizard12, как «Трек» и «Фон»: слева проверка субтитров,
 * стили лентой карточек (номер = порядок в пуле) и настройки текста выбранного стиля;
 * справа превью субтитров поверх выбранного на «Фоне» кадра и итог шага.
 */

/** Строка настройки: подпись слева, контрол справа. */
function SetRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className="w12-set-row"><span className="w12-set-lbl">{label}</span>{children}</div>;
}

type ChoiceKind = 'size' | 'height' | 'position' | 'shadow' | 'focus';

function VisualChoice<T extends string>({ kind, label, value, options, onChange }: {
  kind: ChoiceKind; label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void;
}) {
  return (
    <SetRow label={label}>
      <div className="w12-seg" role="radiogroup" aria-label={label}>
        {options.map((item, index) => (
          <button key={item.value} type="button" role="radio" aria-checked={value === item.value} aria-label={`${label}: ${item.label}`} title={item.label}
            className="w12-seg-btn" onClick={() => onChange(item.value)}>
            <ChoiceGlyph kind={kind} index={index} value={item.value} />
          </button>
        ))}
      </div>
    </SetRow>
  );
}

/** Значок варианта: сам показывает, что изменится (размер буквы, рост, место строки, тень, фокус). */
function ChoiceGlyph({ kind, index, value }: { kind: ChoiceKind; index: number; value: string }) {
  if (kind === 'position') return <span className="w12-g-frame" data-pos={value}><i /></span>;
  if (kind === 'height') return <span className="w12-g-height" style={{ height: [8, 13, 18][index] }}><i /></span>;
  if (kind === 'shadow') return <span className="w12-g-shadow"><i /><i style={{ left: [0, 2, 5][index], top: [0, 2, 4][index], opacity: [0, .5, .8][index] }} /></span>;
  if (kind === 'size') return <span className="w12-g-letter w12-l" style={{ fontSize: [11, 15, 19][index] }}>A</span>;
  const focus = [{}, { fontStyle: 'italic' }, { fontStyle: 'italic', fontWeight: 700 }, { display: 'inline-block', transform: 'skewX(-12deg)' }][index] ?? {};
  return <span className="w12-g-letter w12-l" style={{ fontSize: 16, fontFamily: '"Arial Narrow", Arial, sans-serif', ...focus }}>a</span>;
}

/** Цвет: свотч = цвет «как в стиле», полоса — свой оттенок; как выбор цвета на шаге «Фон». */
function SubtitleColorControl({ label, value, defaultColor, defaultLabel, onChange }: {
  label: string; value: string; defaultColor: string; defaultLabel: string; onChange: (color: string) => void;
}) {
  const [huePct, setHuePct] = useState(() => nearestHuePercent(value));
  const barRef = useRef<HTMLDivElement>(null);
  const isDefault = value.toLowerCase() === defaultColor.toLowerCase();
  useEffect(() => {
    if (!isDefault) setHuePct(nearestHuePercent(value));
  }, [isDefault, value]);
  const pick = (clientX: number) => {
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pct = Math.min(100, Math.max(0, ((clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    onChange(hueAt(pct));
  };
  return (
    <SetRow label={label}>
      <div className="w12-set-color">
        <button type="button" className="w12-sw w12-sw-sm" aria-label={defaultLabel} title={defaultLabel} aria-pressed={isDefault}
          style={{ background: defaultColor }} onClick={() => onChange(defaultColor)} />
        <div
          ref={barRef}
          role="slider"
          tabIndex={0}
          aria-label={label}
          aria-valuetext={value}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(huePct)}
          title={value.toUpperCase()}
          className="w12-hue w12-hue-sm"
          style={{ background: HUE_GRADIENT }}
          onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); pick(event.clientX); }}
          onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) pick(event.clientX); }}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            const next = Math.max(0, Math.min(100, huePct + (event.key === 'ArrowRight' ? 1 : -1)));
            setHuePct(next);
            onChange(hueAt(next));
          }}
        >
          {!isDefault && <span className="w12-thumb" style={{ left: `${huePct}%`, background: value }} />}
        </div>
      </div>
    </SetRow>
  );
}

type FontOption = { value: string; label: string; family: string; disabled?: string; lowercase?: boolean };

/**
 * Образец «Аа» в строке меню шрифтов. Шрифт скачивается, только когда строка показалась в
 * прокрутке меню: раньше открытие меню тянуло все ~39 шрифтов каталога (~2,9 МБ) разом.
 * Показавшийся раз образец остаётся своим шрифтом (файл уже в кэше).
 */
function FontSample({ option, menuRef }: { option: FontOption; menuRef: RefObject<HTMLDivElement> }) {
  const ref = useRef<HTMLSpanElement>(null);
  const shown = useInView(ref, { root: menuRef, rootMargin: '120px 0px', once: true });
  return (
    <span ref={ref} className="w12-font-sample" aria-hidden="true" style={shown ? { fontFamily: option.family } : { visibility: 'hidden' }}>
      {option.lowercase ? 'аа' : 'Аа'}
    </span>
  );
}

/**
 * Выбор шрифта: выпадающий список с образцом «Аа» каждым шрифтом; недоступные — с причиной.
 * С клавиатуры — как у типа подборки на «Фоне»: ↓/↑ открывают и ходят по шрифтам (недоступные
 * пропускаются), Home/End — к краям, Enter/пробел выбирают, Esc закрывает. Фокус на время
 * открытия уходит в список и возвращается на кнопку.
 */
function FontMenu({ label, value, options, onChange }: { label: string; value: string; options: FontOption[]; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const optionId = (index: number) => `${listId}-${index}`;
  const selected = options.find((option) => option.value === value) ?? options[0];
  useEffect(() => {
    if (!open) return undefined;
    const current = options.findIndex((option) => option.value === value);
    setActive(current >= 0 ? current : Math.max(0, options.findIndex((option) => !option.disabled)));
    menuRef.current?.focus();
    const onDown = (event: PointerEvent) => { if (!wrapRef.current?.contains(event.target as Node)) setOpen(false); };
    window.addEventListener('pointerdown', onDown);
    return () => window.removeEventListener('pointerdown', onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  // активная строка всегда в видимой части прокрутки меню
  useEffect(() => {
    if (open) document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active]);
  const close = () => { setOpen(false); buttonRef.current?.focus(); };
  const pick = (option: FontOption | undefined) => {
    if (!option || option.disabled) return;
    onChange(option.value);
    close();
  };
  /** следующий доступный шрифт в сторону dir (по кругу); недоступные не выбираются и с клавиатуры */
  const step = (from: number, dir: 1 | -1) => {
    for (let k = 1; k <= options.length; k++) {
      const index = (from + dir * k + options.length * k) % options.length;
      if (!options[index].disabled) return index;
    }
    return from;
  };
  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      // гасим здесь: иначе тот же Esc закрыл бы и подсказку шага (она слушает window)
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) { setOpen(true); return; }
      setActive((index) => step(index, event.key === 'ArrowDown' ? 1 : -1));
      return;
    }
    if (!open) return;
    if (event.key === 'Home') { event.preventDefault(); setActive(step(options.length - 1, 1)); return; }
    if (event.key === 'End') { event.preventDefault(); setActive(step(0, -1)); return; }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(options[active]); return; }
    if (event.key === 'Tab') setOpen(false);
  };
  return (
    <SetRow label={label}>
      <div ref={wrapRef} className="w12-dd-wrap w12-font-dd" onKeyDown={onKey}>
        <button ref={buttonRef} type="button" className="w12-dd" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined} aria-label={`${label}: ${selected?.label ?? ''}`} onClick={() => setOpen((next) => !next)}>
          <span className="w12-l">{selected?.label}</span><Svg>{W12.down}</Svg>
        </button>
        {open && (
          <div ref={menuRef} id={listId} role="listbox" tabIndex={-1} aria-label={label} aria-activedescendant={optionId(active)} className="w12-dd-menu w12-font-menu">
            {options.map((option, index) => (
              <div key={option.value} id={optionId(index)} role="option" aria-selected={value === option.value} aria-disabled={Boolean(option.disabled) || undefined}
                title={option.disabled} data-active={index === active || undefined} className={cn('w12-dd-opt', option.disabled && 'w12-off')}
                onPointerEnter={() => { if (!option.disabled) setActive(index); }}
                onClick={() => pick(option)}>
                <span className="w12-font-name">
                  <span className="w12-l">{option.label}</span>
                  {option.disabled && <small>{option.disabled}</small>}
                </span>
                <FontSample option={option} menuRef={menuRef} />
              </div>
            ))}
          </div>
        )}
      </div>
    </SetRow>
  );
}

export function SubtitleTextCustomization({ guideTargetRef }: { guideTargetRef: RefObject<HTMLDivElement> }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const background = useWizardStore((state) => state.background);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const catalogQuery = useQuery({ queryKey: ['subtitle-fonts'], queryFn: api.subtitleFonts, staleTime: Infinity });
  const catalog = catalogQuery.data;
  // @font-face шрифтов каталога — образцы в списках рисуются ими же, что и превью
  useSubtitleFonts();
  // вкладка = стиль из пула, который сейчас настраивается (у каждого стиля свои настройки)
  const tab = activeTextTab(subtitles);
  const settings = textSettingsFor(subtitles, tab);
  const updateText = (patch: Partial<SubtitleTextSettings>) => {
    if (!tab) return;
    setSubtitles({ textByStyle: { ...subtitles.textByStyle, [tab]: { ...settings, ...patch } } });
  };

  const tabStyle = tab ? styleIdOf(tab) : null;
  const styles = tabStyle ? [tabStyle] : [];
  // вкладки стилей — табы с панелью настроек: стрелки ходят по вкладкам, панель подписана активной
  const tabsId = useId();
  const styleTabId = (index: number) => `${tabsId}-tab-${index}`;
  const stylePanelId = `${tabsId}-panel`;
  const styleTabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const hasStyleTabs = subtitles.pool.length > 1;
  const activeTabIndex = Math.max(0, subtitles.pool.findIndex((name) => name === tab));
  const panelA11y = hasStyleTabs ? { id: stylePanelId, role: 'tabpanel', 'aria-labelledby': styleTabId(activeTabIndex) } : {};
  const onStyleTabKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const count = subtitles.pool.length;
    const dir = ({ ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 } as Record<string, number>)[event.key];
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? count - 1 : dir === undefined ? -1 : (activeTabIndex + dir + count) % count;
    if (index < 0) return;
    event.preventDefault();
    setSubtitles({ textTab: subtitles.pool[index] });
    styleTabRefs.current[index]?.focus();
  };
  const pickable = fontStyles(styles, catalog);
  const hasBrat = tabStyle === 'brat';
  const fixed = isFixedStyle(tabStyle);
  const styleAccent = styleAccentColor(catalog, tabStyle, subtitles.color);
  const base = findFont(catalog, settings.font);
  const accents = accentFontsFor(catalog, base, pickable);
  const wide = allBackgroundsWide(background);
  const blocked = base ? fontBlockedFor(base, pickable) : [];
  const heightAllowed = !!base?.serif;

  const fontOptions: FontOption[] = [
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

  return (
    <section ref={guideTargetRef} className="w12-sec">
      <div className="w12-sec-head">
        <h2><span className="w12-l">{t('wizard.subs.customization.title')}</span></h2>
      </div>
      <p className="w12-sec-note">{t('wizard.subs.customization.description')}</p>
      <div className="w12-cut w12-set">
        {hasStyleTabs && (
          <div className="w12-types" role="tablist" aria-label={t('wizard.subs.customization.styleTabs')} onKeyDown={onStyleTabKey}>
            {subtitles.pool.map((name, index) => (
              <button key={name} ref={(el) => { styleTabRefs.current[index] = el; }} id={styleTabId(index)} type="button" role="tab" aria-selected={name === tab}
                aria-controls={stylePanelId} tabIndex={index === activeTabIndex ? 0 : -1} className="w12-type" onClick={() => setSubtitles({ textTab: name })}>
                <span className="w12-l">{name}</span>
              </button>
            ))}
          </div>
        )}
        {!tab ? <p className="w12-set-empty" {...panelA11y}>{t('wizard.subs.customization.pickStyleFirst')}</p> : (
          <div className="w12-set-rows" {...panelA11y}>
            {/* тайтл: шрифт, цвет и анимация зашиты в шаблон — остаются размер и положение */}
            {fixed && <p className="w12-set-note">{t('wizard.subs.customization.fixedTitle')}</p>}
            {queryDown(catalogQuery) && <InlineError error={catalogQuery.error} offline={catalogQuery.fetchStatus === 'paused'} onRetry={() => catalogQuery.refetch()} retrying={catalogQuery.isFetching} />}
            {pickable.length > 0 && <FontMenu label={t('wizard.subs.customization.font')} value={settings.font ?? ''} options={fontOptions} onChange={onFont} />}
            {hasBrat && <p className="w12-set-note">{t('wizard.subs.customization.fontLockedBrat')}</p>}
            {blocked.length > 0 && <p role="alert" className="w12-miss">{t('wizard.subs.customization.fontInvalid', { styles: blocked.join(', ') })}</p>}
            {accents.length > 0 && (
              <FontMenu label={t('wizard.subs.customization.accentFont')} value={settings.accentFont ?? ''} onChange={(value) => updateText({ accentFont: value || null })} options={[
                { value: '', label: t('wizard.subs.customization.accentNone'), family: cssFamily(base) },
                ...accents.map((font) => ({ value: font.ps, label: font.label, family: cssFamily(font), lowercase: true }))
              ]} />
            )}
            <VisualChoice kind="size" label={t('wizard.subs.customization.size')} value={settings.size} onChange={(size) => updateText({ size })} options={[
              { value: 'small', label: t('wizard.subs.customization.small') }, { value: 'medium', label: t('wizard.subs.customization.medium') }, { value: 'large', label: t('wizard.subs.customization.large') }
            ]} />
            {heightAllowed && <VisualChoice kind="height" label={t('wizard.subs.customization.height')} value={settings.height} onChange={(height) => updateText({ height })} options={[
              { value: 'compact', label: t('wizard.subs.customization.compact') }, { value: 'normal', label: t('wizard.subs.customization.normal') }, { value: 'tall', label: t('wizard.subs.customization.tall') }
            ]} />}
            <VisualChoice kind="position" label={t('wizard.subs.customization.position')} value={settings.position} onChange={(position) => updateText({ position })} options={positions} />
            {settings.position === 'down' && !wide && <p role="alert" className="w12-miss">{t('wizard.subs.customization.downInvalid')}</p>}
            {!fixed && <VisualChoice kind="shadow" label={t('wizard.subs.customization.shadow')} value={settings.shadow} onChange={(shadow) => updateText({ shadow })} options={[
              { value: 'none', label: t('wizard.subs.customization.none') }, { value: 'soft', label: t('wizard.subs.customization.soft') }, { value: 'strong', label: t('wizard.subs.customization.strong') }
            ]} />}
            {hasBrat && <VisualChoice kind="focus" label={t('wizard.subs.customization.focusStyle')} value={settings.focusStyle ?? 'none'}
              onChange={(focus) => updateText({ focusStyle: focus === 'none' ? null : focus })} options={[
                { value: 'none', label: t('wizard.subs.customization.focusNone') }, { value: 'italic', label: t('wizard.subs.customization.focusItalic') },
                { value: 'bold_italic', label: t('wizard.subs.customization.focusBoldItalic') }, { value: 'faux_italic', label: t('wizard.subs.customization.focusFaux') }
              ]} />}
            {!fixed && <>
            <SubtitleColorControl label={t('wizard.subs.customization.color')} value={subtitles.color}
              defaultColor={WHITE_TEXT} defaultLabel={t('wizard.subs.customization.whiteColor')} onChange={(color) => setSubtitles({ color })} />
            {/* свотч = прод-цвет акцента стиля (у Jakson/Tape красный, у остальных — как основной текст);
                клик по нему — «как в стиле» (null) */}
            <SubtitleColorControl label={t('wizard.subs.customization.accentColor')} value={settings.accentColor ?? styleAccent}
              defaultColor={styleAccent} defaultLabel={t('wizard.subs.customization.accentColorDefault')}
              onChange={(color) => updateText({ accentColor: color.toLowerCase() === styleAccent.toLowerCase() ? null : color })} />
            </>}
          </div>
        )}
      </div>
    </section>
  );
}

export function StageSubtitles() {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const toggleStyle = useWizardStore((state) => state.toggleSubtitleStyle);
  const stylesQuery = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  const timelineGuideTargetRef = useRef<HTMLDivElement>(null);
  const textGuideTargetRef = useRef<HTMLDivElement>(null);
  const stylesGuideTargetRef = useRef<HTMLDivElement>(null);
  const hasStyles = subtitles.pool.length > 0;
  const textConfigured = Object.keys(subtitles.textByStyle ?? {}).length > 0;
  // Маршрут (сверху вниз, как на экране): выбор стилей → проверка слов → настройки текста.
  // Настройки — ПОСЛЕ выбора: у каждого стиля свои (вкладки), без стиля блоку нечего показать.
  // Хуки объявлены от ПОСЛЕДНЕГО шага цепочки к первому: idle-условие шага N
  // требует dismissed-значения шага N+1 («мы ещё не ушли дальше»), поэтому оно
  // должно быть уже посчитано на момент объявления хука для шага N.
  // visible 2-го/3-го шага — false, показ отмечает useMarkGuideSeen ниже, когда
  // пройдены предыдущие шаги (иначе seen записался бы в момент маунта).
  const [textGuideDismissed, setTextGuideDismissed] = useGuideDismiss('subtitles-text', hasStyles && !textConfigured, false);
  const [timelineGuideDismissed, setTimelineGuideDismissed] = useGuideDismiss('subtitles-timeline', !textGuideDismissed, false);
  const [stylesGuideDismissed, setStylesGuideDismissed] = useGuideDismiss('subtitles-styles', !hasStyles && !timelineGuideDismissed, true);
  const showStylesGuide = !stylesGuideDismissed;
  const showTimelineGuide = stylesGuideDismissed && !timelineGuideDismissed;
  // цель третьей подсказки (блок настроек стиля) есть только при выбранном стиле
  const showTextGuide = stylesGuideDismissed && timelineGuideDismissed && !textGuideDismissed && hasStyles;
  useMarkGuideSeen('subtitles-timeline', showTimelineGuide);
  useMarkGuideSeen('subtitles-text', showTextGuide);

  useScrollGuideIntoView(showTimelineGuide, timelineGuideTargetRef);
  useScrollGuideIntoView(showStylesGuide, stylesGuideTargetRef);
  // блок настроек ниже экрана — к его началу (заголовок и вкладки), не к середине
  useScrollGuideIntoView(showTextGuide, textGuideTargetRef, 'start');

  return (
    <>
      {/* Стили — лентой карточек, как фон: номер на карточке = порядок стиля в пуле */}
      <div ref={stylesGuideTargetRef} className="w12-sec">
        <div className="w12-sec-head">
          <h2><span className="w12-t-it w12-ttl-ic" aria-hidden="true">Т</span><span className="w12-l">{t('wizard.subs.stylesTitle')}</span></h2>
          <div className="w12-side"><span className="w12-pool-note w12-num">{t('wizard.bg.selectedCount', { count: subtitles.pool.length })}</span></div>
        </div>
        <div className="w12-pool w12-pool-fixed">
          {stylesQuery.isLoading ? (
            <div className="w12-rail w12-center"><span className="spinner" aria-hidden="true" /></div>
          ) : queryDown(stylesQuery) ? (
            /* список стилей не пришёл — раньше здесь оставалась пустая полоса без объяснения */
            <div className="w12-rail w12-center">
              <InlineError error={stylesQuery.error} offline={stylesQuery.fetchStatus === 'paused'} onRetry={() => stylesQuery.refetch()} retrying={stylesQuery.isFetching} />
            </div>
          ) : (
            <Rail resetKey="subtitle-styles">
              {(stylesQuery.data?.styles ?? []).map((item) => (
                <MediaCard key={item.id} item={item} format="4:3" caption={item.name} order={subtitles.pool.indexOf(item.name) + 1} onToggle={() => toggleStyle(item.name)} />
              ))}
            </Rail>
          )}
        </div>
      </div>

      {/* Проверка: как ASR разложил слова по треку — подвинуть слово, пометить фокус */}
      <div ref={timelineGuideTargetRef}>
        <SubtitleTimeline />
      </div>

      <ActionGuideOverlay
        open={showTimelineGuide}
        targetRef={timelineGuideTargetRef}
        title={t('wizard.subs.guideFitTitle')}
        text={t('wizard.subs.guideFitText')}
        dismissLabel={t('wizard.subs.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 3 })}
        onDismiss={() => setTimelineGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<SubtitleFitGuideVisual />}
      />

      <SubtitleTextCustomization guideTargetRef={textGuideTargetRef} />

      <ActionGuideOverlay
        open={showStylesGuide}
        targetRef={stylesGuideTargetRef}
        title={t('wizard.subs.guideStyleTitle')}
        text={t('wizard.subs.guideStyleText')}
        dismissLabel={t('wizard.subs.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 3 })}
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
    </>
  );
}

const WHITE_TEXT = '#f6f5fd'; // ui-allow: цвет данных (дефолтный цвет субтитров), не цвет интерфейса

/* Часы плеера тикают каждые 50 мс — на них подписаны только эти два маленьких компонента,
   а не вся правая колонка с видео-фоном и итогом шага. */
function PreviewPlay({ clipStart }: { clipStart: number }) {
  const { t } = useTranslation();
  const time = useSubtitleClock((c) => c.time);
  const playing = useSubtitleClock((c) => c.playing);
  const toggle = useSubtitleClock((c) => c.toggle);
  if (!toggle) return null;
  return (
    <button type="button" className="w12-drop-play" onClick={() => toggle()}
      aria-label={playing ? t('wizard.subs.timeline.pause') : t('wizard.subs.timeline.play')}>
      <span className="w12-dot">{playing ? PAUSE : PLAY}</span>
      <span className="w12-num w12-drop-clock">{clockLabel((time ?? clipStart) - clipStart)}</span>
    </button>
  );
}

function ClockedSubtitles(props: Omit<SubtitleCanvasProps, 'time' | 'rest'>) {
  const time = useSubtitleClock((c) => c.time);
  const playing = useSubtitleClock((c) => c.playing);
  return <SubtitleCanvas {...props} time={props.words.length ? time : null} rest={!playing} />;
}

export function SubtitlesWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const background = useWizardStore((state) => state.background);
  const lyrics = useWizardStore((state) => state.lyrics);
  const fragmentLyrics = useWizardStore((state) => state.fragmentLyrics);
  const backdrop = useBackdrop();
  const previewLyrics = (fragmentLyrics.trim() || lyrics.trim()) || t('wizard.subs.lyricsPlaceholder');
  const tab = activeTextTab(subtitles);
  const textSettings = textSettingsFor(subtitles, tab);
  const tabStyle = tab ? styleIdOf(tab) : null;
  // слова — из «Проверки субтитров» (тайминги и фокус человека); до распознавания — заглушка из текста
  const asr = useWizardStore((state) => state.asr);
  const timed = useMemo(() => (asr.status === 'COMPLETED'
    ? asr.words.map((w) => ({ text: w.text, start: w.tStart, end: w.tEnd, focus: w.focus }))
    : []), [asr.status, asr.words]);
  const [geomError, setGeomError] = useState<string | null>(null);
  const wideFrame = allBackgroundsWide(background);

  return (
    <aside className="w12-col-aside">
      <div className="w12-card w12-pv-card">
        <div className="w12-aside-head">
          <h2>{t('wizard.subs.preview')}</h2>
          {/* тот же плеер, что в «Проверке субтитров»: превью идёт по таймингам слов */}
          {timed.length > 0 && <PreviewPlay clipStart={asr.clipStart ?? 0} />}
        </div>
        {/* кадр — выбранный на «Фоне» (первый футаж/фото или цвет): субтитры видно так, как они лягут */}
        <div className={cn('w12-pv-stage', wideFrame && 'w12-pv-ambient')}>
          {/* размытая подложка — заставка ролика, а не второй экземпляр того же видео */}
          {wideFrame && backdrop.url && (backdrop.isVideo
            ? catalogPosterOf(backdrop.url) && <img className="w12-ambient-bg" src={catalogPosterOf(backdrop.url)!} alt="" aria-hidden="true" />
            : <img className="w12-ambient-bg" src={backdrop.url} alt="" aria-hidden="true" />)}
          <div className={cn('w12-player', wideFrame && 'w12-cine')} style={{ containerType: 'inline-size' }}>
            {backdrop.url && (backdrop.isVideo
              ? <PreviewVideo key={backdrop.url} className="w12-media-el" src={backdrop.url} ignoreLowData />
              : <img className="w12-media-el" src={backdrop.url} alt="" />)}
            {backdrop.color && <div className="w12-media-el" style={{ background: backdrop.color }} />}
            <div className="w12-shade" />
            {tabStyle
              ? <ClockedSubtitles style={tabStyle} settings={textSettings} color={subtitles.color} words={timed} lyrics={previewLyrics}
                  wide={wideFrame} onError={setGeomError} />
              : <div className="w12-empty">{t('wizard.subs.previewPickStyle')}</div>}
            {tabStyle && geomError && <div className="w12-sub-error">{geomError}</div>}
            {tab && <span className="w12-pv-tag">{tab}</span>}
          </div>
        </div>
      </div>

      <PillsFooter
        pills={subtitles.pool.map((name) => ({ key: name, label: name, icon: <span className="w12-t-it" aria-hidden="true">T</span> }))}
        activeKey={tab ?? undefined}
        emptyLabel={t('wizard.subs.footerEmpty')}
        onPill={(name) => setSubtitles({ textTab: name })}
        ready={ready}
        canContinue={canContinue}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
      />
    </aside>
  );
}
