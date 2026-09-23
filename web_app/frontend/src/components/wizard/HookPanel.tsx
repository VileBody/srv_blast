import { WarmupInput } from './WarmupInput';
import { ChangeEvent, Fragment, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useChip } from '../../i18n/useChip';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { ArrowRight, useDragScroll } from './BackgroundPanel';
import { FullscreenZone } from '../ui/FullscreenZone';
import { PillsFooter } from './WizardFrame';
import { HookConfig, HookKind, HOOK_LABELS, hookComplete, hookPills, useWizardStore } from '../../stores/wizardStore';
import effectsRegistry from '../../data/effects-registry.json';
import { CatalogMedia } from './CatalogPreview';
import { dropToSeconds, normalizeDropTime, timingToSeconds } from './useFragmentAudio';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';

/*
 * Этап «Хук» (Figma W18 → W24/32 → W25/34 → W26/28/29/30 → W27 → W31):
 * единый тайминг дропа (чипы во всю высоту панели), строки типов 620×80 со скроллом
 * под градиентный оверлей, «?»-подсказки плашками внутри строк (тексты из макета),
 * настройка в рабочей зоне; «Эффекты» — шаги с подтверждением галочкой.
 */

const HOOK_TYPES: { kind: HookKind; icon: string; iconW: number; iconH: number; hint: string }[] = [
  { kind: 'none', icon: '/assets/figma/icon-bolt.svg', iconW: 15, iconH: 18, hint: 'wizard.fx.hintNoHook' },
  { kind: 'warmup', icon: '/assets/figma/hook-sound.svg', iconW: 16, iconH: 18, hint: 'wizard.fx.hintSound' },
  { kind: 'object', icon: '/assets/figma/hook-object.svg', iconW: 18, iconH: 18, hint: 'wizard.fx.hintObject' },
  { kind: 'effects', icon: '/assets/figma/hook-effects.svg', iconW: 16, iconH: 17, hint: 'wizard.fx.hintEffects' },
  { kind: 'motion', icon: '/assets/figma/hook-motion.svg', iconW: 16, iconH: 18, hint: 'wizard.fx.hintMotion' },
  { kind: 'thought', icon: '/assets/figma/hook-thought.svg', iconW: 15, iconH: 16, hint: 'wizard.fx.hintThought' }
];

// Точные списки из Figma (W34/W28/W29/W30/W27/W31)
const OBJECTS = ['Круг', 'Квадрат', 'Ромб', 'Звезда-5', 'Звезда-10'];
// FX-эффекты тянутся из единого реестра effects-registry.json (source of truth):
// добавил эффект в реестр → появляется и чип здесь, и резолв в manifestId на бэке.
const EFFECT_HOOKS = effectsRegistry.hook.map((e) => e.label);
// «Без склейки» / «Без стилизации» — осознанный отказ, стоят первыми в ленте. На бэке
// (effect_map.NO_GLUE_LABEL/NO_STYLE_LABEL) они НЕ подменяются склейкой/стилем с этапа фона.
export const NO_GLUE = 'Без склейки';
export const NO_STYLE = 'Без стилизации';
const EFFECT_GLUES = [NO_GLUE, ...effectsRegistry.glue.map((e) => e.label)];
const EFFECT_STYLES = [NO_STYLE, ...effectsRegistry.style.map((e) => e.label)];
const MOTIONS = ['Свайп', 'Тап', 'Зум', 'Задержи', 'Голова'];
const THOUGHTS = ['Панчлайн', 'Пропущенное слово', 'Эхо', 'Вопрос', 'Инверсия'];

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
        <em className="mt-[1px] font-bold italic text-[24px] leading-none text-text">?</em>
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

/**
 * Охват грейда: до дропа (по умолчанию) или на весь ролик — то же, что спрашивает бот
 * (effect_extra_full). Живёт строкой в шапке шага «Стилизация», рядом с его заголовком.
 */
export function StyleScopeToggle({ config, onPick }: { config: HookConfig; onPick: (full: boolean) => void }) {
  const { t } = useTranslation();
  const locked = Boolean(config.effectStyle && FULL_WINDOW_STYLES.has(config.effectStyle));
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

function selectedStyles(config: HookConfig): string[] {
  return config.effectStyles?.length ? config.effectStyles : (config.effectStyle ? [config.effectStyle] : []);
}

function toggleStyle(config: HookConfig, option?: string): Partial<HookConfig> {
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
function previewIdFor(key: keyof HookConfig, value?: string): string | undefined {
  if (!value) return undefined;
  if (key === 'object') return OBJECT_PREVIEW_IDS[value];
  if (key === 'motion') return MOTION_PREVIEW_IDS[value];
  const group = key === 'effectHook' ? 'hook' : key === 'effectGlue' ? 'glue' : key === 'effectStyle' ? 'style' : null;
  if (!group) return undefined;
  const item = effectsRegistry[group].find((entry) => entry.label === value);
  const prefix = key === 'effectHook' ? 'effect_hook' : key === 'effectGlue' ? 'effect_transition' : 'effect_extra';
  return item ? `${prefix}__${item.manifestId}` : undefined;
}

function configuredPreviewId(kind: HookKind, config: HookConfig, active?: HookStep): string | undefined {
  if (active) {
    const selected = previewIdFor(active.key, config[active.key] as string | undefined);
    if (selected) return selected;
  }
  return hookSteps(kind)
    .map((step) => previewIdFor(step.key, config[step.key] as string | undefined))
    .find(Boolean);
}

function maskTiming(raw: string): string {
  const digits = raw.replace(/\D/g, '').slice(0, 6);
  return digits.replace(/(\d{2})(?=\d)/g, '$1:');
}

/** Свой тайминг дропа не может превышать длительность трека (правка ревью) */
function clampDrop(value: string, durationS?: number): string {
  const masked = maskTiming(value);
  if (!durationS) return masked;
  const m = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(masked);
  if (!m) return masked;
  const sec = Number(m[1]) * 60 + Number(m[2]) + Number(m[3] ?? 0) / 100;
  if (sec <= durationS) return masked;
  const mm = String(Math.floor(durationS / 60)).padStart(2, '0');
  const ss = String(Math.floor(durationS % 60)).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** Мини-визуал первой подсказки хука: тайминг дропа — точка на дорожке. */
/**
 * Переиспользует визуальный язык самой первой подсказки (тайминг отрывка на треке):
 * два тайминг-пила + тонкая шкала. Волну убрал — не читалась. Здесь не диапазон,
 * а ОДНА точка (момент дропа) — вместо отдельной палочки+подписи сам пил «drop»
 * сидит прямо на шкале и служит ручкой: он ближе к началу и он же дёргается
 * (тот же принцип, что и в тайминге трека: анимируем ровно то, что тянут).
 */
function HookDropGuideVisual() {
  return (
    <div className="flex w-full items-center justify-center gap-[9px]" aria-hidden="true">
      <span className="flex h-[38px] w-[58px] shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] bg-white/[0.1] text-[15px] font-[400] leading-none text-white">
        <span className="action-guide-optical-text action-guide-timing-text">00:14</span>
      </span>
      <span className="relative h-[4px] min-w-0 flex-1 rounded-full bg-white/25">
        <span className="guide-track-drop-pill action-guide-optical-text absolute left-[26%] top-1/2 whitespace-nowrap rounded-[5px] bg-accent-light px-[7px] py-[3px] text-[8px] font-[400] text-white">drop</span>
      </span>
      <span className="flex h-[38px] w-[58px] shrink-0 items-center justify-center whitespace-nowrap rounded-[8px] bg-white/[0.1] text-[15px] font-[400] leading-none text-white">
        <span className="action-guide-optical-text action-guide-timing-text">00:20</span>
      </span>
    </div>
  );
}

/** Мини-визуал третьей подсказки хука: превью слева, «настройки» строчками справа. */
/** Точь-в-точь композиция SubtitleStyleGuideVisual (3 карточки, средняя выбрана),
 * только вместо иконки «T» — иконка стиля: и там, и там про выбор варианта из ряда. */
function HookWorkzoneGuideVisual() {
  // Три РАЗНЫЕ иконки — ровно то, что перечислено в тексте подсказки (склейка/стиль/превью),
  // а не одна и та же картинка трижды.
  const icons = ['/assets/figma/combo-transition.svg', '/assets/figma/combo-style.svg', '/assets/figma/btn-play.svg'];
  return (
    <div className="grid w-full grid-cols-3 gap-[7px]" aria-hidden="true">
      {icons.map((src, index) => (
        <span
          key={src}
          className={cn(
            'guide-mode-reveal relative flex h-[46px] items-center justify-center overflow-hidden rounded-[8px] bg-gradient-to-b from-[#42335e] to-[#181126]',
            index === 0 ? 'guide-mode-delay-1' : index === 1 ? 'guide-mode-delay-2' : 'guide-mode-delay-3',
            index === 1 && 'ring-2 ring-inset ring-accent-light'
          )}
        >
          <img src={src} width="18" height="18" alt="" className="opacity-90" />
        </span>
      ))}
    </div>
  );
}

/**
 * Мини-визуал «Выбери тип хука»: центральный пил — сплошной (не полупрозрачный,
 * иначе боковые пилы под ним просвечивают) и крутит ТРИ иконки кросс-фейдом
 * (guide-hook-icon-a/b/c, один кадр-keyframe + отрицательные animation-delay —
 * без дублирования кейфреймов), показывая, что слот подставляет РАЗНЫЕ типы хука.
 */
function HookTypeGuideVisual() {
  return (
    <div className="flex w-full items-center justify-center" aria-hidden="true">
      <span className="guide-mode-reveal guide-mode-delay-1 relative z-[1] mr-[-14px] flex h-[52px] w-[44px] shrink-0 items-center justify-center rounded-[11px] bg-white/[0.06] opacity-55">
        <SvgMaskIcon src="/assets/figma/hook-sound.svg" style={{ width: 12, height: 14, color: 'rgba(255,255,255,.7)' }} />
      </span>
      {/*
        Три слоя, каждый со своим transform-заданием (иначе бегущая анимация просто
        затирает предыдущую на том же свойстве):
        1) внешний — только позиционирование в ряду + одноразовое появление;
        2) средний — ВИДИМЫЙ пил (фон/тень/рамка) — это он трясётся, не иконка;
        3) внутренний слой иконок — только opacity-кроссфейд, transform не трогает.
      */}
      <span className="guide-mode-reveal guide-mode-delay-2 relative z-[2] flex h-[66px] w-[60px] shrink-0 items-center justify-center">
        <span className="guide-hook-shake relative flex h-full w-full items-center justify-center overflow-hidden rounded-[14px] bg-[#6850b7] shadow-[0_10px_22px_rgba(5,1,15,.4),inset_0_0_0_1.5px_var(--accent-light)]">
          <span className="guide-hook-icon-a absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 15, height: 18, color: '#fff' }} />
          </span>
          <span className="guide-hook-icon-b absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/hook-object.svg" style={{ width: 20, height: 20, color: '#fff' }} />
          </span>
          <span className="guide-hook-icon-c absolute inset-0 flex items-center justify-center">
            <SvgMaskIcon src="/assets/figma/hook-effects.svg" style={{ width: 20, height: 20, color: '#fff' }} />
          </span>
        </span>
      </span>
      <span className="guide-mode-reveal guide-mode-delay-3 relative z-[1] ml-[-14px] flex h-[52px] w-[44px] shrink-0 items-center justify-center rounded-[11px] bg-white/[0.06] opacity-55">
        <SvgMaskIcon src="/assets/figma/hook-thought.svg" style={{ width: 12, height: 13, color: 'rgba(255,255,255,.7)' }} />
      </span>
    </div>
  );
}

export function StageHooks() {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const clearHook = useWizardStore((state) => state.clearHook);
  const track = useWizardStore((state) => state.track);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const dropGuideTargetRef = useRef<HTMLDivElement>(null);
  const typeGuideTargetRef = useRef<HTMLDivElement>(null);
  // typeGuideDismissed объявлен первым: idle-условие drop-гайда («мы ещё не ушли
  // дальше по цепочке») на него ссылается. visible=false у type: пререквизит
  // «drop уже закрыт» тут не собрать (dropGuideDismissed объявлен НИЖЕ) — иначе
  // при уже заданном dropTime (у юзера с готовой задачей) оба гайда, drop и
  // type, всплыли бы разом — раньше их разводило только значение dropTime
  // (пусто/не пусто), теперь оба не гейтятся задачей и без явной
  // последовательности пересекаются. Показ отмечаем отдельно после
  // showTypeGuide (см. ниже), когда dropGuideDismissed уже посчитан.
  const [typeGuideDismissed, setTypeGuideDismissed] = useGuideDismiss('hook-type', Boolean(hooks.dropTime) && !hooks.kind, false);
  const [dropGuideDismissed, setDropGuideDismissed] = useGuideDismiss('hook-drop', !hooks.dropTime && !typeGuideDismissed, true);
  const showDropGuide = !dropGuideDismissed;
  const showTypeGuide = Boolean(hooks.dropTime) && dropGuideDismissed && !typeGuideDismissed;
  useMarkGuideSeen('hook-type', Boolean(hooks.dropTime) && dropGuideDismissed);
  useScrollGuideIntoView(showDropGuide, dropGuideTargetRef);
  useScrollGuideIntoView(showTypeGuide, typeGuideTargetRef);
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: 15_000 });
  // Окно отрывка — часть ключа: выбрал другой кусок трека → другие кандидаты дропа.
  // Сохранённый режим тайминга может быть старым, поэтому готовность определяют сами
  // валидные границы — ровно те значения, которые отправляются в API.
  const clipFromS = timingToSeconds(timingFrom);
  const clipToS = timingToSeconds(timingTo);
  const clipReady = clipFromS !== null && clipToS !== null && clipToS > clipFromS;
  const dropsQuery = useQuery({
    queryKey: ['drops', track?.id, timingFrom, timingTo],
    queryFn: () => api.drops(track!.id, timingFrom, timingTo),
    enabled: meQuery.isSuccess && Boolean(meQuery.data.capabilities?.analyzedDrops) && Boolean(track) && clipReady,
  });
  const [customDrop, setCustomDrop] = useState(false);
  const [dropError, setDropError] = useState(false);
  const [hint, setHint] = useState<HookKind | null>(null);

  // Кандидаты ВНЕ отрывка не предлагаем: выбрав такой, человек упирался в неактивное
  // «Продолжить» без объяснения (dropReady в визарде требует дроп внутри окна).
  const drops = (dropsQuery.data?.drops ?? []).filter((d) => {
    const s = dropToSeconds(normalizeDropTime(d.time));
    return s !== null && clipFromS !== null && clipToS !== null && s >= clipFromS && s <= clipToS;
  });
  // Сохранённый дроп вылетел из окна (окно поменяли после выбора) — говорим об этом сразу
  const storedDropS = dropToSeconds(hooks.dropTime);
  const storedDropOutside = storedDropS !== null && clipReady && (storedDropS < clipFromS! || storedDropS > clipToS!);
  // В сторе тайминг всегда трёхчастный, в списке — «mm:ss»: сравниваем в одной форме
  const customActive = Boolean(hooks.dropTime && !drops.some((d) => normalizeDropTime(d.time) === hooks.dropTime));
  // Пока анализ идёт, ряд занимают заглушки: иначе человек видит один «Свой вариант»
  // и уходит вписывать тайминг руками, не дождавшись кандидатов.
  const dropsLoading = drops.length === 0 && clipReady && dropsQuery.isFetching;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-space-4">
        <h2 className="wizard-h flex items-center gap-space-3">
          <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 15, height: 21, color: 'var(--accent-light)' }} />
          {t('wizard.fx.title')}
        </h2>
        <span className="wizard-body">{t('wizard.fx.chooseDrop')}</span>
      </div>

      {/*
        Пока кандидатов нет, ряд состоит из одной кнопки «свой тайминг», и это
        читается как сломанный экран. Строка ниже объясняет, ЧТО происходит:
        считаем / нужен отрывок / анализ не удался — во всех случаях руками
        тайминг ввести можно, и это надо сказать вслух.
      */}
      {drops.length === 0 && (
        <p className="mt-[20px] shrink-0 text-[16px] leading-[1.3] text-text-60">
          {!clipReady
            ? t('wizard.fx.dropNeedsClip')
            : dropsQuery.isFetching
              ? t('wizard.fx.dropAnalyzing')
              : t('wizard.fx.dropManualOnly')}
        </p>
      )}

      {/* Тайминг дропа (Figma 606:217): панель 620×60, активный чип — пил во всю высоту */}
      <div ref={dropGuideTargetRef} className="mt-[20px] flex h-[60px] shrink-0 items-stretch rounded-r15 bg-grad-soft-10 max-md:mt-[12px] max-md:h-[48px]">
        {dropsLoading && [0, 1, 2].map((index) => (
          <span key={index} className="flex h-full flex-1 items-center justify-center" aria-hidden="true">
            <span className="h-[26px] w-[92px] animate-pulse rounded-[8px] bg-accent-20" />
          </span>
        ))}
        {drops.map((drop) => (
          <button
            key={drop.time}
            type="button"
            className={cn(
              'flex h-full min-w-0 flex-1 items-center justify-center rounded-r15 text-[24px] font-[350] text-text-80 transition hover:text-text max-xl:text-[17px] max-md:px-[2px] max-md:text-[13px]',
              hooks.dropTime === normalizeDropTime(drop.time) && 'border-2 border-accent-light bg-grad-soft-20 !text-text'
            )}
            onClick={() => { setDropError(false); setCustomDrop(false); setHooks({ dropTime: normalizeDropTime(drop.time) }); }}
          >
            {/* глиф Point сидит выше геометрического центра пила */}
            <span className="translate-y-[1px] whitespace-nowrap">
              {drop.time}
              <small className="ml-2 text-xs opacity-70 max-md:hidden">{Math.round(drop.confidence * 100)}%{drop.best ? ' ★' : ''}</small>
              {/* телефон: без процентов — звезда справа от лучшего тайминга, в той же строке */}
              {drop.best && <small className="ml-[3px] hidden text-[10px] text-accent-light max-md:inline">★</small>}
            </span>
          </button>
        ))}
        {customDrop ? (
          <input
            autoFocus
            className="soft-input !h-full flex-[1.4] !w-auto max-md:min-w-0 max-md:flex-1 max-md:!text-[13px]"
            placeholder="00:00:00"
            defaultValue={customActive ? hooks.dropTime : ''}
            onChange={(e: ChangeEvent<HTMLInputElement>) => { e.target.value = clampDrop(e.target.value, track?.durationS); }}
            onBlur={(e) => {
              const value = clampDrop(e.target.value, track?.durationS);
              // Пустое поле — осознанный сброс дропа (стереть и начать заново), а не опечатка:
              // раньше пустое значение просто игнорировалось и старый дроп молча оставался
              // в сторе — стереть тайминг из интерфейса было нечем.
              if (!value) {
                setDropError(false);
                setHooks({ dropTime: '' });
                setCustomDrop(false);
                return;
              }
              const seconds = dropToSeconds(value);
              const valid = seconds !== null && clipFromS !== null && clipToS !== null && seconds >= clipFromS && seconds <= clipToS;
              setDropError(!valid);
              if (valid) setHooks({ dropTime: value });
              setCustomDrop(false);
            }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          />
        ) : (
          <button
            type="button"
            className={cn(
              'flex h-full flex-[1.4] items-center justify-center rounded-r15 text-[24px] font-[350] text-text-80 transition hover:text-text max-xl:text-[17px] max-md:flex-1 max-md:text-[14px]',
              customActive && 'border-2 border-accent-light bg-grad-soft-20 !text-text'
            )}
            onClick={() => setCustomDrop(true)}
          >
            <span className="translate-y-[1px] whitespace-nowrap">{customActive ? hooks.dropTime : <><span className="max-md:hidden">{t('wizard.fx.customDrop')}</span><span className="hidden max-md:inline">{t('wizard.fx.customDropShort')}</span></>}</span>
          </button>
        )}
      </div>

      {(dropError || storedDropOutside) && <p className="mt-[8px] shrink-0 text-[14px] leading-[1.3] text-[var(--warning)]">{t('wizard.fx.dropOutsideClip')}</p>}

      <ActionGuideOverlay
        open={showDropGuide}
        targetRef={dropGuideTargetRef}
        title={t('wizard.fx.guideDropTitle')}
        text={t('wizard.fx.guideDropText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 3 })}
        onDismiss={() => setDropGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookDropGuideVisual />}
      />

      <p className="wizard-body mt-[28px] shrink-0 max-md:mt-[16px]">{t('wizard.fx.chooseType')}</p>

      {/* Список типов: строки 620×80, скролл уходит под градиентные фейды (Figma Rectangle 771/772) */}
      <div ref={typeGuideTargetRef} className="relative mt-[12px] min-h-0 flex-1 max-md:mt-[8px]">
        <div className="no-scrollbar flex h-full flex-col gap-[20px] overflow-y-auto py-[16px] max-md:gap-[10px] max-md:py-0" style={{ maskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)', WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)' }}>
          {HOOK_TYPES.map((item) => {
            const active = hooks.kind === item.kind;
            const configured = hookPills(hooks).some((pill) => pill.kind === item.kind);
            // «Без хука» не использует дроп, поэтому его можно настроить сразу.
            const locked = !hooks.dropTime && item.kind !== 'none';
            return (
              <button
                key={item.kind}
                type="button"
                disabled={locked}
                className={cn(
                  'relative flex h-[80px] shrink-0 items-center rounded-r15 bg-grad-soft-10 px-[28px] text-left transition hover:bg-grad-soft-20 hover:shadow-[inset_0_0_0_1px_rgba(139,111,230,.55)] max-md:h-[52px] max-md:px-[16px]',
                  // Подсвечены все настроенные типы, а не только открытый (правка ревью)
                  (active || configured) && 'border-2 border-accent-light',
                  active && 'bg-grad-soft-20',
                  // Только типы, которым действительно нужен дроп, ждут его тайминг.
                  locked && 'cursor-not-allowed opacity-45'
                )}
                // Повторный клик по выбранному/настроенному типу снимает его: раньше хук,
                // раз попав в подсветку, отцепиться уже не мог и уезжал в генерацию.
                onClick={() => { if (active || configured) clearHook(item.kind); else setHooks({ kind: item.kind }); }}
                aria-pressed={active || configured}
              >
                <SvgMaskIcon src={item.icon} style={{ width: item.iconW, height: item.iconH, color: active || configured ? 'var(--accent-light)' : 'var(--text-80)' }} />
                <span className="wizard-body ml-space-4 !text-text">
                  {chip(HOOK_LABELS[item.kind])}
                </span>
                {/* Подсказка-плашка внутри строки (Figma Group 1865/1866): начинается после лейблов, не наезжает */}
                {hint === item.kind && (
                  <span className="absolute right-[84px] top-1/2 z-[1] flex h-[58px] w-[min(390px,58%)] -translate-y-1/2 items-center overflow-hidden rounded-r10 bg-[#2b2145] px-space-4 text-[15px] leading-[1.25] text-text shadow-[0_8px_28px_rgba(0,0,0,.45)] ring-1 ring-[var(--accent-light)] max-md:fixed max-md:inset-x-[12px] max-md:bottom-[12px] max-md:top-auto max-md:z-[60] max-md:h-auto max-md:w-auto max-md:translate-y-0 max-md:py-[12px] max-md:text-[13px] max-md:leading-[1.35]">
                    {t(item.hint)}
                  </span>
                )}
                <span
                  className="absolute right-[28px] z-[2] flex h-[40px] w-[40px] items-center justify-center max-md:right-[12px] max-md:h-[32px] max-md:w-[32px]"
                  onMouseEnter={() => setHint(item.kind)}
                  onMouseLeave={() => setHint(null)}
                  // тач: ховера нет — по тапу показываем/прячем, тап не должен выбирать строку
                  onClick={(e) => { e.stopPropagation(); setHint(hint === item.kind ? null : item.kind); }}
                  aria-label={t('wizard.fx.whatIs', { label: chip(HOOK_LABELS[item.kind]) })}
                >
                  <img src="/assets/figma/hint-circle.svg" width="40" height="40" alt="" aria-hidden="true" className="absolute inset-0" />
                  <span className="relative text-[20px] text-text-80">?</span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <ActionGuideOverlay
        open={showTypeGuide}
        targetRef={typeGuideTargetRef}
        title={t('wizard.fx.guideTypeTitle')}
        text={t('wizard.fx.guideTypeText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 3 })}
        onDismiss={() => setTypeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookTypeGuideVisual />}
      />
    </div>
  );
}

/*
 * Ряд чипов со скролл-фейдами по краям.
 * Края (правка заказчика): лента тает у краёв. Раньше поверх пилюль клали цветной градиент
 * «в тон фона» — но фон под лентой не однотонный (пилюли, границы), поэтому фейд всегда «не в
 * тон». Решение: маскируем сам скролл-контейнер (mask-image) — пилюли уходят в НАСТОЯЩУЮ
 * прозрачность, сквозь них виден реальный фон → всегда в тон, при любом фоне.
 * Слева фейд у 0; справа встаёт перед кнопкой подтверждения (`rightGap`).
 */
function ChipRow({ options, value, values, onPick, rightGap = 0, edgePad = 0 }: {
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
  // Маска: слева тает 24px (если есть что прокрутить), справа тает 24px и заканчивается ПЕРЕД
  // зоной кнопки (rightGap) — под кнопкой лента полностью прозрачна. Нулевые стопы = без фейда.
  const L = fade.left ? 24 : 0;
  const Rw = fade.right ? 24 : 0;
  const mask = `linear-gradient(to right, transparent 0px, #000 ${L}px, #000 calc(100% - ${rightGap + Rw}px), transparent calc(100% - ${rightGap}px))`;
  return (
    <div className="relative min-w-0">
      <div
        ref={scroll.ref}
        className="media-row cursor-grab select-none items-center gap-[12px] active:cursor-grabbing"
        style={{ height: 52, paddingLeft: edgePad, paddingRight: rightGap + edgePad, maskImage: mask, WebkitMaskImage: mask }}
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
            className={cn('glue-chip !h-[52px] !gap-space-3 !pl-[6px] !pr-space-4 !text-[18px]', selected && 'is-selected relative z-[2]')}
            onClick={() => { if (!scroll.moved()) onPick(selected && !values ? undefined : option); }}
          >
            <span className="scale-[0.8]"><ChipIcon label={option} /></span>
            {chip(option)}
          </button>
          );
        })}
      </div>
    </div>
  );
}

const KIND_ORDER: HookKind[] = ['warmup', 'object', 'effects', 'motion', 'thought', 'none'];

/** Подпись выбранного варианта в рабочей зоне. Звук — имя файла юзера, его не переводим. */
function hookPickLabel(config: HookConfig, chip: (label: string) => string): string | undefined {
  if (config.sound) return config.sound;
  const picked = config.object ?? config.motion ?? config.thought;
  return picked ? chip(picked) : undefined;
}

/** Следующий ЕЩЁ НЕ настроенный тип хука — им и оперирует «+» (как в футере визарда). */
function nextFreeKind(hooks: { configs: Partial<Record<HookKind, HookConfig>> }, current?: HookKind): HookKind | undefined {
  return KIND_ORDER.find((k) => k !== current && !hookComplete(k, hooks.configs[k]));
}

/**
 * Фуллскрин FX-зона (Figma W40): та же начинка, пересобранная под широкий экран.
 * Футер уехал в хедер: «Набор эффектов» = пилюли настроенных FX (60px, как в футере) + «+».
 * ПЕРВЫЙ контейнер — настройка ТЕКУЩЕГО типа хука (f1..f5: звук/объект/FX/движение/мысль),
 * два нижних — склейка и стилизация. Склейка/стиль пишутся в конфиг ТЕКУЩЕГО хука,
 * то есть у каждого хука своя пара — это и даёт уникальность вариаций.
 * Геометрия: контейнеры 390×160 и 390×175 (шаг 195), плеер 373×665 + «Продолжить» 373×60.
 */
/** The selected hook/shape/motion sample is already rendered and stored in S3. */
function EffectPreview({ previewId }: { previewId?: string }) {
  const query = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews });
  if (!previewId || query.isLoading) return null;
  const effect = query.data?.previews.find(item => item.id === previewId);
  return <CatalogMedia url={effect?.previewUrl} className="absolute inset-0 h-full w-full" />;
}

function HooksFullscreen({
  onCollapse,
  canContinue,
  onNext
}: {
  onCollapse: () => void;
  canContinue: boolean;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const [step, setStep] = useState(0);
  const pillsScroll = useDragScroll();
  const [pillsFade, setPillsFade] = useState({ left: false, right: false });

  const kind = hooks.kind ?? 'effects';
  const config = hooks.configs[kind] || {};
  const pills = hookPills(hooks);
  const nextKind = nextFreeKind(hooks, kind);
  const syncPillFades = () => {
    const el = pillsScroll.ref.current;
    if (!el) return;
    setPillsFade({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  };

  useEffect(() => {
    syncPillFades();
    const el = pillsScroll.ref.current;
    if (!el) return;
    const observer = new ResizeObserver(syncPillFades);
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pills.length]);
  // Маска ленты пилюль: пилюли тают в прозрачность у краёв (в тон любого фона).
  const pillsMask = `linear-gradient(to right, transparent 0px, #000 ${pillsFade.left ? 24 : 0}px, #000 calc(100% - ${pillsFade.right ? 24 : 0}px), transparent 100%)`;

  /* Контейнер 390×175: заголовок с отступом, лента на всю ширину (фейды у краёв контейнера, а не ленты) */
  const section = (title: string, index: number, options: string[], key: keyof HookConfig) => (
    <div className={cn('h-[175px] shrink-0 overflow-hidden rounded-r15 bg-grad-soft-10 py-[28px]', step === index && 'shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
      <div className="flex items-center justify-between gap-[8px] px-[28px]">
        <p className="wizard-body min-w-0 truncate leading-[29px]">{title}</p>
        {key === 'effectStyle' && kind !== 'none' && <StyleScopeToggle config={config} onPick={(full) => { setHooks({ config: { effectStyleFull: full } }); setStep(index); }} />}
        {key === 'effectHook' && config.effectHook === 'Слоу-шаттер' && <SlowShutterExtendToggle config={config} onPick={(value) => { setHooks({ config: { effectHookExtend: value } }); setStep(index); }} />}
      </div>
      <div className="mt-[28px]">
        <ChipRow
          options={options}
          value={config[key] as string | undefined}
          values={key === 'effectStyle' ? selectedStyles(config) : undefined}
          edgePad={28}
          onPick={(option) => { setHooks({ config: key === 'effectStyle' ? toggleStyle(config, option) : { [key]: option } }); setStep(index); }}
        />
      </div>
    </div>
  );

  /*
   * Шаги те же, что и в обычной рабочей зоне (hookSteps) — просто здесь ширина позволяет
   * показать все три сразу, а не по одному. Общее определение и есть страховка от того,
   * что режимы снова разъедутся по составу настроек.
   * Для «Звука» первый шаг — не список, а загрузка/прослушивание своего файла.
   */
  const steps = hookSteps(kind);
  const currentDef = steps[Math.min(step, steps.length - 1)];
  const previewId = configuredPreviewId(kind, config, currentDef);

  /** Стрелки плеера листают варианты ВНУТРИ активной группы, не переключая группы. */
  const cycleVariant = (delta: number) => {
    if (!currentDef || currentDef.options.length === 0) return;
    const current = config[currentDef.key] as string | undefined;
    const index = current ? currentDef.options.indexOf(current) : -1;
    const nextIndex = index < 0
      ? (delta > 0 ? 0 : currentDef.options.length - 1)
      : (index + delta + currentDef.options.length) % currentDef.options.length;
    const option = currentDef.options[nextIndex];
    setHooks({ config: currentDef.key === 'effectStyle' ? toggleStyle(config, option) : { [currentDef.key]: option } });
  };

  const left = (
    <div className="flex h-full flex-col gap-[20px]">
      {/* «Набор эффектов»: бывший футер — пилюли 60px + «+» добавляет следующий тип хука */}
      <div className="h-[160px] shrink-0 rounded-r15 bg-grad-soft-10 p-[28px]">
        <p className="wizard-body leading-[29px]">{t('wizard.fx.set')}</p>
        <div className="mt-[20px] flex min-w-0 items-center gap-[12px]">
          <div className="relative min-w-0 flex-1">
            <div
              ref={pillsScroll.ref}
              onScroll={syncPillFades}
              className="no-scrollbar flex min-w-0 cursor-grab items-center gap-[12px] overflow-x-auto select-none active:cursor-grabbing"
              style={{ maskImage: pillsMask, WebkitMaskImage: pillsMask }}
              {...pillsScroll.handlers}
            >
            {pills.map((pill) => (
              <button
                key={pill.kind}
                type="button"
                onClick={() => { if (!pillsScroll.moved()) { setHooks({ kind: pill.kind }); setStep(0); } }}
                className={cn('flex h-[60px] shrink-0 items-center gap-[10px] rounded-r15 bg-grad-soft-20 px-[20px] text-[24px] font-[350] leading-none text-text-80 transition', kind === pill.kind && 'text-text shadow-[inset_0_0_0_2px_var(--accent-light)]')}
              >
                <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 12, height: 18, color: 'var(--accent-light)' }} />
                {chip(pill.label)}
              </button>
            ))}
            </div>
          </div>
          <button
            type="button"
            aria-label={t('wizard.fx.add')}
            disabled={!nextKind}
            onClick={() => { if (nextKind) { setHooks({ kind: nextKind }); setStep(0); } }}
            className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r15 bg-text text-accent transition hover:opacity-90 disabled:cursor-default"
          >
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
              <path d="M10 4v12M4 10h12" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>
      </div>

      {steps.map((definition, index) => kind === 'warmup' && index === 0 ? (
        <div key={definition.key} className={cn('min-h-[175px] shrink-0 rounded-r15 bg-grad-soft-10 p-[28px]', step === 0 && 'shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
          <WarmupInput />
        </div>
      ) : <Fragment key={definition.key}>{section(t(definition.title), index, definition.options, definition.key)}</Fragment>)}
    </div>
  );

  const right = (
    <div className="flex h-full flex-col">
      <div className="group relative h-[665px] shrink-0 overflow-hidden rounded-r15 bg-grad-soft-10">
        <EffectPreview previewId={previewId} />
        <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
        {/* стрелки: пролистывание вариантов активной группы (Figma 746:1412) */}
        <button type="button" aria-label={t('wizard.fx.prevStep')} disabled={!currentDef} onClick={() => cycleVariant(-1)} className="absolute left-[25px] top-1/2 z-[4] -translate-y-1/2 text-text opacity-0 transition-opacity group-hover:opacity-100 disabled:opacity-30">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true"><path d="M14.5 6 8.5 12l6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <button type="button" aria-label={t('wizard.fx.nextStep')} disabled={!currentDef} onClick={() => cycleVariant(1)} className="absolute right-[25px] top-1/2 z-[4] -translate-y-1/2 text-text opacity-0 transition-opacity group-hover:opacity-100 disabled:opacity-30">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true"><path d="M9.5 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>

        <span className="absolute left-1/2 top-1/2 z-[4] flex h-[60px] w-[60px] -translate-x-1/2 -translate-y-1/2 items-center justify-center gap-[6px] rounded-full bg-[rgba(5,1,15,0.6)] opacity-0 transition-opacity group-hover:opacity-100">
          <span className="h-[20px] w-[5px] rounded-[2px] bg-text" />
          <span className="h-[20px] w-[5px] rounded-[2px] bg-text" />
        </span>

      </div>

      <button
        type="button"
        onClick={() => { onCollapse(); onNext(); }}
        disabled={!canContinue}
        className={cn('mt-[20px] flex h-[60px] shrink-0 items-center justify-center gap-[16px] rounded-r15 bg-grad-soft-20 text-[24px] font-[350] leading-none text-text-80 transition', canContinue && 'border border-accent-light hover:text-text')}
      >
        {t('wizard.continue')}
        <ArrowRight />
      </button>
    </div>
  );

  return <FullscreenZone onCollapse={onCollapse} left={left} right={right} />;
}

export function HooksWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const pillsScroll = useDragScroll();
  const [step, setStep] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const workzoneGuideTargetRef = useRef<HTMLDivElement>(null);

  const kind = hooks.kind;
  const config = (kind && hooks.configs[kind]) || {};
  // Третий гайд хука: тип уже выбран. Раньше подсказка 2/3 (hook-type, в
  // StageHooks) сама пряталась условием !hooks.kind — теперь она гейтится
  // только своим dismissed, и её можно оставить открытой, выбрав тип кликом
  // мимо кнопки подсказки, поэтому ждём его ЖИВОГО dismissed явно — иначе
  // hook-type (левая панель) и этот гайд (правая) всплывают одновременно.
  const typeGuideDismissed = useGuideLiveDismissed('hook-type');
  const [workzoneGuideDismissed, setWorkzoneGuideDismissed] = useGuideDismiss('hook-workzone', Boolean(kind), Boolean(kind) && typeGuideDismissed);
  const showWorkzoneGuide = Boolean(kind) && typeGuideDismissed && !workzoneGuideDismissed;
  useScrollGuideIntoView(showWorkzoneGuide, workzoneGuideTargetRef);
  /*
   * Смена типа хука начинает его настройку с первого шага. Без сброса переключение
   * с недонастроенных «Эффектов» на «Звук» открывало бы сразу шаг «стиль».
   * Если у нового хука первый шаг уже заполнен — сразу ведём на первый незаполненный.
   */
  useEffect(() => {
    if (!kind) return;
    const filled = hookSteps(kind).findIndex((item) => !((hooks.configs[kind] || {})[item.key]));
    setStep(filled < 0 ? 0 : filled);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);
  const configuredPills = hookPills(hooks);
  const pills = kind && !configuredPills.some((pill) => pill.kind === kind)
    ? [...configuredPills, { kind, label: HOOK_LABELS[kind] }]
    : configuredPills;
  const nextKind = nextFreeKind(hooks, kind);

  /*
   * Единый мастер настройки хука: шаг 1 — сам хук, шаг 2 — склейка, шаг 3 — стиль.
   * Раньше так работали только «Эффекты», а остальные типы показывали один список и
   * считались настроенными: склейку и стиль им можно было выбрать лишь в фуллскрине.
   * Определение шагов общее с фуллскрином (см. hookSteps), разъехаться им негде.
   */
  const steps = kind ? hookSteps(kind) : [];
  const stepIndex = Math.min(step, Math.max(0, steps.length - 1));
  const stepDef = steps[stepIndex];
  const stepValue = stepDef ? (config[stepDef.key] as string | undefined) : undefined;
  const styleValues = selectedStyles(config);
  const previewId = kind ? configuredPreviewId(kind, config, stepDef) : undefined;
  const canAdvance = Boolean(stepDef && (stepDef.key === 'effectStyle' ? styleValues.length : stepValue)) && stepIndex < steps.length - 1;

  const confirmButton = canAdvance && (
    /* Подтверждение шага галочкой (Figma W28) — переход только по клику */
    <button
      type="button"
      aria-label={t('wizard.fx.confirmStep')}
      className="absolute right-0 top-0 z-[2] flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-r15 bg-text transition hover:opacity-90 active:scale-95"
      onClick={() => setStep(stepIndex + 1)}
    >
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </button>
  );

  /** Шаг «Звук» — не выбор из списка, а загрузка своего файла. */
  const soundStep = <WarmupInput />;

  const settings = kind && stepDef && (
    <div className="shrink-0 rounded-r15 bg-grad-soft-10 p-space-5">
      <div className="mb-space-4 flex items-center justify-between gap-[10px]">
        <p className="wizard-body min-w-0 truncate">{t(stepDef.title)}</p>
        {stepDef.key === 'effectStyle' && kind !== 'none' && <StyleScopeToggle config={config} onPick={(full) => setHooks({ config: { effectStyleFull: full } })} />}
        {stepDef.key === 'effectHook' && config.effectHook === 'Слоу-шаттер' && <SlowShutterExtendToggle config={config} onPick={(value) => setHooks({ config: { effectHookExtend: value } })} />}
        <span className="flex shrink-0 items-center gap-space-1 rounded-r40 bg-accent-20 px-space-2 py-space-1 text-[14px] text-text-80">
          <button type="button" aria-label={t('wizard.fx.prevStep')} disabled={stepIndex === 0} className="disabled:opacity-40" onClick={() => setStep(stepIndex - 1)}>‹</button>
          {stepIndex + 1}/{steps.length}
          <button type="button" aria-label={t('wizard.fx.nextStep')} disabled={stepIndex === steps.length - 1} className="disabled:opacity-40" onClick={() => setStep(stepIndex + 1)}>›</button>
        </span>
      </div>
      {/* Галочка — overlay поверх ленты справа: лента уходит ПОД неё, правый фейд встаёт перед */}
      <div className="relative">
        {stepDef.options.length === 0 ? (
          <div className={cn(canAdvance && 'pr-[64px]')}>{soundStep}</div>
        ) : (
          <ChipRow
            options={stepDef.options}
            value={stepValue}
            values={stepDef.key === 'effectStyle' ? styleValues : undefined}
            onPick={(option) => setHooks({ config: stepDef.key === 'effectStyle' ? toggleStyle(config, option) : { [stepDef.key]: option } })}
            rightGap={canAdvance ? 64 : 0}
          />
        )}
        {confirmButton}
      </div>
    </div>
  );

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      {fullscreen && (
        <HooksFullscreen
          onCollapse={() => setFullscreen(false)}
          canContinue={canContinue}
          onNext={onNext}
        />
      )}
      <div ref={workzoneGuideTargetRef} className="card-2 flex min-h-0 flex-1 flex-col gap-space-5 px-space-6 py-space-6 max-lg:px-space-5">
        {/* Figma W41: разворот в фуллскрин — в правом верхнем углу FX-зоны */}
        <div className="flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          {/* Была голая иконка 20×20 без подложки — её просто не замечали. Теперь это
              обычная кнопка с обводкой и подписью: видно, что тут есть широкий режим. */}
          <button
            type="button"
            onClick={() => setFullscreen(true)}
            className="flex h-[37px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent-light bg-grad-soft-20 px-[12px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125 max-md:hidden"
          >
            <img src="/assets/figma/fx-expand.svg" width="16" height="16" alt="" aria-hidden />
            {t('common.expand')}
          </button>
        </div>

        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10 max-md:aspect-[9/16] max-md:w-full">
          {kind && <EffectPreview previewId={previewId} />}
          <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          {!kind ? (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[223px] text-center">{t('wizard.fx.empty')}</p>
            </div>
          ) : null}
        </div>

        {settings}
      </div>

      <ActionGuideOverlay
        open={showWorkzoneGuide}
        targetRef={workzoneGuideTargetRef}
        title={t('wizard.fx.guideWorkzoneTitle')}
        text={t('wizard.fx.guideWorkzoneText')}
        dismissLabel={t('wizard.fx.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 3, total: 3 })}
        onDismiss={() => setWorkzoneGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<HookWorkzoneGuideVisual />}
      />

      <PillsFooter
        pills={pills.map((pill) => ({
          key: pill.kind,
          label: chip(pill.label),
          icon: <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 12, height: 18, color: 'var(--accent-light)' }} />
        }))}
        activeKey={kind}
        emptyLabel={t('wizard.fx.add')}
        onPill={(key) => setHooks({ kind: key as HookKind })}
        onPlus={() => { if (nextKind) setHooks({ kind: nextKind }); }}
        plusDisabled={!nextKind}
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
