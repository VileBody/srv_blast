import { useEffect, useRef, useState } from 'react';
import { create } from 'zustand';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { useChip } from '../../i18n/useChip';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { CatalogMedia } from './CatalogPreview';
import { WarmupInput } from './WarmupInput';
import { DROP_LEAD_S, dropLead, dropToSeconds, hookFitsDrop, timingToSeconds } from './useFragmentAudio';
import { useDragScroll } from './useDragScroll';
import { PillsFooter, Svg, W12 } from './WizardFrame';
import { FX_VARIANT_PALETTE, FxVariant, fxVariantsMode, HOOK_LABELS, variantsFromLegacyHooks, HookConfig, HookKind, hookComplete, useWizardStore } from '../../stores/wizardStore';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { useGuideAction, useGuideActed } from '../guidance/useGuideAction';
import {
  ChipRow, FX_PREVIEWS_STALE_MS, HOOK_TYPES, HookStep, HookTypeHead,
  hookSteps, NO_GLUE, NO_STYLE, previewIdFor, selectedStyles, styleLocksFullWindow
} from './hookCatalog';

// Таймлайн сам берёт каталоги из HookPanel — статический импорт дал бы цикл модулей.

/*
 * Режим вариантов шага FX (по умолчанию; `?fxLab=0` — откат на классический шаг).
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

/** Режим вариантов FX (по умолчанию; `?fxLab=0` — откат на классический шаг). */
export const useFxLab = () => fxVariantsMode();

/**
 * Черновик из классического шага FX (до выкатки вариантов) — разово в варианты, чтобы
 * настроенные хуки не пропали с экрана. Живёт на странице визарда: «Пул» может открыться
 * сразу, минуя шаг FX.
 */
export function useLegacyHooksToVariants(enabled: boolean) {
  const needs = useWizardStore((s) => enabled && s.fxVariants.length === 0 && Object.keys(s.hooks.configs).length > 0);
  useEffect(() => {
    if (!needs) return;
    const { hooks, allocation } = useWizardStore.getState();
    const variants = variantsFromLegacyHooks(hooks);
    if (!variants.length) return;
    // Доли пересчитает «Пул» (seeded = false). Классические конфиги после переноса убираем
    // (дроп остаётся): иначе, удалив все варианты, человек получил бы их обратно отсюда же.
    useWizardStore.setState({
      fxVariants: variants,
      hooks: { dropTime: hooks.dropTime, kind: undefined, configs: {} },
      allocation: { ...allocation, variants: {}, hooks: {}, styles: {}, seeded: false }
    });
  }, [needs]);
}

/** Вариант живёт в сторе визарда (fxVariants): переживает перезагрузку и едет на бэк. */
export type LabVariant = FxVariant;
const PALETTE = FX_VARIANT_PALETTE;
const STEP_NAME: Record<string, string> = {
  sound: 'wizard.fxv.stepSound', object: 'wizard.fxv.stepObject', effectHook: 'wizard.fxv.stepEffect', motion: 'wizard.fxv.stepMotion',
  thought: 'wizard.fxv.stepThought', effectGlue: 'wizard.fxv.stepGlue', effectStyle: 'wizard.fxv.stepStyle'
};

/** Только состояние интерфейса; сами варианты и их доли — в сторе визарда. */
interface LabState {
  activeId: string | null;
  expanded: HookKind | null;
  /**
   * Тип раскрыт, а варианта у него ещё нет: док показывает шаги на пустом конфиге, вариант
   * появляется на первом настоящем выборе. Раньше пустой «— новый» заводился сразу при
   * раскрытии и потом молча блокировал «Продолжить».
   */
  pendingKind: HookKind | null;
  /** последний удалённый вариант — для «Вернуть» (удаление в один клик, без подтверждения) */
  removed: { variant: LabVariant; index: number; count: number } | null;
  tab: number;
  /**
   * Варианты, которые человек довёл до конца сам («Готово» на последней вкладке) или
   * открыл уже готовыми. Сама полнота конфига тут не годится: курсор просмотра выбирает
   * пример сразу, и вариант «полон» с первого же пролистанного стиля — а подсказка про
   * варианты должна ждать, пока человек закончит, а не всплывать посреди просмотра.
   */
  confirmed: Record<string, true>;
  /** Счётчик «покажи рабочую зону»: растёт, когда человек раскрыл тип хука (см. LabWorkZone). */
  focusSeq: number;
  select: (id: string) => void;
  toggleType: (kind: HookKind) => void;
  startPending: (kind: HookKind) => void;
  add: (kind: HookKind, config?: HookConfig, tab?: number) => void;
  copyActive: () => void;
  remove: (id: string) => void;
  undoRemove: () => void;
  dismissRemoved: () => void;
  patch: (patch: Partial<HookConfig>) => void;
  setTab: (tab: number) => void;
  setCount: (id: string, n: number) => void;
  confirm: (id: string) => void;
  requestFocus: () => void;
}

/** Id переживают перезагрузку (они ключи allocation.variants) — счётчик модуля тут не годится. */
const newId = () => `v-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const variantsNow = () => useWizardStore.getState().fxVariants;
const setVariants = (next: FxVariant[]) => useWizardStore.setState({ fxVariants: next });
/** Первый свободный цвет палитры: после удаления варианта цвета не повторяются. */
const nextColor = (list: FxVariant[]) => PALETTE.find((c) => !list.some((v) => v.color === c)) ?? PALETTE[list.length % PALETTE.length];
const setCounts = (patch: (counts: Record<string, number>) => Record<string, number>) => {
  const { allocation, setAllocation } = useWizardStore.getState();
  setAllocation({ variants: patch(allocation.variants ?? {}) });
};

function stepFilled(step: HookStep, config: HookConfig): boolean {
  return step.key === 'effectStyle' ? selectedStyles(config).length > 0 : Boolean(config[step.key]);
}
function firstOpenTab(v?: LabVariant): number {
  if (!v) return 0;
  const i = hookSteps(v.kind).findIndex((step) => !stepFilled(step, v.config));
  return i < 0 ? 0 : i;
}

/** Правка конкретного варианта (загрузка прогрева асинхронная — активный вариант мог смениться). */
const patchVariant = (id: string, patch: Partial<HookConfig>) =>
  setVariants(variantsNow().map((v) => (v.id === id ? { ...v, config: { ...v.config, ...patch } } : v)));

export const useFxLabStore = create<LabState>((set, get) => ({
  activeId: null,
  expanded: null,
  pendingKind: null,
  removed: null,
  tab: 0,
  confirmed: {},
  focusSeq: 0,
  select: (id) => set((s) => {
    const v = variantsNow().find((x) => x.id === id);
    // открыл уже готовый вариант (в т.ч. восстановленный после перезагрузки) — он и есть «доведённый»
    const confirmed = v && hookComplete(v.kind, v.config) && !s.confirmed[id] ? { ...s.confirmed, [id]: true as const } : s.confirmed;
    return { activeId: id, expanded: v?.kind ?? s.expanded, pendingKind: null, tab: firstOpenTab(v), confirmed };
  }),
  toggleType: (kind) => {
    const s = get();
    if (s.expanded === kind) { set({ expanded: null, pendingKind: null }); return; }
    const first = variantsNow().find((v) => v.kind === kind && !v.draft);
    if (first) get().select(first.id);
    else get().startPending(kind);
    // выбрал тип — дальше смотреть примеры: на телефоне рабочая зона ниже списка, ведём к ней
    get().requestFocus();
  },
  startPending: (kind) => set({ expanded: kind, activeId: null, pendingKind: kind, tab: 0 }),
  add: (kind, config = {}, tab) => {
    const list = variantsNow();
    const v: LabVariant = { id: newId(), kind, config, color: nextColor(list) };
    setVariants([...list, v]);
    setCounts((c) => ({ ...c, [v.id]: 1 }));
    set({ activeId: v.id, expanded: kind, pendingKind: null, tab: tab ?? firstOpenTab(v) });
  },
  copyActive: () => {
    const list = variantsNow();
    const src = list.find((v) => v.id === get().activeId);
    if (!src) return;
    // Копия — целиком, со стилем: «Круг · Без склейки · Без стилизации» раньше терял
    // «Без стилизации» и получал метку «настроить». Отличие человек вносит сам, в копии.
    const v: LabVariant = { id: newId(), kind: src.kind, config: structuredClone(src.config), color: nextColor(list), recipe: src.recipe ? structuredClone(src.recipe) : undefined };
    setVariants([...list, v]);
    setCounts((c) => ({ ...c, [v.id]: 1 }));
    set({ activeId: v.id, expanded: v.kind, pendingKind: null, tab: hookSteps(v.kind).length - 1 });
  },
  remove: (id) => {
    const s = get();
    const list = variantsNow();
    const index = list.findIndex((v) => v.id === id);
    if (index < 0) return;
    const count = useWizardStore.getState().allocation.variants?.[id] ?? 1;
    const variants = list.filter((v) => v.id !== id);
    setVariants(variants);
    setCounts(({ [id]: _gone, ...rest }) => rest);
    const activeId = s.activeId === id ? (variants.find((v) => v.kind === s.expanded) ?? variants[0])?.id ?? null : s.activeId;
    set({ activeId, removed: { variant: list[index], index, count } });
  },
  undoRemove: () => {
    const r = get().removed;
    if (!r) return;
    const list = variantsNow();
    if (list.some((v) => v.id === r.variant.id)) { set({ removed: null }); return; }
    // цвет мог уйти новому варианту — тогда берём свободный, чтобы два варианта не слились
    const variant = list.some((v) => v.color === r.variant.color) ? { ...r.variant, color: nextColor(list) } : r.variant;
    const next = [...list];
    next.splice(Math.min(r.index, next.length), 0, variant);
    setVariants(next);
    setCounts((c) => ({ ...c, [variant.id]: r.count }));
    set({ removed: null, activeId: variant.id, expanded: variant.kind, pendingKind: null, tab: firstOpenTab(variant) });
  },
  dismissRemoved: () => set({ removed: null }),
  patch: (patch) => { const id = get().activeId; setVariants(variantsNow().map((v) => (v.id === id ? { ...v, config: { ...v.config, ...patch } } : v))); },
  setTab: (tab) => set({ tab }),
  setCount: (id, n) => setCounts((c) => ({ ...c, [id]: Math.max(0, n) })),
  confirm: (id) => set((s) => (s.confirmed[id] ? s : { confirmed: { ...s.confirmed, [id]: true } })),
  requestFocus: () => set((s) => ({ focusSeq: s.focusSeq + 1 }))
}));

/**
 * Варианты для экрана}));

/**
 * Варианты для экрана. Черновики (вариант без хука) остались от прежнего таймлайна шага
 * FX — их убираем при входе, в пул и рендер они не попадают.
 */
export function useLabVariants(): LabVariant[] {
  return useWizardStore((s) => s.fxVariants);
}
export function useDropStaleDrafts() {
  useEffect(() => {
    const list = variantsNow();
    if (!list.some((v) => v.draft)) return;
    const drafts = list.filter((v) => v.draft).map((v) => v.id);
    setVariants(list.filter((v) => !v.draft));
    setCounts((c) => Object.fromEntries(Object.entries(c).filter(([k]) => !drafts.includes(k))));
  }, []);
}

/** Название хука варианта (для чипа «Комбинаций»): «Молния», «Свайп», «Без хука». */
export function variantHookLabel(v: LabVariant, chip: (label: string) => string): string {
  const c = v.config;
  const hook = v.kind === 'none' ? HOOK_LABELS.none : (c.sound ?? c.object ?? c.effectHook ?? c.motion ?? c.thought);
  return chip(hook ?? HOOK_LABELS[v.kind]);
}

/** Короткое описание варианта: хук · склейка · стиль. */
export function useVariantLabel() {
  const chip = useChip();
  const { t } = useTranslation();
  return (v: LabVariant) => {
    if (v.draft) return t('wizard.fxv.draft');
    const c = v.config;
    const hook = v.kind === 'none' ? HOOK_LABELS.none : (c.sound ?? c.object ?? c.effectHook ?? c.motion ?? c.thought);
    const style = selectedStyles(c)[0];
    const parts = [hook ? chip(hook) : null, c.effectGlue ? chip(c.effectGlue) : null, style ? chip(style) : null].filter(Boolean);
    return parts.length ? parts.join(' · ') : t('wizard.fxv.newOf', { hook: chip(HOOK_LABELS[v.kind]) });
  };
}

/** сколько живёт «Вернуть» после удаления варианта */
const UNDO_MS = 8000;
/** сколько человек не листает примеры, прежде чем над доком появится «Дальше ✓» */
const NEXT_IDLE_MS = 2500;
const iconOf = (kind: HookKind) => HOOK_TYPES.find((item) => item.kind === kind)!;
const guideShell = { variant: 'visual' as const, shell: 'track-top' as const };

/*
 * Тур шага FX в прототипе — одна цепочка на обе колонки, и каждый шаг ждёт действия
 * предыдущего (useGuideAction): дроп → тип → «посмотри примеры» (сразу после выбора типа)
 * → [человек доводит вариант до конца: хук, склейка, стиль] → варианты → [завёл второй
 * вариант или потрогал список] → футер. Раньше «Варианты» шли до дока и всплывали на
 * первом же выборе примера. Id со своим префиксом: у старых hook-drop / hook-type «видел»
 * уже записан у всех, кто проходил прежний тур, и на новом маршруте они молча не показывались.
 */
export const FX_LAB_TOUR = ['drop', 'type', 'dock', 'variants', 'footer'] as const;
export type FxLabTourStep = typeof FX_LAB_TOUR[number];
export const fxLabGuideId = (step: FxLabTourStep) => `fx2-${step}`;
export function useFxLabTourProgress() {
  const { t } = useTranslation();
  const total = FX_LAB_TOUR.length;
  return (step: FxLabTourStep) => t('wizard.guideProgress', { current: FX_LAB_TOUR.indexOf(step) + 1, total });
}

/* ── мини-визуалы подсказок (язык тот же: появление по очереди, одна анимация) ── */

function VariantsGuideVisual() {
  const { t } = useTranslation();
  const rows = [['#8b6fe6', 'Молния · Щелчок · Неон'], ['#e38fb5', 'Молния · Щелчок · Ч/Б']];
  return (
    <div className="flex w-full flex-col gap-[6px]" aria-hidden="true">
      {rows.map(([c, l], i) => (
        <span key={l} className={cn('guide-mode-reveal flex h-[24px] items-center gap-[7px] rounded-[7px] px-[8px] text-[11px] leading-none text-white/85', i ? 'guide-mode-delay-2 bg-white/[0.06]' : 'guide-mode-delay-1 bg-accent-20 shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
          <i className="h-[7px] w-[7px] rounded-full" style={{ background: c }} /><span>{l}</span>
          <span className="ml-auto text-white/50">×</span>
        </span>
      ))}
      <span className="guide-mode-reveal guide-mode-delay-3 flex h-[22px] w-fit items-center gap-[5px] rounded-[7px] bg-white/[0.08] px-[8px] text-[11px] leading-none text-white/80"><span>+</span><span>{t('wizard.fxv.add')}</span></span>
    </div>
  );
}
function DockGuideVisual() {
  const { t } = useTranslation();
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
          <span>{t('wizard.fxv.visBrowse')}</span>
          <span className="guide-sb-press flex h-[18px] w-[18px] items-center justify-center rounded-full bg-white/10">›</span>
        </span>
        <span className="guide-mode-reveal guide-mode-delay-3 flex gap-[4px]">
          {['Щелчок', 'Минимакс', 'Вспышка'].map((l, i) => (
            <span key={l} className={cn('truncate rounded-[6px] px-[6px] py-[4px] text-[9px] leading-none', i === 1 ? 'bg-accent-20 text-white shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'bg-white/[0.07] text-white/60')}><span className="inline-block">{l}</span></span>
          ))}
        </span>
        <span className="guide-mode-reveal guide-mode-delay-4 text-[10px] leading-none text-white/60"><span className="inline-block">{t('wizard.fxv.visPick')}</span></span>
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

export function LabTypeList({ locked, dropLeadS }: { locked: boolean; dropLeadS: number | null }) {
  const { t } = useTranslation();
  const lab = useFxLabStore();
  const allVariants = useLabVariants();
  const label = useVariantLabel();
  const [hint, setHint] = useState<HookKind | null>(null);
  useDropStaleDrafts();
  const openRef = useRef<HTMLDivElement>(null);
  // После перезагрузки варианты на месте, а выбор интерфейса — нет: открываем первый.
  // Раскрытый тип без варианта (pendingKind) — осознанное состояние, его не перебиваем.
  useEffect(() => {
    if (lab.pendingKind) return;
    if (lab.activeId && allVariants.some((v) => v.id === lab.activeId)) return;
    const first = allVariants.find((v) => !v.draft);
    if (first) lab.select(first.id);
  }, [allVariants, lab]);
  // Дроп сдвинули раньше, чем нужно раскрытому типу: закрываем его, пока там не завели вариант
  useEffect(() => {
    if (lab.expanded && dropLeadS !== null && !hookFitsDrop(lab.expanded, dropLeadS)) lab.toggleType(lab.expanded);
  }, [dropLeadS, lab]);
  // «Вернуть» живёт несколько секунд; уход со шага его гасит
  const removed = lab.removed;
  useEffect(() => {
    if (!removed) return undefined;
    const id = window.setTimeout(() => useFxLabStore.getState().dismissRemoved(), UNDO_MS);
    return () => window.clearTimeout(id);
  }, [removed]);
  useEffect(() => () => useFxLabStore.getState().dismissRemoved(), []);

  // Шаг «Варианты»: после «Посмотри примеры» (док — в рабочей зоне, поэтому его ЖИВОЙ
  // dismissed) и только когда человек довёл вариант до конца — действие того шага.
  const hasOpen = Boolean(lab.expanded && allVariants.some((v) => v.kind === lab.expanded && !v.draft));
  const dockDismissed = useGuideLiveDismissed(fxLabGuideId('dock'));
  const dockActed = useGuideActed(fxLabGuideId('dock'));
  const progress = useFxLabTourProgress();
  // Док может показывать вариант другого типа, чем раскрытый (раскрыли пустой тип, а
  // довели вариант соседнего) — шаг всё равно наступает: вариант доведён до конца.
  const hasActive = Boolean(lab.activeId && allVariants.some((v) => v.id === lab.activeId && !v.draft));
  const variantsTurn = (hasOpen || hasActive) && dockDismissed && dockActed;
  const [variantsGuideDismissed, setVariantsGuideDismissed] = useGuideDismiss(fxLabGuideId('variants'), false);
  const showVariantsGuide = variantsTurn && !variantsGuideDismissed;
  useMarkGuideSeen(fxLabGuideId('variants'), showVariantsGuide);
  // Действие шага: завёл ещё вариант (или потрогал список вариантов) — тогда и подсказка
  // про футер, где между ними переключаются, к месту.
  const variantCount = allVariants.filter((v) => !v.draft).length;
  useGuideAction(fxLabGuideId('variants'), variantsTurn, { done: variantCount > 1, targetRef: openRef });
  const prevCountRef = useRef(variantCount);
  useEffect(() => {
    if (showVariantsGuide && variantCount > prevCountRef.current) setVariantsGuideDismissed(true);
    prevCountRef.current = variantCount;
  });

  return (
    <>
      <div className="w12-fx-types">
        {HOOK_TYPES.map((item) => {
          const variants = allVariants.filter((v) => v.kind === item.kind && !v.draft);
          const open = lab.expanded === item.kind;
          const has = variants.length > 0;
          // Дроп оставил типу меньше трека, чем ему нужно (DROP_LEAD_S): такой хук не настраивается
          const shortLead = dropLeadS !== null && !hookFitsDrop(item.kind, dropLeadS);
          const isLocked = (locked && item.kind !== 'none') || shortLead;
          return (
            <div key={item.kind} ref={open ? openRef : undefined} className={cn('w12-fx-type', (open || has) && 'w12-on', open && 'w12-open', isLocked && 'w12-locked')}>
              {/* строка типа: иконка · название · «?» рядом с названием · метки вариантов · стрелка справа */}
              <HookTypeHead
                item={item}
                locked={isLocked}
                expanded={open}
                onToggle={() => lab.toggleType(item.kind)}
                hintOpen={hint === item.kind}
                onHint={(show) => setHint(show ? item.kind : null)}
              >
                {has && (
                  <span className="w12-fx-dots" aria-label={t('wizard.fxv.count', { count: variants.length })}>
                    {variants.slice(0, 5).map((v) => <i key={v.id} className="w12-fx-dot" style={{ background: v.color }} />)}
                  </span>
                )}
                <span className="w12-fx-chev" aria-hidden="true"><Svg>{W12.down}</Svg></span>
              </HookTypeHead>
              {shortLead && <p className="w12-fx-type-note">{t('wizard.fx.kindNeedsLead', { seconds: DROP_LEAD_S[item.kind] })}</p>}
              {open && (
                <div className="w12-fx-vars">
                  {variants.map((v) => {
                    const active = v.id === lab.activeId;
                    const done = hookComplete(v.kind, v.config);
                    return (
                      <div key={v.id} className="w12-fx-var" data-active={active || undefined}>
                        <button type="button" aria-current={active} className="w12-fx-var-main" onClick={() => lab.select(v.id)}>
                          <i className="w12-fx-dot" style={{ background: v.color }} />
                          <span className="w12-fx-var-name w12-l">{label(v)}</span>
                          {!done && <span className="w12-chip w12-warn w12-chip-sm"><span className="w12-l">{t('wizard.fxv.configure')}</span></span>}
                        </button>
                        <button type="button" className="w12-icon-btn" aria-label={t('wizard.fxv.remove')} title={t('wizard.fxv.remove')} onClick={() => lab.remove(v.id)}>
                          <Svg>{W12.close}</Svg>
                        </button>
                      </div>
                    );
                  })}
                  {/* Вариант рождается на первом выборе примера. Пока его нет, «+ Вариант · копия
                      текущего» копировать нечего, а на телефоне его жали вместо примеров — вместо
                      него ссылка к рабочей зоне. Сам «+» — второстепенный, не главная кнопка. */}
                  {variants.length ? (
                    <button type="button" className="w12-fx-add" onClick={() => lab.copyActive()}>
                      <Svg>{W12.plus}</Svg>
                      <span className="w12-l">{t('wizard.fxv.add')}</span>
                      <small>{t('wizard.fxv.addHint')}</small>
                    </button>
                  ) : (
                    <button type="button" className="w12-fx-add w12-fx-goto" onClick={() => lab.requestFocus()}>
                      <span className="w12-l">{t('wizard.fxv.toExamples')}</span>
                      <small>{t('wizard.fxv.toExamplesHint')}</small>
                    </button>
                  )}
                </div>
              )}
              {removed?.variant.kind === item.kind && (
                <div className="w12-fx-undo" role="status">
                  <span className="w12-fx-undo-text">{t('wizard.fxv.removedVariant', { label: label(removed.variant) })}</span>
                  <button type="button" className="w12-link" onClick={lab.undoRemove}>{t('wizard.fxv.undoRemove')}</button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <ActionGuideOverlay
        open={showVariantsGuide}
        targetRef={openRef}
        title={t('wizard.fxv.guideVariantsTitle')}
        text={t('wizard.fxv.guideVariantsText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={progress('variants')}
        onDismiss={() => setVariantsGuideDismissed(true)}
        visual={<VariantsGuideVisual />}
        {...guideShell}
      />
    </>
  );
}

/* ── уточнение выбранного (где действует стиль, длина шлейфа): строка в доке ── */

function LabModifier({ label, value, options, onPick, disabledValues, note }: { label: string; value: string; options: [string, string][]; onPick: (value: string) => void; disabledValues?: string[]; note?: string }) {
  return (
    <div className="w12-fx-mod">
      <span>{label}</span>
      <span className="w12-seg" role="group" aria-label={label} title={note}>
        {options.map(([val, text]) => (
          <button key={val || 'std'} type="button" aria-pressed={value === val} disabled={disabledValues?.includes(val)} onClick={() => onPick(val)} className="w12-seg-btn w12-seg-text">
            <span className="w12-l">{text}</span>
          </button>
        ))}
      </span>
    </div>
  );
}

/* ── рабочая зона: пример во всю высоту, настройка — доком внутри него ─────── */

function LabPreview({ previewId }: { previewId?: string }) {
  const query = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews, staleTime: FX_PREVIEWS_STALE_MS });
  if (!previewId || query.isLoading) return null;
  const effect = query.data?.previews.find((item) => item.id === previewId);
  // cover: пример заполняет рабочую зону целиком, а не висит полосой посередине
  return <div className="w12-fx-media"><CatalogMedia url={effect?.previewUrl} className="w12-fx-media-el" /></div>;
}

export function LabWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const lab = useFxLabStore();
  const allVariants = useLabVariants();
  const label = useVariantLabel();
  const pillsScroll = useDragScroll();
  const dockRef = useRef<HTMLDivElement>(null);
  const footRef = useRef<HTMLDivElement>(null);
  const stored = allVariants.find((x) => x.id === lab.activeId);
  // Тип раскрыт, варианта ещё нет: показываем его шаги на пустом конфиге — вариант родится
  // на первом выборе (pick / загрузка прогрева), а не при раскрытии.
  const v: LabVariant | undefined = stored ?? (lab.pendingKind ? { id: `pending-${lab.pendingKind}`, kind: lab.pendingKind, config: {}, color: nextColor(allVariants) } : undefined);
  const steps = v ? hookSteps(v.kind) : [];
  const tab = Math.min(lab.tab, Math.max(0, steps.length - 1));
  const step = steps[tab];
  const config = v?.config ?? {};
  const style = selectedStyles(config)[0];
  const selected = step ? (step.key === 'effectStyle' ? style : (config[step.key] as string | undefined)) : undefined;
  // Курсор просмотра = выбор: стрелки на видео листают примеры, и тот, на котором человек
  // остановился, сразу становится выбранным (раньше выбор был отдельным — приходилось искать
  // этот же вариант в ленте и жать его). Лента сама доезжает до выбранного.
  const options = step?.options ?? [];
  const [cursor, setCursor] = useState(0);
  useEffect(() => {
    const i = selected ? options.indexOf(selected) : -1;
    setCursor(i < 0 ? 0 : i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v?.id, tab]);
  const browsed = options[Math.min(cursor, Math.max(0, options.length - 1))];
  const previewId = step && browsed ? previewIdFor(step.key, browsed) : undefined;
  const browse = (d: number) => {
    if (!options.length) return;
    const next = (Math.min(cursor, options.length - 1) + d + options.length) % options.length;
    setCursor(next);
    pick(options[next]);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.target instanceof HTMLElement) || e.target.matches('input, textarea, select')) return;
      if (e.key === 'ArrowLeft') browse(-1);
      if (e.key === 'ArrowRight') browse(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  /*
   * Шаг «Посмотри примеры» — сразу после выбора типа (подсказка про тип закрывается самим
   * раскрытием, её живой dismissed — в левой панели). Закрывается первым же действием в доке.
   * Его действие — вариант доведён до конца («Готово» на последней вкладке): только тогда
   * дальше идут «Варианты» (левая панель), а за ними — «Футер».
   */
  const progress = useFxLabTourProgress();
  const typeDismissed = useGuideLiveDismissed(fxLabGuideId('type'));
  const variantsDismissed = useGuideLiveDismissed(fxLabGuideId('variants'));
  const variantsActed = useGuideActed(fxLabGuideId('variants'));
  const [footGuideDismissed, setFootGuideDismissed] = useGuideDismiss(fxLabGuideId('footer'), false);
  const [dockGuideDismissed, setDockGuideDismissed] = useGuideDismiss(fxLabGuideId('dock'), false);
  const dockTurn = Boolean(v) && typeDismissed;
  const showDockGuide = dockTurn && !dockGuideDismissed;
  const variantDone = Boolean(stored && hookComplete(stored.kind, stored.config) && lab.confirmed[stored.id]);
  useGuideAction(fxLabGuideId('dock'), dockTurn, { done: variantDone });
  const showFootGuide = Boolean(v) && variantsDismissed && variantsActed && !footGuideDismissed;
  useMarkGuideSeen(fxLabGuideId('dock'), showDockGuide);
  useMarkGuideSeen(fxLabGuideId('footer'), showFootGuide);
  // Подсказка дока прочитана — человек уже листает/выбирает: убираем её с примера.
  const touchDock = () => { if (showDockGuide) setDockGuideDismissed(true); };

  const pick = (option?: string) => {
    if (!v || !step) return;
    touchDock();
    // Стиль в варианте ровно один: два стиля в одном ролике непонятно, где и как
    // сработают. Нужен другой стиль — это другой вариант («+ Вариант»).
    if (option) setCursor(Math.max(0, options.indexOf(option)));
    const patch = (step.key === 'effectStyle' ? { effectStyles: option ? [option] : [], effectStyle: option } : { [step.key]: option }) as Partial<HookConfig>;
    // первый настоящий выбор заводит вариант; снимать выбор у ещё не созданного нечего
    if (!stored) { if (option) lab.add(v.kind, patch, tab); return; }
    // без перехода на следующую вкладку: человек может сравнить другие варианты этого шага
    lab.patch(patch);
  };
  // Дроп на самом старте отрывка (раньше полсекунды): «до дропа» стилю ложиться не на что —
  // он идёт на весь ролик, и вариант это хранит явно, чтобы экран и рендер совпадали.
  const dropAtStart = useWizardStore((state) => {
    const lead = dropLead(dropToSeconds(state.hooks.dropTime), timingToSeconds(state.timingFrom), timingToSeconds(state.timingTo));
    return lead !== null && lead < 0.5;
  });
  const needsFullScope = Boolean(stored && dropAtStart && style && !stored.config.effectStyleFull);
  useEffect(() => { if (needsFullScope) lab.patch({ effectStyleFull: true }); }, [needsFullScope, lab]);
  const styleLocked = styleLocksFullWindow(style) || dropAtStart;

  /*
   * «Дальше ✓»: вкладки дока (Эффект → Склейка → Стиль) не замечали — листали примеры и не
   * понимали, как идти дальше. Выбор уже делает сам просмотр, поэтому, когда человек перестал
   * листать на заполненной вкладке, над доком появляется кнопка: следующая вкладка, а на
   * последней — «Готово» (вариант доведён; это и есть действие подсказки «Посмотри примеры»).
   */
  const stepIsFilled = Boolean(stored && step && stepFilled(step, config));
  const complete = Boolean(stored && hookComplete(stored.kind, stored.config));
  const lastTab = tab >= steps.length - 1;
  // на последней вкладке недонастроенного варианта (вкладку открыли кликом, пропустив шаг) ведём на пропущенную
  const nextTab = lastTab ? (complete ? -1 : firstOpenTab(stored)) : tab + 1;
  const [nextReady, setNextReady] = useState(false);
  useEffect(() => {
    setNextReady(false);
    if (!stepIsFilled) return undefined;
    const timer = window.setTimeout(() => setNextReady(true), NEXT_IDLE_MS);
    return () => window.clearTimeout(timer);
  }, [stored?.id, tab, cursor, selected, stepIsFilled]);
  const goNext = () => {
    setNextReady(false);
    if (!stored) return;
    touchDock();
    if (nextTab >= 0) lab.setTab(nextTab);
    else lab.confirm(stored.id);
  };

  /* Раскрыли тип хука — ведём к примерам: на телефоне рабочая зона под списком типов, и без
     этого человек жал «+ Вариант» вместо того, чтобы смотреть примеры. Док коротко подсвечивается. */
  const stageRef = useRef<HTMLDivElement>(null);
  const handledFocusRef = useRef(lab.focusSeq);
  const [attention, setAttention] = useState(false);
  useEffect(() => {
    if (lab.focusSeq === handledFocusRef.current) return undefined;
    handledFocusRef.current = lab.focusSeq;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // аккордеон слева только что раскрылся и сдвинул страницу — скроллим после раскладки;
    // nearest — на широком экране зона и так видна, и страница не дёргается
    const scroll = window.setTimeout(() => {
      stageRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'nearest', inline: 'nearest' });
    }, 60);
    setAttention(true);
    const off = window.setTimeout(() => setAttention(false), 1400);
    return () => { window.clearTimeout(scroll); window.clearTimeout(off); };
  }, [lab.focusSeq]);

  // «+» футера копирует текущий вариант, а общий PillsFooter (WizardFrame — вне этой правки)
  // подписывает его «Перейти к следующему разделу». Пока у футера нет своего пропа подписи,
  // ставим верную подпись здесь; React её не перетрёт — его проп не меняется между рендерами.
  const copyLabel = t('wizard.fxv.copyVariant');
  useEffect(() => {
    const plus = footRef.current?.querySelector<HTMLButtonElement>('.w12-sum-plus');
    if (!plus) return;
    plus.setAttribute('aria-label', copyLabel);
    plus.title = copyLabel;
  });

  return (
    <aside className="w12-col-aside">
      <div className="w12-card w12-pv-card">
        <div className="w12-aside-head">
          <h2>{t('wizard.workZone')}</h2>
        </div>

        {/* пример во всю высоту зоны; узкий экран — 9:16, как у превью фона */}
        <div ref={stageRef} className="w12-fx-stage">
          {v && <LabPreview previewId={previewId} />}
          {/* «Без склейки» / «Без стилизации» — осознанный отказ, видео-примера у него нет:
              без подписи пустой кадр выглядел как недогрузившийся */}
          {v && (browsed === NO_GLUE || browsed === NO_STYLE) && (
            <div className="w12-fx-none" role="status">
              <b className="w12-l">{chip(browsed)}</b>
              <span>{t(browsed === NO_GLUE ? 'wizard.fxv.noneGlueHint' : 'wizard.fxv.noneStyleHint')}</span>
            </div>
          )}
          {!v && <div className="w12-empty">{t('wizard.fx.empty')}</div>}
          {v && (
            <>
              <div className="w12-fx-tags">
                <span className="w12-fx-tag"><i className="w12-fx-dot" style={{ background: v.color }} /><span className="w12-l">{label(v)}</span></span>
                {browsed && (
                  <span className="w12-fx-tag">
                    <span className="w12-fx-tag-dim">{t('wizard.fxv.example')}</span>
                    <span className="w12-l">{chip(browsed)}</span>
                    <span className="w12-fx-tag-dim w12-num">{cursor + 1}/{options.length}</span>
                    {browsed === selected && <em className="w12-fx-picked"><span className="w12-l">{t('wizard.fxv.picked')}</span></em>}
                  </span>
                )}
              </div>
              {options.length > 1 && (
                <>
                  <button type="button" className="w12-rail-btn w12-l w12-fx-nav" aria-label={t('wizard.fxv.prev')} onClick={() => browse(-1)}><Svg>{W12.left}</Svg></button>
                  <button type="button" className="w12-rail-btn w12-r w12-fx-nav" aria-label={t('wizard.fxv.next')} onClick={() => browse(1)}><Svg>{W12.right}</Svg></button>
                </>
              )}
              <div ref={dockRef} className="w12-fx-dock" data-attn={attention || undefined}>
                {nextReady && (
                  <button type="button" className="w12-fx-next" onClick={goNext}>
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    <span className="w12-fx-next-l">{nextTab >= 0 ? t('wizard.fxv.stepNext', { step: t(STEP_NAME[steps[nextTab].key]) }) : t('wizard.fxv.stepDone')}</span>
                  </button>
                )}
                <div className="w12-fx-steps" role="tablist" aria-label={t('wizard.fxv.settings')}>
                  {steps.map((s, i) => {
                    const filled = stepFilled(s, config);
                    const value = s.key === 'effectStyle' ? style : (config[s.key] as string | undefined);
                    return (
                      <button key={s.key} type="button" role="tab" aria-selected={i === tab} className="w12-fx-step" onClick={() => { touchDock(); lab.setTab(i); }}>
                        <small>{t(STEP_NAME[s.key])}</small>
                        <span className={cn(!filled && 'w12-off')}>{value ? chip(value) : t('wizard.fxv.choose')}</span>
                      </button>
                    );
                  })}
                </div>
                {step?.key === 'effectStyle' && v.kind !== 'none' && style && (
                  // ЧБ и подобные рендер всегда тянет на весь ролик: «До дропа» у них заблокировано,
                  // как в классическом шаге (StyleScopeToggle), — иначе экран обещал бы другое
                  <LabModifier
                    label={t('wizard.fxv.styleScope')}
                    value={styleLocked || config.effectStyleFull ? 'full' : 'pre'}
                    options={[['pre', t('wizard.fxv.scopePre')], ['full', t('wizard.fxv.scopeFull')]]}
                    disabledValues={styleLocked ? ['pre'] : undefined}
                    note={dropAtStart ? t('wizard.fx.scopeDropAtStart') : styleLocked ? t('wizard.fx.scopeLocked') : undefined}
                    onPick={(val) => lab.patch({ effectStyleFull: val === 'full' })}
                  />
                )}
                {step?.key === 'effectHook' && config.effectHook === 'Слоу-шаттер' && (
                  <LabModifier
                    label={t('wizard.fxv.trail')}
                    value={config.effectHookExtend ?? ''}
                    options={[['', t('wizard.fxv.trailStd')], ['after_drop:3', t('wizard.fxv.trail3')], ['to_end', t('wizard.fxv.trailEnd')]]}
                    onPick={(val) => lab.patch({ effectHookExtend: val as HookConfig['effectHookExtend'] })}
                  />
                )}
                {step && (step.key === 'sound'
                  ? (
                    <WarmupInput
                      key={v.id}
                      uploadKey={v.id}
                      value={config}
                      // у ещё не созданного варианта выбор «звук/видео» живёт в самом вводе, вариант
                      // заводит готовая загрузка; у созданного — правка именно его (загрузка асинхронна)
                      onPatch={(patch) => { touchDock(); if (stored) patchVariant(stored.id, patch); else if (patch.sound) lab.add(v.kind, patch, tab); }}
                    />
                  )
                  : <ChipRow options={step.options} value={selected} onPick={pick} />)}
              </div>
            </>
          )}
        </div>
      </div>

      <div ref={footRef}>
        <PillsFooter
          pills={allVariants.filter((x) => !x.draft).map((x) => ({
            key: x.id,
            label: label(x),
            icon: <SvgMaskIcon src={iconOf(x.kind).icon} style={{ width: 13, height: 15, color: 'var(--accent-light)' }} />,
            trail: <i className="ml-[10px] inline-block h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: x.color }} aria-hidden="true" />
          }))}
          activeKey={lab.activeId ?? undefined}
          emptyLabel={t('wizard.fx.add')}
          onPill={(key) => lab.select(key)}
          onPlus={() => lab.copyActive()}
          plusDisabled={!stored}
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
        title={t('wizard.fxv.guideDockTitle')}
        text={t('wizard.fxv.guideDockText')}
        dismissLabel={t('wizard.fx.guideNext')}
        progressLabel={progress('dock')}
        onDismiss={() => setDockGuideDismissed(true)}
        visual={<DockGuideVisual />}
        {...guideShell}
      />
      <ActionGuideOverlay
        open={showFootGuide}
        targetRef={footRef}
        title={t('wizard.fxv.guideFooterTitle')}
        text={t('wizard.fxv.guideFooterText')}
        dismissLabel={t('wizard.fx.guideDismiss')}
        progressLabel={progress('footer')}
        onDismiss={() => setFootGuideDismissed(true)}
        visual={<FooterGuideVisual />}
        {...guideShell}
      />
    </aside>
  );
}

/* ── «Пул»: у каждого варианта своя пилюля и счётчик ──────────────────────── */

export function useLabPoolRows() {
  const setCount = useFxLabStore((s) => s.setCount);
  const variants = useLabVariants();
  const counts = useWizardStore((s) => s.allocation.variants ?? {});
  const label = useVariantLabel();
  return variants
    .filter((v) => !v.draft && hookComplete(v.kind, v.config))
    .map((v) => ({ id: v.id, color: v.color, kind: v.kind, label: label(v), count: counts[v.id] ?? 0, set: (n: number) => setCount(v.id, n) }));
}
