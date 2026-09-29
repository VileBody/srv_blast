import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { create } from 'zustand';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { useChip } from '../../i18n/useChip';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { CatalogMedia } from './CatalogPreview';
import { useDragScroll } from './BackgroundPanel';
import { PillsFooter } from './WizardFrame';
import { emptyTimeline, HOOK_LABELS, HookConfig, HookKind, hookComplete, TimelineRecipe, useWizardStore } from '../../stores/wizardStore';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { useFxTimelineOpen } from './timelineGuides';
import {
  ChipRow, HOOK_TYPES, HookStep,
  hookSteps, previewIdFor, selectedStyles
} from './HookPanel';

// Таймлайн сам берёт каталоги из HookPanel — статический импорт дал бы цикл модулей.
const FxTimeline = lazy(() => import('./FxTimeline').then((m) => ({ default: m.FxTimeline })));

/*
 * ПРОТОТИП (?fxLab=1): варианты хука на шаге FX без переделки логики настройки.
 *
 * Вариант = тип хука + его конфиг (хук, склейка, ОДИН стиль) + свой рецепт таймлайна.
 * Сегодня это hooks.configs[kind]; здесь таких конфигов у одного типа может быть
 * несколько. Состояние прототипа локальное: в черновик и в рендер ничего не уходит.
 *
 *  - левый список типов — аккордеон: раскрытый тип показывает свои варианты;
 *  - настройка (хук · склейка · стиль) — док прямо на примере, видео во всю высоту;
 *  - футер — пилюли вариантов, «+» = копия текущего;
 *  - таймлайн открывается на текущем варианте, вкладки в шапке = варианты;
 *  - «Пул»: у каждого варианта своя пилюля и счётчик видео.
 */

/**
 * Флаг прототипа «липкий» на сессию вкладки: навигация визарда по шагам переписывает
 * адрес и теряет `?fxLab=1`. `?fxLab=0` выключает.
 */
export const useFxLab = () => {
  if (typeof window === 'undefined') return false;
  const param = new URLSearchParams(window.location.search).get('fxLab');
  try {
    if (param === '0') window.sessionStorage.removeItem('fxLab');
    else if (param !== null) window.sessionStorage.setItem('fxLab', '1');
    return window.sessionStorage.getItem('fxLab') === '1';
  } catch {
    return param !== null && param !== '0';
  }
};

export interface LabVariant { id: string; kind: HookKind; config: HookConfig; color: string; recipe?: TimelineRecipe; draft?: boolean }
const PALETTE = ['#8b6fe6', '#e38fb5', '#6fc7c0', '#e8b45f', '#9fb5ff', '#b7e27a', '#ff9a7a', '#d6a1ff'];
const STEP_NAME: Record<string, string> = {
  sound: 'Прогрев', object: 'Объект', effectHook: 'Эффект', motion: 'Движение', thought: 'Мысль',
  effectGlue: 'Склейка', effectStyle: 'Стиль'
};

type Snapshot = { hooks: ReturnType<typeof useWizardStore.getState>['hooks']; timeline: TimelineRecipe };

interface LabState {
  variants: LabVariant[];
  activeId: string | null;
  expanded: HookKind | null;
  tab: number;
  counts: Record<string, number>;
  snapshot: Snapshot | null;
  select: (id: string) => void;
  toggleType: (kind: HookKind) => void;
  add: (kind: HookKind, config?: HookConfig) => void;
  copyActive: () => void;
  remove: (id: string) => void;
  patch: (patch: Partial<HookConfig>) => void;
  setTab: (tab: number) => void;
  setCount: (id: string, n: number) => void;
  /* таймлайн работает со стором визарда — на время его работы вариант «одалживается» в стор */
  beginTimeline: () => void;
  switchTimeline: (id: string) => void;
  newInTimeline: () => void;
  endTimeline: () => void;
}

let seq = 0;
const newId = () => `v${++seq}`;

function stepFilled(step: HookStep, config: HookConfig): boolean {
  return step.key === 'effectStyle' ? selectedStyles(config).length > 0 : Boolean(config[step.key]);
}
function firstOpenTab(v?: LabVariant): number {
  if (!v) return 0;
  const i = hookSteps(v.kind).findIndex((step) => !stepFilled(step, v.config));
  return i < 0 ? 0 : i;
}

function loadIntoWizard(v: LabVariant) {
  const store = useWizardStore.getState();
  useWizardStore.setState({
    // черновой вариант (создан из таймлайна) ещё без хука — таймлайн откроется пустым
    hooks: { ...store.hooks, kind: v.draft ? undefined : v.kind, configs: v.draft ? {} : { [v.kind]: v.config } },
    timeline: v.recipe ? { ...v.recipe } : { ...emptyTimeline(), pace: 'auto' }
  });
}
function readFromWizard(v: LabVariant): LabVariant {
  const { hooks, timeline } = useWizardStore.getState();
  if (!hooks.kind) return { ...v, recipe: { ...timeline } };
  return { ...v, kind: hooks.kind, draft: false, config: { ...(hooks.configs[hooks.kind] ?? {}) }, recipe: { ...timeline } };
}
const mkDraft = (): LabVariant => ({ id: newId(), kind: 'none', config: {}, color: PALETTE[seq % PALETTE.length], draft: true });

export const useFxLabStore = create<LabState>((set, get) => ({
  variants: [],
  activeId: null,
  expanded: null,
  tab: 0,
  counts: {},
  snapshot: null,
  select: (id) => set((s) => ({ activeId: id, expanded: s.variants.find((v) => v.id === id)?.kind ?? s.expanded, tab: firstOpenTab(s.variants.find((v) => v.id === id)) })),
  toggleType: (kind) => {
    const s = get();
    if (s.expanded === kind) { set({ expanded: null }); return; }
    const first = s.variants.find((v) => v.kind === kind);
    if (first) set({ expanded: kind, activeId: first.id, tab: firstOpenTab(first) });
    else get().add(kind);
  },
  add: (kind, config = {}) => set((s) => {
    const v = { id: newId(), kind, config, color: PALETTE[seq % PALETTE.length] };
    return { variants: [...s.variants, v], activeId: v.id, expanded: kind, tab: firstOpenTab(v), counts: { ...s.counts, [v.id]: 1 } };
  }),
  copyActive: () => {
    const s = get();
    const src = s.variants.find((v) => v.id === s.activeId);
    if (!src) return;
    // Копия: тот же хук, склейка и темп; стиль — выбрать заново (в варианте он один).
    const config: HookConfig = { ...src.config, effectStyles: [], effectStyle: undefined };
    const v: LabVariant = { id: newId(), kind: src.kind, config, color: PALETTE[seq % PALETTE.length], recipe: src.recipe ? { ...src.recipe, styles: [] } : undefined };
    set({ variants: [...s.variants, v], activeId: v.id, expanded: v.kind, tab: hookSteps(v.kind).length - 1, counts: { ...s.counts, [v.id]: 1 } });
  },
  remove: (id) => set((s) => {
    const variants = s.variants.filter((v) => v.id !== id);
    const activeId = s.activeId === id ? (variants.find((v) => v.kind === s.expanded) ?? variants[0])?.id ?? null : s.activeId;
    const { [id]: _gone, ...counts } = s.counts;
    return { variants, activeId, counts };
  }),
  patch: (patch) => set((s) => ({ variants: s.variants.map((v) => (v.id === s.activeId ? { ...v, config: { ...v.config, ...patch } } : v)) })),
  setTab: (tab) => set({ tab }),
  setCount: (id, n) => set((s) => ({ counts: { ...s.counts, [id]: Math.max(0, n) } })),
  beginTimeline: () => {
    const s = get();
    let v = s.variants.find((x) => x.id === s.activeId);
    // Таймлайн доступен всегда: без вариантов открываем черновой — он станет вариантом,
    // как только в таймлайне выберут хук (иначе при закрытии исчезнет).
    if (!v) { v = mkDraft(); set({ variants: [...s.variants, v], activeId: v.id, counts: { ...s.counts, [v.id]: 1 } }); }
    const { hooks, timeline } = useWizardStore.getState();
    set({ snapshot: { hooks, timeline } });
    loadIntoWizard(v);
  },
  newInTimeline: () => {
    const s = get();
    const variants = s.variants.map((x) => (x.id === s.activeId ? readFromWizard(x) : x));
    const v = mkDraft();
    set({ variants: [...variants, v], activeId: v.id, counts: { ...s.counts, [v.id]: 1 } });
    loadIntoWizard(v);
  },
  switchTimeline: (id) => {
    const s = get();
    const next = s.variants.find((x) => x.id === id);
    if (!next || id === s.activeId) return;
    const variants = s.variants.map((x) => (x.id === s.activeId ? readFromWizard(x) : x));
    set({ variants, activeId: id, expanded: next.kind });
    loadIntoWizard(variants.find((x) => x.id === id)!);
  },
  endTimeline: () => {
    const s = get();
    // черновики, которым в таймлайне так и не выбрали хук, не остаются пустыми вариантами
    const variants = s.variants.map((x) => (x.id === s.activeId ? readFromWizard(x) : x)).filter((x) => !x.draft);
    const active = variants.find((x) => x.id === s.activeId);
    set({ variants, snapshot: null, activeId: active?.id ?? variants[0]?.id ?? null, expanded: active?.kind ?? s.expanded, tab: firstOpenTab(active) });
    if (s.snapshot) useWizardStore.setState({ hooks: s.snapshot.hooks, timeline: s.snapshot.timeline });
  }
}));

/** Короткое описание варианта: хук · склейка · стиль. */
export function useVariantLabel() {
  const chip = useChip();
  return (v: LabVariant) => {
    if (v.draft) return 'Пустой вариант — выбери хук';
    const c = v.config;
    const hook = v.kind === 'none' ? HOOK_LABELS.none : (c.sound ?? c.object ?? c.effectHook ?? c.motion ?? c.thought);
    const style = selectedStyles(c)[0];
    const parts = [hook ? chip(hook) : null, c.effectGlue ? chip(c.effectGlue) : null, style ? chip(style) : null].filter(Boolean);
    return parts.length ? parts.join(' · ') : `${chip(HOOK_LABELS[v.kind])} — новый`;
  };
}

const iconOf = (kind: HookKind) => HOOK_TYPES.find((item) => item.kind === kind)!;
const guideShell = { variant: 'visual' as const, shell: 'track-top' as const };

/* ── мини-визуалы подсказок (язык тот же: появление по очереди, одна анимация) ── */

function VariantsGuideVisual() {
  const rows = [['#8b6fe6', 'Молния · Щелчок · Неон'], ['#e38fb5', 'Молния · Щелчок · Ч/Б']];
  return (
    <div className="flex w-full flex-col gap-[6px]" aria-hidden="true">
      {rows.map(([c, l], i) => (
        <span key={l} className={cn('guide-mode-reveal flex h-[24px] items-center gap-[7px] rounded-[7px] px-[8px] text-[11px] leading-none text-white/85', i ? 'guide-mode-delay-2 bg-white/[0.06]' : 'guide-mode-delay-1 bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
          <i className="h-[7px] w-[7px] rounded-full" style={{ background: c }} /><span className="translate-y-px">{l}</span>
          <span className="ml-auto text-white/50">×</span>
        </span>
      ))}
      <span className="guide-mode-reveal guide-mode-delay-3 flex h-[22px] w-fit items-center gap-[5px] rounded-[7px] bg-white/[0.08] px-[8px] text-[11px] leading-none text-white/80"><span>+</span><span className="translate-y-px">Вариант</span></span>
    </div>
  );
}
function DockGuideVisual() {
  // слева направо: «видео» со стрелками (кадр сменяется) → под ним лента, где один пункт выбран
  return (
    <div className="flex w-full items-center gap-[10px]" aria-hidden="true">
      <span className="guide-mode-reveal guide-mode-delay-1 relative h-[64px] w-[40px] shrink-0 overflow-hidden rounded-[7px] bg-black">
        <i className="guide-sb-a absolute inset-0" style={{ background: 'linear-gradient(160deg, #3b2f6e, #120b24 70%)' }} />
        <i className="guide-sb-b absolute inset-0" style={{ background: 'linear-gradient(200deg, #6a3f58, #1a0d1c 70%)' }} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-[6px]">
        <span className="guide-mode-reveal guide-mode-delay-2 flex items-center gap-[6px] text-[10px] leading-none text-white/80">
          <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-white/10">‹</span>
          <span className="translate-y-px">смотри примеры</span>
          <span className="guide-sb-press flex h-[18px] w-[18px] items-center justify-center rounded-full bg-white/10">›</span>
        </span>
        <span className="guide-mode-reveal guide-mode-delay-3 flex gap-[4px]">
          {['Щелчок', 'Минимакс', 'Вспышка'].map((l, i) => (
            <span key={l} className={cn('truncate rounded-[6px] px-[6px] py-[4px] text-[9px] leading-none', i === 1 ? 'bg-accent-20 text-white shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'bg-white/[0.07] text-white/60')}><span className="inline-block translate-y-px">{l}</span></span>
          ))}
        </span>
        <span className="guide-mode-reveal guide-mode-delay-4 text-[10px] leading-none text-white/60"><span className="inline-block translate-y-px">выбор — в ленте ниже</span></span>
      </span>
    </div>
  );
}
function FooterGuideVisual() {
  return (
    <div className="flex w-full items-center gap-[6px]" aria-hidden="true">
      {['#8b6fe6', '#e38fb5', '#6fc7c0'].map((c, i) => (
        <span key={c} className={cn('guide-mode-reveal flex h-[26px] flex-1 items-center gap-[6px] rounded-[8px] px-[8px]', `guide-mode-delay-${i + 1}`, i === 0 ? 'bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'bg-white/[0.06]')}>
          <i className="h-[7px] w-[7px] rounded-full" style={{ background: c }} /><i className="h-[3px] flex-1 rounded-full bg-white/30" />
        </span>
      ))}
      <span className="guide-mode-reveal guide-mode-delay-4 flex h-[26px] w-[26px] items-center justify-center rounded-[8px] bg-[#e9e6f8] text-[15px] text-[#221643]"><span className="guide-sb-press inline-block">+</span></span>
    </div>
  );
}

/* ── левая панель: типы-аккордеон с вариантами ─────────────────────────────── */

export function LabTypeList({ locked }: { locked: boolean }) {
  const { t } = useTranslation();
  const chip = useChip();
  const lab = useFxLabStore();
  const label = useVariantLabel();
  const [hint, setHint] = useState<HookKind | null>(null);
  const openRef = useRef<HTMLDivElement>(null);

  // Подсказка 1/3: появляется, когда у раскрытого типа есть вариант.
  const hasOpen = Boolean(lab.expanded && lab.variants.some((v) => v.kind === lab.expanded));
  const [variantsGuideDismissed, setVariantsGuideDismissed] = useGuideDismiss('fxlab-variants', false);
  const showVariantsGuide = hasOpen && !variantsGuideDismissed;
  useMarkGuideSeen('fxlab-variants', showVariantsGuide);

  return (
    <>
      <div className="no-scrollbar flex h-full flex-col gap-[14px] overflow-y-auto py-[16px]" style={{ maskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)', WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)' }}>
        {HOOK_TYPES.map((item) => {
          const variants = lab.variants.filter((v) => v.kind === item.kind);
          const open = lab.expanded === item.kind;
          const has = variants.length > 0;
          const isLocked = locked && item.kind !== 'none';
          return (
            <div key={item.kind} ref={open ? openRef : undefined} className={cn('relative shrink-0 rounded-r15 bg-grad-soft-10 transition', (open || has) && 'border-2 border-accent-light', open && 'bg-grad-soft-20', isLocked && 'opacity-45')}>
              {/* строка типа: иконка · название · «?» рядом с названием · метки вариантов · стрелка справа */}
              <button
                type="button"
                disabled={isLocked}
                aria-expanded={open}
                onClick={() => lab.toggleType(item.kind)}
                className="flex h-[76px] w-full items-center gap-[12px] px-[26px] text-left disabled:cursor-not-allowed"
              >
                <SvgMaskIcon src={item.icon} style={{ width: item.iconW, height: item.iconH, color: has ? 'var(--accent-light)' : 'var(--text-80)' }} />
                <span className="wizard-body ml-[4px] !text-text">{chip(HOOK_LABELS[item.kind])}</span>
                <span
                  role="button"
                  tabIndex={0}
                  className="relative flex h-[24px] w-[24px] shrink-0 items-center justify-center rounded-full bg-white/[0.08] text-[13px] text-text-60 transition hover:bg-white/[0.14] hover:text-text"
                  onMouseEnter={() => setHint(item.kind)}
                  onMouseLeave={() => setHint(null)}
                  onClick={(e) => { e.stopPropagation(); setHint(hint === item.kind ? null : item.kind); }}
                  aria-label={t('wizard.fx.whatIs', { label: chip(HOOK_LABELS[item.kind]) })}
                >
                  <span className="translate-y-px">?</span>
                </span>
                {has && (
                  <span className="flex items-center gap-[5px]" aria-label={`${variants.length} вар.`}>
                    {variants.slice(0, 5).map((v) => <i key={v.id} className="h-[8px] w-[8px] rounded-full" style={{ background: v.color }} />)}
                  </span>
                )}
                <span className={cn('ml-auto flex h-[32px] w-[32px] shrink-0 items-center justify-center rounded-[10px] text-text-60 transition', open ? 'bg-white/[0.08] text-text' : 'bg-transparent')}>
                  <svg className={cn('h-[16px] w-[16px] transition-transform', open && 'rotate-180')} viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
              </button>
              {hint === item.kind && (
                <span className="absolute left-[26px] right-[26px] top-[64px] z-[3] rounded-r10 bg-[#2b2145] px-space-4 py-[10px] text-[14px] leading-[1.3] text-text shadow-[0_8px_28px_rgba(0,0,0,.45)] ring-1 ring-[var(--accent-light)]">{t(item.hint)}</span>
              )}
              {open && (
                <div className="flex flex-col gap-[8px] px-[16px] pb-[16px]">
                  {variants.map((v) => {
                    const active = v.id === lab.activeId;
                    const done = hookComplete(v.kind, v.config);
                    return (
                      <div key={v.id} className={cn('flex h-[46px] items-center rounded-r10 transition', active ? 'bg-accent-20 shadow-[inset_0_0_0_1.5px_var(--accent-light)]' : 'bg-[rgba(5,1,15,.34)] hover:bg-[rgba(5,1,15,.5)]')}>
                        <button type="button" aria-current={active} className="flex h-full min-w-0 flex-1 items-center gap-[11px] pl-[14px] text-left" onClick={() => lab.select(v.id)}>
                          <i className="h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: v.color }} />
                          <span className={cn('min-w-0 translate-y-px truncate text-[16px]', active ? 'text-text' : 'text-text-80')}>{label(v)}</span>
                          {!done && <span className="shrink-0 rounded-[6px] bg-[rgba(245,158,11,.14)] px-[7px] py-[3px] text-[12px] leading-none text-[var(--warning)]"><span className="inline-block translate-y-px">настроить</span></span>}
                        </button>
                        <button type="button" aria-label="Удалить вариант" title="Удалить вариант" onClick={() => lab.remove(v.id)} className="mr-[6px] flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] text-text-40 transition hover:bg-white/10 hover:text-text active:scale-95">
                          <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1L11 11M11 1L1 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                        </button>
                      </div>
                    );
                  })}
                  <button type="button" onClick={() => (variants.length ? lab.copyActive() : lab.add(item.kind))} className="flex h-[40px] w-fit items-center gap-[8px] rounded-r10 bg-white/[0.07] px-[14px] text-[15px] text-text-80 transition hover:bg-white/[0.12] hover:text-text active:scale-[.98]">
                    <span className="text-[18px] leading-none text-accent-light">+</span>
                    <span className="translate-y-px">Вариант</span>
                    <span className="translate-y-px text-[13px] text-text-40">копия текущего</span>
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <ActionGuideOverlay
        open={showVariantsGuide}
        targetRef={openRef}
        title="Варианты хука"
        text="У одного типа может быть несколько вариантов — у каждого своя склейка и свой стиль. В «Пуле» каждый вариант получает свою долю роликов."
        dismissLabel="Дальше"
        progressLabel="Шаг 1 из 3"
        onDismiss={() => setVariantsGuideDismissed(true)}
        visual={<VariantsGuideVisual />}
        {...guideShell}
      />
    </>
  );
}

/* ── уточнение выбранного (где действует стиль, длина шлейфа): строка в доке ── */

function LabModifier({ label, value, options, onPick }: { label: string; value: string; options: [string, string][]; onPick: (value: string) => void }) {
  return (
    <div className="flex items-center justify-between gap-[12px] px-[4px]">
      <span className="translate-y-px text-[14px] text-text-60">{label}</span>
      <span className="flex gap-[2px] rounded-[10px] bg-white/[0.06] p-[3px]" role="group" aria-label={label}>
        {options.map(([val, text]) => (
          <button key={val || 'std'} type="button" aria-pressed={value === val} onClick={() => onPick(val)}
            className={cn('h-[28px] whitespace-nowrap rounded-[8px] px-[12px] text-[13px] transition', value === val ? 'bg-accent-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-60 hover:text-text')}>
            <span className="inline-block translate-y-px">{text}</span>
          </button>
        ))}
      </span>
    </div>
  );
}

/* ── рабочая зона: пример во всю высоту, настройка — доком внутри него ─────── */

function LabPreview({ previewId }: { previewId?: string }) {
  const query = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews });
  if (!previewId || query.isLoading) return null;
  const effect = query.data?.previews.find((item) => item.id === previewId);
  // cover: пример заполняет рабочую зону целиком, а не висит полосой посередине
  return <div className="absolute inset-0 [&_img]:!object-cover [&_video]:!object-cover"><CatalogMedia url={effect?.previewUrl} className="absolute inset-0 h-full w-full" /></div>;
}

export function LabWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const lab = useFxLabStore();
  const label = useVariantLabel();
  const pillsScroll = useDragScroll();
  const [timelineOpen, setTimelineOpen] = useState(false);
  const dockRef = useRef<HTMLDivElement>(null);
  const footRef = useRef<HTMLDivElement>(null);
  const v = lab.variants.find((x) => x.id === lab.activeId);
  const steps = v ? hookSteps(v.kind) : [];
  const tab = Math.min(lab.tab, Math.max(0, steps.length - 1));
  const step = steps[tab];
  const config = v?.config ?? {};
  const style = selectedStyles(config)[0];
  const selected = step ? (step.key === 'effectStyle' ? style : (config[step.key] as string | undefined)) : undefined;
  // Курсор просмотра: стрелки на видео листают примеры, ничего не выбирая. Выбор — в ленте.
  const options = step?.options ?? [];
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    const i = selected ? options.indexOf(selected) : -1;
    setCursor(i < 0 ? 0 : i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v?.id, tab]);
  const browsed = options[Math.min(cursor, Math.max(0, options.length - 1))];
  const previewId = step && browsed ? previewIdFor(step.key, browsed) : undefined;
  const browse = (d: number) => { if (options.length) setCursor((c) => (c + d + options.length) % options.length); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (timelineOpen || !(e.target instanceof HTMLElement) || e.target.matches('input, textarea, select')) return;
      if (e.key === 'ArrowLeft') browse(-1);
      if (e.key === 'ArrowRight') browse(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  // Подсказки 2–3: после «Варианты хука» (левая панель — поэтому её живой dismissed).
  const variantsDismissed = useGuideLiveDismissed('fxlab-variants');
  const [footGuideDismissed, setFootGuideDismissed] = useGuideDismiss('fxlab-footer', false);
  const [dockGuideDismissed, setDockGuideDismissed] = useGuideDismiss('fxlab-dock', false);
  const showDockGuide = Boolean(v) && variantsDismissed && !dockGuideDismissed && !timelineOpen;
  const showFootGuide = Boolean(v) && variantsDismissed && dockGuideDismissed && !footGuideDismissed && !timelineOpen;
  useMarkGuideSeen('fxlab-dock', showDockGuide);
  useMarkGuideSeen('fxlab-footer', showFootGuide);

  // Таймлайн — на том же варианте; на время работы вариант «одалживается» в стор визарда.
  useEffect(() => () => { if (useFxLabStore.getState().snapshot) useFxLabStore.getState().endTimeline(); }, []);
  // общий флаг «таймлайн открыт»: по нему молчат подсказки шага FX (в т.ч. реактивация по простою)
  const setTimelineFlag = useFxTimelineOpen((s) => s.setOpen);
  useEffect(() => () => setTimelineFlag(false), [setTimelineFlag]);
  const openTimeline = () => { lab.beginTimeline(); setTimelineOpen(true); setTimelineFlag(true); };
  const closeTimeline = () => { lab.endTimeline(); setTimelineOpen(false); setTimelineFlag(false); };

  const pick = (option?: string) => {
    if (!v || !step) return;
    // Стиль в варианте ровно один: два стиля в одном ролике непонятно, где и как
    // сработают. Нужен другой стиль — это другой вариант («+ Вариант»).
    if (option) setCursor(Math.max(0, options.indexOf(option)));
    if (step.key === 'effectStyle') { lab.patch({ effectStyles: option ? [option] : [], effectStyle: option }); return; }
    // без перехода на следующую вкладку: человек может сравнить другие варианты этого шага
    lab.patch({ [step.key]: option } as Partial<HookConfig>);
  };

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      {timelineOpen && (
        <Suspense fallback={null}>
          <FxTimeline
            onClose={closeTimeline}
            tabs={<LabVariantSwitcher />}
          />
        </Suspense>
      )}
      <div className="card-2 flex min-h-0 flex-1 flex-col gap-space-5 px-space-6 py-space-6 max-lg:px-space-5">
        <div className="flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          <button type="button" onClick={openTimeline}
            className="flex h-[37px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent-light bg-grad-soft-20 px-[12px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125 disabled:opacity-40">
            <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true"><path d="M2 5h16M2 10h16M2 15h16M6 3v4m5 1v4m4 1v4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
            <span className="translate-y-px">{t('wizard.fx.timeline')}</span>
          </button>
        </div>

        {/* узкий экран: у колонки нет высоты — зона примера держит 9:16, как на проде */}
        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10 max-lg:aspect-[9/16] max-lg:w-full max-lg:flex-none">
          {v && <LabPreview previewId={previewId} />}
          {!v && (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[260px] text-center">{t('wizard.fx.empty')}</p>
            </div>
          )}
          {v && (
            <>
              <div className="absolute left-[14px] right-[14px] top-[14px] z-[4] flex items-start justify-between gap-[8px]">
                <div className="flex min-w-0 items-center gap-[8px] rounded-[10px] bg-[rgba(5,1,15,.58)] px-[12px] py-[7px] backdrop-blur-[10px]">
                  <i className="h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: v.color }} />
                  <span className="translate-y-px truncate text-[14px] text-text">{label(v)}</span>
                </div>
                {browsed && (
                  <div className="flex shrink-0 items-center gap-[8px] rounded-[10px] bg-[rgba(5,1,15,.58)] px-[12px] py-[7px] backdrop-blur-[10px]">
                    <span className="translate-y-px text-[13px] text-text-60">Пример</span>
                    <span className="translate-y-px text-[14px] text-text">{chip(browsed)}</span>
                    <span className="translate-y-px text-[12px] text-text-40">{cursor + 1}/{options.length}</span>
                    {browsed === selected && <span className="rounded-[6px] bg-accent px-[6px] py-[2px] text-[11px] text-white"><span className="inline-block translate-y-px">выбрано</span></span>}
                  </div>
                )}
              </div>
              {options.length > 1 && (
                <>
                  <button type="button" aria-label="Предыдущий пример" onClick={() => browse(-1)} className="absolute left-[12px] top-[38%] z-[4] flex h-[44px] w-[44px] -translate-y-1/2 items-center justify-center rounded-full bg-[rgba(5,1,15,.58)] text-text-80 backdrop-blur-[10px] transition hover:text-text active:scale-95">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="M14.5 6 8.5 12l6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </button>
                  <button type="button" aria-label="Следующий пример" onClick={() => browse(1)} className="absolute right-[12px] top-[38%] z-[4] flex h-[44px] w-[44px] -translate-y-1/2 items-center justify-center rounded-full bg-[rgba(5,1,15,.58)] text-text-80 backdrop-blur-[10px] transition hover:text-text active:scale-95">
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="M9.5 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </button>
                </>
              )}
              <div ref={dockRef} className="absolute inset-x-[12px] bottom-[12px] z-[4] flex flex-col gap-[12px] rounded-r15 bg-[rgba(5,1,15,.62)] p-[12px] backdrop-blur-[12px]">
                <div className="flex min-w-0 gap-[6px]" role="tablist" aria-label="Настройка варианта">
                  {steps.map((s, i) => {
                    const filled = stepFilled(s, config);
                    const value = s.key === 'effectStyle' ? style : (config[s.key] as string | undefined);
                    return (
                      <button key={s.key} type="button" role="tab" aria-selected={i === tab} onClick={() => lab.setTab(i)}
                        className={cn('flex min-w-0 flex-1 flex-col items-start gap-[2px] rounded-r10 px-[12px] py-[7px] text-left transition', i === tab ? 'bg-accent-20 shadow-[inset_0_0_0_1.5px_var(--accent-light)]' : 'bg-white/[0.06] hover:bg-white/[0.1]')}>
                        <span className="translate-y-px text-[11px] uppercase tracking-[.06em] text-text-40">{STEP_NAME[s.key]}</span>
                        <span className={cn('w-full translate-y-px truncate text-[15px]', filled ? 'text-text' : 'text-text-40')}>{value ? chip(value) : 'выбрать'}</span>
                      </button>
                    );
                  })}
                </div>
                {step?.key === 'effectStyle' && v.kind !== 'none' && style && (
                  <LabModifier
                    label="Где действует стиль"
                    value={config.effectStyleFull ? 'full' : 'pre'}
                    options={[['pre', 'До дропа'], ['full', 'Весь ролик']]}
                    onPick={(val) => lab.patch({ effectStyleFull: val === 'full' })}
                  />
                )}
                {step?.key === 'effectHook' && config.effectHook === 'Слоу-шаттер' && (
                  <LabModifier
                    label="Длина шлейфа"
                    value={config.effectHookExtend ?? ''}
                    options={[['', 'Стандарт'], ['after_drop:3', '3 кадра'], ['to_end', 'До конца']]}
                    onPick={(val) => lab.patch({ effectHookExtend: val as HookConfig['effectHookExtend'] })}
                  />
                )}
                {step && (step.options.length === 0
                  ? <p className="px-[4px] text-[14px] text-text-60">Загрузка своего звука или видео — как сейчас (в прототипе не подключено)</p>
                  : <ChipRow options={step.options} value={selected} onPick={pick} />)}
              </div>
            </>
          )}
        </div>
      </div>

      <div ref={footRef}>
        <PillsFooter
          pills={lab.variants.map((x) => ({
            key: x.id,
            label: label(x),
            icon: <SvgMaskIcon src={iconOf(x.kind).icon} style={{ width: 13, height: 15, color: 'var(--accent-light)' }} />,
            trail: <i className="ml-[10px] inline-block h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: x.color }} aria-hidden="true" />
          }))}
          activeKey={lab.activeId ?? undefined}
          emptyLabel={t('wizard.fx.add')}
          onPill={(key) => lab.select(key)}
          onPlus={() => lab.copyActive()}
          plusDisabled={!v}
          ready={ready}
          canContinue={canContinue}
          loading={loading}
          onBack={onBack}
          onNext={onNext}
          dragScroll={pillsScroll}
        />
      </div>

      <ActionGuideOverlay
        open={showDockGuide}
        targetRef={dockRef}
        title="Сначала посмотри примеры"
        text="Стрелки на видео листают примеры: эффекты, склейки, стили. Понравилось — выбери это в ленте ниже."
        dismissLabel="Дальше"
        progressLabel="Шаг 2 из 3"
        onDismiss={() => setDockGuideDismissed(true)}
        visual={<DockGuideVisual />}
        {...guideShell}
      />
      <ActionGuideOverlay
        open={showFootGuide}
        targetRef={footRef}
        title="Все варианты батча"
        text="Здесь переключаешься между вариантами. «+» делает копию текущего — поменяй в ней стиль или склейку."
        dismissLabel="Понятно"
        progressLabel="Шаг 3 из 3"
        onDismiss={() => setFootGuideDismissed(true)}
        visual={<FooterGuideVisual />}
        {...guideShell}
      />
    </aside>
  );
}

/* ── переключатель вариантов в шапке таймлайна: «вариант ▾» + приклеенный «+» ── */

function LabVariantSwitcher() {
  const lab = useFxLabStore();
  const label = useVariantLabel();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);
  const cur = lab.variants.find((x) => x.id === lab.activeId);
  const check = <svg className="ck" viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  return (
    <div ref={ref} className="fxt-vsw">
      <button type="button" className="fxt-vsw-main" aria-haspopup="listbox" aria-expanded={open} aria-label="Вариант рецепта" onClick={() => setOpen((o) => !o)}>
        {cur && <i style={{ background: cur.color }} />}
        <span className="lb tx">{cur ? label(cur) : 'Вариантов нет'}</span>
        <svg className="chev" viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <button type="button" className="fxt-vsw-plus" aria-label="Новый вариант" data-tip="Новый вариант" onClick={() => { lab.newInTimeline(); setOpen(false); }}>
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
      </button>
      {open && (
        <div className="fxt-vsw-menu" role="listbox" aria-label="Варианты">
          {lab.variants.map((x) => (
            <button key={x.id} type="button" role="option" aria-selected={x.id === lab.activeId} aria-current={x.id === lab.activeId} className="fxt-vsw-item" onClick={() => { lab.switchTimeline(x.id); setOpen(false); }}>
              <i style={{ background: x.color }} /><span className="lb tx">{label(x)}</span>
              {x.id === lab.activeId && check}
            </button>
          ))}
          <div className="fxt-vsw-sep" />
          <button type="button" className="fxt-vsw-item new" onClick={() => { lab.newInTimeline(); setOpen(false); }}>
            <span className="ic"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg></span>
            <span className="lb tx">Добавить вариант</span>
          </button>
        </div>
      )}
    </div>
  );
}

/* ── «Пул»: у каждого варианта своя пилюля и счётчик ──────────────────────── */

export function useLabPoolRows() {
  const lab = useFxLabStore();
  const label = useVariantLabel();
  return lab.variants
    .filter((v) => hookComplete(v.kind, v.config))
    .map((v) => ({ id: v.id, color: v.color, kind: v.kind, label: label(v), count: lab.counts[v.id] ?? 0, set: (n: number) => lab.setCount(v.id, n) }));
}
