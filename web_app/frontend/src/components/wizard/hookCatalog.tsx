import { CSSProperties, ReactNode, useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useChip } from '../../i18n/useChip';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HOOK_LABELS, HookConfig, HookKind } from '../../stores/wizardStore';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import effectsRegistry from '../../data/effects-registry.json';
import { CatalogMedia } from './CatalogPreview';
import { useDragScroll } from './useDragScroll';

/*
 * Каталог хуков: типы, приёмы, склейки и стили из реестра эффектов, шаги настройки хука,
 * превью приёмов и лента чипов. Отдельный модуль, чтобы шаг FX (HookPanel), лаборатория
 * вариантов (FxLab), фон, «Пул» и монтажный стол брали его без колец импортов между собой.
 */

export const HOOK_TYPES: { kind: HookKind; icon: string; iconW: number; iconH: number; hint: string }[] = [
  { kind: 'none', icon: '/assets/figma/icon-bolt.svg', iconW: 15, iconH: 18, hint: 'wizard.fx.hintNoHook' },
  { kind: 'warmup', icon: '/assets/figma/hook-sound.svg', iconW: 16, iconH: 18, hint: 'wizard.fx.hintSound' },
  { kind: 'object', icon: '/assets/figma/hook-object.svg', iconW: 18, iconH: 18, hint: 'wizard.fx.hintObject' },
  { kind: 'effects', icon: '/assets/figma/hook-effects.svg', iconW: 16, iconH: 17, hint: 'wizard.fx.hintEffects' },
  { kind: 'motion', icon: '/assets/figma/hook-motion.svg', iconW: 16, iconH: 18, hint: 'wizard.fx.hintMotion' },
  { kind: 'thought', icon: '/assets/figma/hook-thought.svg', iconW: 15, iconH: 16, hint: 'wizard.fx.hintThought' }
];

// Точные списки из Figma (W34/W28/W29/W30/W27/W31)
export const OBJECTS = ['Круг', 'Квадрат', 'Ромб', 'Звезда-5', 'Звезда-10'];
// FX-эффекты тянутся из единого реестра effects-registry.json (source of truth):
// добавил эффект в реестр → появляется и чип здесь, и резолв в manifestId на бэке.
export const EFFECT_HOOKS = effectsRegistry.hook.map((e) => e.label);
// «Без склейки» / «Без стилизации» — осознанный отказ, стоят первыми в ленте. На бэке
// (effect_map.NO_GLUE_LABEL/NO_STYLE_LABEL) они НЕ подменяются склейкой/стилем с этапа фона.
export const NO_GLUE = 'Без склейки';
export const NO_STYLE = 'Без стилизации';
export const EFFECT_GLUES = [NO_GLUE, ...effectsRegistry.glue.map((e) => e.label)];
export const EFFECT_STYLES = [NO_STYLE, ...effectsRegistry.style.map((e) => e.label)];
export const MOTIONS = ['Свайп', 'Тап', 'Зум', 'Задержи', 'Голова'];
export const THOUGHTS = ['Панчлайн', 'Пропущенное слово', 'Эхо', 'Вопрос', 'Инверсия'];

const OBJECT_PREVIEW_IDS: Record<string, string> = {
  'Круг': 'shape__elipse',
  'Квадрат': 'shape__square',
  'Ромб': 'shape__rhomb',
  'Звезда-5': 'shape__star2',
  'Звезда-10': 'shape__star1'
};
const MOTION_PREVIEW_IDS: Record<string, string> = {
  'Свайп': 'motion__swipe',
  'Тап': 'motion__tap',
  'Зум': 'motion__pinch',
  'Задержи': 'motion__holdfinger',
  'Голова': 'motion__head'
};

/*
 * Иконки чипов из Figma. baked — SVG уже содержит фиолетовый круг 40×40;
 * inner — только глиф, круг #5f42b9 рисуем в CSS; спец-случаи (Квадрат, Вопрос) — inline.
 */
type ChipIconDef = { src?: string; inner?: boolean; kind?: 'square' | 'question' | 'off'; big?: boolean };
export const CHIP_ICONS: Record<string, ChipIconDef> = {
  // Объекты
  'Круг': { src: '/assets/figma/obj-krug.svg' },
  'Квадрат': { kind: 'square' },
  'Ромб': { src: '/assets/figma/obj-romb.svg' },
  'Звезда-5': { src: '/assets/figma/obj-zvezda5.svg' },
  'Звезда-10': { src: '/assets/figma/obj-zvezda10.svg' },
  // Движение
  'Свайп': { src: '/assets/figma/mot-swipe-inner.svg', inner: true, big: true },
  'Тап': { src: '/assets/figma/mot-tap.svg' },
  'Зум': { src: '/assets/figma/mot-zoom.svg' },
  'Задержи': { src: '/assets/figma/mot-hold.svg' },
  'Голова': { src: '/assets/figma/mot-head-inner.svg', inner: true },
  // Эффекты (hook/glue/style) — иконки подмешиваются из реестра ниже
  // Мысль
  'Панчлайн': { src: '/assets/figma/thg-punchline.svg' },
  'Пропущенное слово': { src: '/assets/figma/thg-missing-inner.svg', inner: true },
  'Эхо': { src: '/assets/figma/thg-echo-inner.svg', inner: true },
  'Вопрос': { kind: 'question' },
  'Инверсия': { src: '/assets/figma/thg-inversion-inner.svg', inner: true },
  // Отказ от склейки/стилизации
  [NO_GLUE]: { kind: 'off' },
  [NO_STYLE]: { kind: 'off' }
};

// FX-иконки (hook/glue/style) — из единого реестра: один эффект = одна запись в effects-registry.json
for (const group of ['hook', 'glue', 'style'] as const) {
  for (const e of effectsRegistry[group]) {
    CHIP_ICONS[e.label] = e.inner ? { src: `/assets/figma/${e.icon}`, inner: true } : { src: `/assets/figma/${e.icon}` };
  }
}

export function ChipIcon({ label }: { label: string }) {
  const def = CHIP_ICONS[label];
  if (!def) return null;
  if (def.kind === 'off') {
    return (
      <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-accent" aria-hidden="true">
        <svg viewBox="0 0 20 20" width="20" height="20" fill="none"><circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.8" className="text-text" /><path d="M5 15 15 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className="text-text" /></svg>
      </span>
    );
  }
  if (def.kind === 'square') {
    return (
      <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-accent" aria-hidden="true">
        <span className="h-[20px] w-[20px] rounded-[2px] bg-text" />
      </span>
    );
  }
  if (def.kind === 'question') {
    // «?» сидит на 1px ниже центра — компенсация метрики (правка ревью)
    return (
      <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-accent" aria-hidden="true">
        <em className="font-bold italic text-[24px] leading-none text-text">?</em>
      </span>
    );
  }
  if (def.inner) {
    // big (Свайп): крупный глиф прижат к правому нижнему углу круга (Figma 646:3479)
    if (def.big) {
      // inline-block обязателен: у inline-спана w/h игнорируются, контейнинг-блок для
      // абсолютного глифа схлопывается в 0 и preflight `img{max-width:100%}` даёт width:0
      return (
        <span className="relative inline-block h-[40px] w-[40px] shrink-0 overflow-hidden rounded-full bg-accent" aria-hidden="true">
          <img src={def.src} width="27" height="30" alt="" className="absolute bottom-[3px] right-[3px] h-[30px] w-[27px] object-contain" />
        </span>
      );
    }
    return (
      <span className="flex h-[40px] w-[40px] shrink-0 items-center justify-center overflow-hidden rounded-full bg-accent" aria-hidden="true">
        <img src={def.src} width="20" height="20" alt="" className="h-[20px] w-[20px] object-contain" />
      </span>
    );
  }
  return <img src={def.src} width="40" height="40" alt="" className="h-[40px] w-[40px] shrink-0" aria-hidden="true" />;
}

/**
 * Настройка ЛЮБОГО хука — это три шага: сам хук → склейка → стиль.
 *
 * Раньше трёхшаговый мастер был только у «Эффектов», а остальные типы показывали один
 * список и считались настроенными — склейку и стиль им можно было выбрать только в
 * фуллскрине, куда доходили не все. Определение шагов теперь одно на оба режима:
 * разъехаться им больше негде.
 *
 * `options: []` — шаг не про выбор из списка (загрузка своего звука).
 */
export interface HookStep {
  key: keyof HookConfig;
  title: string;
  options: string[];
}

const GLUE_STEP: HookStep = { key: 'effectGlue', title: 'wizard.fx.stepGlue', options: EFFECT_GLUES };
const STYLE_STEP: HookStep = { key: 'effectStyle', title: 'wizard.fx.stepStyle', options: EFFECT_STYLES };

/** Стили, которые манифест всегда тянет на весь ролик — у них выбора нет. */
const FULL_WINDOW_STYLES = new Set(effectsRegistry.style.filter((e) => e.fullWindow).map((e) => e.label));
/** Стиль всегда идёт на весь ролик (ЧБ и т.п.): «До дропа» для него — обещание, которого рендер не выполнит. */
export const styleLocksFullWindow = (style?: string) => Boolean(style && FULL_WINDOW_STYLES.has(style));

/**
 * Охват грейда: до дропа (по умолчанию) или на весь ролик — то же, что спрашивает бот
 * (effect_extra_full). Живёт строкой в шапке шага «Стилизация», рядом с его заголовком.
 */
export function StyleScopeToggle({ config, onPick }: { config: HookConfig; onPick: (full: boolean) => void }) {
  const { t } = useTranslation();
  const locked = styleLocksFullWindow(config.effectStyle);
  const full = locked || Boolean(config.effectStyleFull);
  // Живёт в строке заголовка шага, без подписи: два сегмента объясняют себя сами, а
  // лишнее слово съедало название шага в узкой рабочей зоне.
  return (
    <span className="flex shrink-0 items-center whitespace-nowrap" aria-label={t('wizard.fx.scopeLabel')}>
      <span className="inline-flex items-center gap-[3px] rounded-r15 bg-[rgba(8,3,19,.5)] p-[3px]" title={locked ? t('wizard.fx.scopeLocked') : undefined}>
        {([[false, t('wizard.fx.scopeDrop')], [true, t('wizard.fx.scopeFull')]] as const).map(([value, label]) => (
          <button
            key={String(value)}
            type="button"
            aria-pressed={full === value}
            disabled={locked && value === false}
            onClick={() => onPick(value)}
            className={cn(
              'flex h-[28px] items-center justify-center whitespace-nowrap rounded-[9px] px-[7px] text-[11px] transition disabled:cursor-not-allowed disabled:opacity-40',
              full === value ? 'bg-grad-soft-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-60 hover:text-text'
            )}
          >
            {label}
          </button>
        ))}
      </span>
    </span>
  );
}

const SLOW_EXTEND_OPTIONS = [
  ['', 'wizard.fx.slowStandard'],
  ['to_end', 'wizard.fx.slowToEnd'],
  ['after_drop:3', 'wizard.fx.slowThree']
] as const;

/** Дополнительная длина echo-шлейфа доступна только выбранному slow shutter. */
export function SlowShutterExtendToggle({ config, onPick }: { config: HookConfig; onPick: (value: HookConfig['effectHookExtend']) => void }) {
  const { t } = useTranslation();
  const value = config.effectHookExtend ?? '';
  return (
    <span className="inline-flex shrink-0 items-center gap-[3px] rounded-r15 bg-[rgba(8,3,19,.5)] p-[3px]" aria-label={t('wizard.fx.slowLength')}>
      {SLOW_EXTEND_OPTIONS.map(([option, label]) => (
        <button
          key={option || 'standard'}
          type="button"
          aria-pressed={value === option}
          onClick={() => onPick(option)}
          className={cn(
            'flex h-[28px] items-center justify-center whitespace-nowrap rounded-[9px] px-[7px] text-[11px] transition',
            value === option ? 'bg-grad-soft-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-60 hover:text-text'
          )}
        >
          {t(label)}
        </button>
      ))}
    </span>
  );
}

export function selectedStyles(config: HookConfig): string[] {
  return config.effectStyles?.length ? config.effectStyles : (config.effectStyle ? [config.effectStyle] : []);
}

export function toggleStyle(config: HookConfig, option?: string): Partial<HookConfig> {
  if (!option) return {};
  const current = selectedStyles(config);
  const next = current.includes(option) ? current.filter((style) => style !== option) : [...current, option];
  return { effectStyles: next, effectStyle: next.includes(option) ? option : next[next.length - 1] };
}

/** Первый шаг зависит от типа хука, два следующих общие. */
const FIRST_STEP: Record<Exclude<HookKind, 'none'>, HookStep> = {
  warmup: { key: 'sound', title: 'wizard.fx.loadSound', options: [] },
  object: { key: 'object', title: 'wizard.fx.chooseObject', options: OBJECTS },
  effects: { key: 'effectHook', title: 'wizard.fx.stepFx', options: EFFECT_HOOKS },
  motion: { key: 'motion', title: 'wizard.fx.chooseMotion', options: MOTIONS },
  thought: { key: 'thought', title: 'wizard.fx.chooseThought', options: THOUGHTS }
};

export function hookSteps(kind: HookKind): HookStep[] {
  if (kind === 'none') return [GLUE_STEP, STYLE_STEP];
  return [FIRST_STEP[kind], GLUE_STEP, STYLE_STEP];
}

/** Stable S3 catalog id for the exact option edited at this step. */
export function previewIdFor(key: keyof HookConfig, value?: string): string | undefined {
  if (!value) return undefined;
  if (key === 'object') return OBJECT_PREVIEW_IDS[value];
  if (key === 'motion') return MOTION_PREVIEW_IDS[value];
  const group = key === 'effectHook' ? 'hook' : key === 'effectGlue' ? 'glue' : key === 'effectStyle' ? 'style' : null;
  if (!group) return undefined;
  const item = effectsRegistry[group].find((entry) => entry.label === value);
  const prefix = key === 'effectHook' ? 'effect_hook' : key === 'effectGlue' ? 'effect_transition' : 'effect_extra';
  return item ? `${prefix}__${item.manifestId}` : undefined;
}

export function configuredPreviewId(kind: HookKind, config: HookConfig, active?: HookStep): string | undefined {
  if (active) {
    const selected = previewIdFor(active.key, config[active.key] as string | undefined);
    if (selected) return selected;
  }
  return hookSteps(kind)
    .map((step) => previewIdFor(step.key, config[step.key] as string | undefined))
    .find(Boolean);
}


/*
 * Ряд чипов со скролл-фейдами по краям.
 * Края (правка заказчика): лента тает у краёв. Раньше поверх пилюль клали цветной градиент
 * «в тон фона» — но фон под лентой не однотонный (пилюли, границы), поэтому фейд всегда «не в
 * тон». Решение: маскируем сам скролл-контейнер (mask-image) — пилюли уходят в НАСТОЯЩУЮ
 * прозрачность, сквозь них виден реальный фон → всегда в тон, при любом фоне.
 * Слева фейд у 0; справа встаёт перед кнопкой подтверждения (`rightGap`).
 */
export function ChipRow({ options, value, values, onPick, rightGap = 0, edgePad = 0 }: {
  options: string[];
  value?: string;
  values?: string[];
  onPick: (option?: string) => void;
  /** ширина зоны под кнопкой справа (кнопка + зазор): лента прокручивается под неё, фейд встаёт перед */
  rightGap?: number;
  /** внутренний отступ ленты от краёв (когда контейнер без горизонтального padding): фейды остаются у краёв контейнера */
  edgePad?: number;
}) {
  const chip = useChip();
  const scroll = useDragScroll();
  const [fade, setFade] = useState({ left: false, right: false });
  const syncFades = () => {
    const el = scroll.ref.current;
    if (!el) return;
    setFade({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  };
  useEffect(() => {
    syncFades();
    const el = scroll.ref.current;
    if (!el) return;
    const observer = new ResizeObserver(syncFades);
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.join(',')]);
  // Выбор поменялся (в т.ч. стрелками над примером) — лента доезжает до выбранной пилюли,
  // иначе выбранное оставалось за краем и казалось, что ничего не выбралось
  useEffect(() => {
    const el = scroll.ref.current;
    const pill = el?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!el || !pill) return;
    const at = pill.getBoundingClientRect().left - el.getBoundingClientRect().left + el.scrollLeft;
    el.scrollTo({ left: Math.max(0, at - (el.clientWidth - pill.offsetWidth) / 2), behavior: 'smooth' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  // Края тают только там, где за ними есть пилюли; справа фейд встаёт перед зоной кнопки (rightGap)
  return (
    <div
      ref={scroll.ref}
      className="w12-chiprow"
      data-fade-l={fade.left || undefined}
      data-fade-r={fade.right || undefined}
      style={{ paddingLeft: edgePad, paddingRight: rightGap + edgePad, '--rg': `${rightGap}px` } as CSSProperties}
      onScroll={syncFades}
      {...scroll.handlers}
    >
      {options.map((option) => {
        const selected = values ? values.includes(option) : value === option;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={selected}
            className="w12-pill w12-with-ic"
            onClick={() => { if (!scroll.moved()) onPick(selected && !values ? undefined : option); }}
          >
            <span className="w12-gicon" aria-hidden="true"><span className="w12-gicon-scale"><ChipIcon label={option} /></span></span>
            <span className="w12-l">{chip(option)}</span>
          </button>
        );
      })}
    </div>
  );
}


/**
 * Каталог превью эффектов почти не меняется, а presigned-ссылки в нём новые на каждый
 * запрос — частый refetch заставлял видео примеров скачиваться заново. Держим 6 ч.
 */
export const FX_PREVIEWS_STALE_MS = 6 * 60 * 60_000;

/** The selected hook/shape/motion sample is already rendered and stored in S3. */
export function EffectPreview({ previewId }: { previewId?: string }) {
  const query = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews, staleTime: FX_PREVIEWS_STALE_MS });
  if (!previewId || query.isLoading) return null;
  const effect = query.data?.previews.find(item => item.id === previewId);
  return <CatalogMedia url={effect?.previewUrl} className="absolute inset-0 h-full w-full" />;
}

/**
 * Строка типа хука: иконка · название (кнопка, растянутая на всю строку) · «?» · хвост
 * (метки вариантов, стрелка). «?» — кнопка-соседка, а не span внутри кнопки: так она
 * достаётся с клавиатуры и работает и у заблокированной строки (без дропа).
 */
export function HookTypeHead({ item, locked, pressed, expanded, onToggle, hintOpen, onHint, children }: {
  item: (typeof HOOK_TYPES)[number];
  locked: boolean;
  pressed?: boolean;
  expanded?: boolean;
  onToggle: () => void;
  hintOpen: boolean;
  onHint: (open: boolean) => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const chip = useChip();
  const hintId = useId();
  const label = chip(HOOK_LABELS[item.kind]);
  return (
    <>
      <div className="w12-fx-head">
        <SvgMaskIcon src={item.icon} className="w12-fx-ic" style={{ width: item.iconW, height: item.iconH }} />
        <button type="button" disabled={locked} className="w12-fx-toggle" aria-pressed={pressed} aria-expanded={expanded} onClick={onToggle}>
          <span className="w12-fx-name w12-l">{label}</span>
        </button>
        <button
          type="button"
          className="w12-help-dot w12-fx-help"
          aria-label={t('wizard.fx.whatIs', { label })}
          aria-expanded={hintOpen}
          aria-controls={hintOpen ? hintId : undefined}
          onMouseEnter={() => onHint(true)}
          onMouseLeave={() => onHint(false)}
          onClick={() => onHint(!hintOpen)}
          onKeyDown={(e) => { if (e.key === 'Escape') onHint(false); }}
        >
          <span className="w12-l">?</span>
        </button>
        {children}
      </div>
      {hintOpen && <span id={hintId} role="tooltip" className="w12-fx-hint">{t(item.hint)}</span>}
    </>
  );
}
