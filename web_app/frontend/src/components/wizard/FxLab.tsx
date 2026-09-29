import { useEffect, useState } from 'react';
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
import { HOOK_LABELS, HookConfig, HookKind, hookComplete, useWizardStore } from '../../stores/wizardStore';
import {
  ChipRow, HOOK_TYPES, HookStep, SlowShutterExtendToggle, StyleScopeToggle,
  configuredPreviewId, hookSteps, selectedStyles, toggleStyle
} from './HookPanel';

/*
 * ПРОТОТИП (?fxLab=1): варианты хука на шаге FX без переделки логики настройки.
 *
 * Вариант = тип хука + его конфиг (хук, склейка, стиль) — ровно то, что сегодня
 * лежит в hooks.configs[kind], только таких конфигов у одного типа может быть
 * несколько. Состояние прототипа локальное: в стор визарда и в рендер не пишет.
 *
 * Что меняется в интерфейсе:
 *  - левый список типов — аккордеон: раскрытый тип показывает свои варианты;
 *  - настройка (хук · склейка · стиль) переехала в сам пример — док поверх видео,
 *    отдельной карточки под превью больше нет, видео получает всю высоту;
 *  - футер: пилюли вариантов с коротким описанием, «+» = копия текущего.
 */

export const useFxLab = () => typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('fxLab');

export interface LabVariant { id: string; kind: HookKind; config: HookConfig; color: string }
const PALETTE = ['#8b6fe6', '#e38fb5', '#6fc7c0', '#e8b45f', '#9fb5ff', '#b7e27a', '#ff9a7a', '#d6a1ff'];
const STEP_NAME: Record<string, string> = {
  sound: 'Прогрев', object: 'Объект', effectHook: 'Эффект', motion: 'Движение', thought: 'Мысль',
  effectGlue: 'Склейка', effectStyle: 'Стиль'
};

interface LabState {
  variants: LabVariant[];
  activeId: string | null;
  expanded: HookKind | null;
  tab: number;
  seeded: boolean;
  seed: (configs: Partial<Record<HookKind, HookConfig>>, kind?: HookKind) => void;
  select: (id: string) => void;
  toggleType: (kind: HookKind) => void;
  add: (kind: HookKind, config?: HookConfig) => void;
  copyActive: () => void;
  remove: (id: string) => void;
  patch: (patch: Partial<HookConfig>) => void;
  setTab: (tab: number) => void;
}

let seq = 0;
const newId = () => `v${++seq}`;

export const useFxLabStore = create<LabState>((set, get) => ({
  variants: [],
  activeId: null,
  expanded: null,
  tab: 0,
  seeded: false,
  seed: (configs, kind) => {
    if (get().seeded) return;
    const variants: LabVariant[] = [];
    (Object.keys(configs) as HookKind[]).forEach((k) => {
      const config = configs[k];
      if (config && Object.keys(config).length) variants.push({ id: newId(), kind: k, config: { ...config }, color: PALETTE[variants.length % PALETTE.length] });
    });
    // Для наглядности — второй вариант того же типа, что и первый (как если бы его создали «копией»).
    const src = variants.find((v) => v.kind === kind) ?? variants[0];
    if (src) {
      variants.splice(variants.indexOf(src) + 1, 0, { id: newId(), kind: src.kind, config: { ...src.config, effectGlue: 'Минимакс', effectStyles: ['Ч/Б'], effectStyle: 'Ч/Б' }, color: PALETTE[variants.length % PALETTE.length] });
    }
    const active = variants.find((v) => v.kind === kind) ?? variants[0];
    set({ variants, activeId: active?.id ?? null, expanded: active?.kind ?? null, seeded: true, tab: 0 });
  },
  select: (id) => set((s) => ({ activeId: id, expanded: s.variants.find((v) => v.id === id)?.kind ?? s.expanded, tab: firstOpenTab(s.variants.find((v) => v.id === id)) })),
  toggleType: (kind) => {
    const s = get();
    if (s.expanded === kind) { set({ expanded: null }); return; }
    const first = s.variants.find((v) => v.kind === kind);
    if (first) set({ expanded: kind, activeId: first.id, tab: firstOpenTab(first) });
    else get().add(kind);
  },
  add: (kind, config = {}) => set((s) => {
    const v = { id: newId(), kind, config, color: PALETTE[s.variants.length % PALETTE.length] };
    return { variants: [...s.variants, v], activeId: v.id, expanded: kind, tab: firstOpenTab(v) };
  }),
  copyActive: () => {
    const s = get();
    const src = s.variants.find((v) => v.id === s.activeId);
    if (!src) return;
    // Копия: тот же хук и склейка, стиль — выбрать заново. Чаще всего и нужен «тот же, но в другом стиле».
    const config: HookConfig = { ...src.config, effectStyles: [], effectStyle: undefined };
    const v = { id: newId(), kind: src.kind, config, color: PALETTE[s.variants.length % PALETTE.length] };
    set({ variants: [...s.variants, v], activeId: v.id, expanded: v.kind, tab: hookSteps(v.kind).length - 1 });
  },
  remove: (id) => set((s) => {
    const variants = s.variants.filter((v) => v.id !== id);
    const activeId = s.activeId === id ? (variants.find((v) => v.kind === s.expanded) ?? variants[0])?.id ?? null : s.activeId;
    return { variants, activeId };
  }),
  patch: (patch) => set((s) => ({ variants: s.variants.map((v) => (v.id === s.activeId ? { ...v, config: { ...v.config, ...patch } } : v)) })),
  setTab: (tab) => set({ tab })
}));

function stepFilled(step: HookStep, config: HookConfig): boolean {
  return step.key === 'effectStyle' ? selectedStyles(config).length > 0 : Boolean(config[step.key]);
}
function firstOpenTab(v?: LabVariant): number {
  if (!v) return 0;
  const i = hookSteps(v.kind).findIndex((step) => !stepFilled(step, v.config));
  return i < 0 ? 0 : i;
}

/** Короткое описание варианта: хук · склейка · стиль (+N). */
export function useVariantLabel() {
  const chip = useChip();
  return (v: LabVariant) => {
    const c = v.config;
    const hook = v.kind === 'none' ? chip(HOOK_LABELS.none) : (c.sound ?? c.object ?? c.effectHook ?? c.motion ?? c.thought);
    const styles = selectedStyles(c);
    const style = styles.length ? `${chip(styles[0])}${styles.length > 1 ? ` +${styles.length - 1}` : ''}` : null;
    const parts = [hook ? chip(hook) : null, c.effectGlue ? chip(c.effectGlue) : null, style].filter(Boolean);
    return parts.length ? parts.join(' · ') : `${chip(HOOK_LABELS[v.kind])} — новый`;
  };
}

const iconOf = (kind: HookKind) => HOOK_TYPES.find((item) => item.kind === kind)!;

/* ── левая панель: типы-аккордеон с вариантами ─────────────────────────────── */

export function LabTypeList({ locked }: { locked: boolean }) {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((s) => s.hooks);
  const lab = useFxLabStore();
  const label = useVariantLabel();
  useEffect(() => { lab.seed(hooks.configs, hooks.kind); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="no-scrollbar flex h-full flex-col gap-[14px] overflow-y-auto py-[16px]" style={{ maskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)', WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%)' }}>
      {HOOK_TYPES.map((item) => {
        const variants = lab.variants.filter((v) => v.kind === item.kind);
        const open = lab.expanded === item.kind;
        const has = variants.length > 0;
        const isLocked = locked && item.kind !== 'none';
        return (
          <div key={item.kind} className={cn('shrink-0 rounded-r15 bg-grad-soft-10 transition', (open || has) && 'border-2 border-accent-light', open && 'bg-grad-soft-20', isLocked && 'opacity-45')}>
            <button
              type="button"
              disabled={isLocked}
              aria-expanded={open}
              onClick={() => lab.toggleType(item.kind)}
              className="relative flex h-[76px] w-full items-center px-[26px] text-left disabled:cursor-not-allowed"
            >
              <SvgMaskIcon src={item.icon} style={{ width: item.iconW, height: item.iconH, color: has ? 'var(--accent-light)' : 'var(--text-80)' }} />
              <span className="wizard-body ml-space-4 !text-text">{chip(HOOK_LABELS[item.kind])}</span>
              {has && !open && (
                <span className="ml-[14px] flex items-center gap-[5px]" aria-label={`${variants.length} вар.`}>
                  {variants.slice(0, 4).map((v) => <i key={v.id} className="h-[9px] w-[9px] rounded-full" style={{ background: v.color }} />)}
                  {variants.length > 1 && <span className="translate-y-px text-[14px] text-text-60">{variants.length}</span>}
                </span>
              )}
              <svg className={cn('ml-auto mr-[52px] h-[16px] w-[16px] text-text-60 transition-transform', open && 'rotate-180')} viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span className="absolute right-[24px] flex h-[36px] w-[36px] items-center justify-center" aria-label={t('wizard.fx.whatIs', { label: chip(HOOK_LABELS[item.kind]) })}>
                <img src="/assets/figma/hint-circle.svg" width="36" height="36" alt="" aria-hidden="true" className="absolute inset-0" />
                <span className="relative text-[18px] text-text-80">?</span>
              </span>
            </button>
            {open && (
              <div className="flex flex-col gap-[8px] px-[18px] pb-[18px]">
                {variants.map((v) => {
                  const active = v.id === lab.activeId;
                  const done = hookComplete(v.kind, v.config);
                  return (
                    <div key={v.id} className={cn('group flex h-[48px] items-center gap-[12px] rounded-r10 pl-[14px] pr-[6px] transition', active ? 'bg-accent-20 shadow-[inset_0_0_0_1.5px_var(--accent-light)]' : 'bg-[rgba(5,1,15,.32)] hover:bg-[rgba(5,1,15,.5)]')}>
                      <button type="button" className="flex h-full min-w-0 flex-1 items-center gap-[12px] text-left" onClick={() => lab.select(v.id)}>
                        <i className="h-[10px] w-[10px] shrink-0 rounded-full" style={{ background: v.color }} />
                        <span className={cn('min-w-0 flex-1 translate-y-px truncate text-[16px]', active ? 'text-text' : 'text-text-80')}>{label(v)}</span>
                        {done
                          ? <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-label="Настроен" className="shrink-0 text-accent-light"><path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                          : <span className="shrink-0 translate-y-px text-[13px] text-[var(--warning)]">не настроен</span>}
                      </button>
                      <button type="button" aria-label="Удалить вариант" onClick={() => lab.remove(v.id)} className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[8px] text-text-40 opacity-0 transition hover:bg-white/10 hover:text-text group-hover:opacity-100">
                        <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true"><path d="M1 1L11 11M11 1L1 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                      </button>
                    </div>
                  );
                })}
                <button type="button" onClick={() => (variants.length ? lab.copyActive() : lab.add(item.kind))} className="flex h-[44px] items-center gap-[10px] rounded-r10 border border-dashed border-[rgba(139,111,230,.55)] px-[14px] text-left text-[15px] text-accent-light transition hover:bg-accent-20 hover:text-text">
                  <span className="text-[18px] leading-none">+</span>
                  <span className="translate-y-px">{variants.length ? 'Ещё вариант — копия текущего, со своим стилем' : 'Настроить вариант'}</span>
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── рабочая зона: пример во всю высоту, настройка — доком внутри него ─────── */

function LabPreview({ previewId }: { previewId?: string }) {
  const query = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews });
  if (!previewId || query.isLoading) return null;
  const effect = query.data?.previews.find((item) => item.id === previewId);
  // cover: пример заполняет рабочую зону целиком, а не висит полосой посередине
  return <div className="absolute inset-0 [&_video]:!object-cover [&_img]:!object-cover"><CatalogMedia url={effect?.previewUrl} className="absolute inset-0 h-full w-full" /></div>;
}

export function LabWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const lab = useFxLabStore();
  const label = useVariantLabel();
  const pillsScroll = useDragScroll();
  const [note, setNote] = useState<string | null>(null);
  const v = lab.variants.find((x) => x.id === lab.activeId);
  const steps = v ? hookSteps(v.kind) : [];
  const tab = Math.min(lab.tab, Math.max(0, steps.length - 1));
  const step = steps[tab];
  const config = v?.config ?? {};
  const styleValues = selectedStyles(config);
  const previewId = v ? configuredPreviewId(v.kind, config, step) : undefined;

  const pick = (option?: string) => {
    if (!v || !step) return;
    if (step.key === 'effectStyle') { lab.patch(toggleStyle(config, option)); return; }
    lab.patch({ [step.key]: option } as Partial<HookConfig>);
    // одиночный выбор — сразу к следующему незаполненному шагу; стили мультивыбор — остаёмся
    if (option) {
      const next = { ...config, [step.key]: option } as HookConfig;
      const i = steps.findIndex((s, k) => k > tab && !stepFilled(s, next));
      if (i >= 0) lab.setTab(i);
    }
  };

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col gap-space-5 px-space-6 py-space-6 max-lg:px-space-5">
        <div className="flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          <button type="button" onClick={() => { setNote('Таймлайн откроет этот же вариант — вкладки = пилюли футера'); window.setTimeout(() => setNote(null), 2400); }}
            className="flex h-[37px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent-light bg-grad-soft-20 px-[12px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125">
            <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true"><path d="M2 5h16M2 10h16M2 15h16M6 3v4m5 1v4m4 1v4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>
            <span className="translate-y-px">{t('wizard.fx.timeline')}</span>
          </button>
        </div>

        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10">
          {v && <LabPreview previewId={previewId} />}
          {!v && (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[260px] text-center">{t('wizard.fx.empty')}</p>
            </div>
          )}
          {v && (
            <>
              {/* какой вариант сейчас настраивается */}
              <div className="absolute left-[14px] top-[14px] z-[4] flex max-w-[calc(100%-28px)] items-center gap-[8px] rounded-[10px] bg-[rgba(5,1,15,.58)] px-[12px] py-[7px] backdrop-blur-[10px]">
                <i className="h-[9px] w-[9px] shrink-0 rounded-full" style={{ background: v.color }} />
                <span className="translate-y-px truncate text-[14px] text-text">{label(v)}</span>
              </div>
              {/* настройка — доком поверх примера */}
              <div className="absolute inset-x-[12px] bottom-[12px] z-[4] flex flex-col gap-[12px] rounded-r15 bg-[rgba(5,1,15,.62)] p-[12px] backdrop-blur-[12px]">
                <div className="flex items-center gap-[6px]">
                  <div className="flex min-w-0 flex-1 gap-[6px]" role="tablist" aria-label="Настройка варианта">
                    {steps.map((s, i) => {
                      const filled = stepFilled(s, config);
                      const value = s.key === 'effectStyle' ? (styleValues.length ? `${chip(styleValues[0])}${styleValues.length > 1 ? ` +${styleValues.length - 1}` : ''}` : null) : (config[s.key] as string | undefined);
                      return (
                        <button key={s.key} type="button" role="tab" aria-selected={i === tab} onClick={() => lab.setTab(i)}
                          className={cn('flex min-w-0 flex-1 flex-col items-start gap-[2px] rounded-r10 px-[12px] py-[7px] text-left transition', i === tab ? 'bg-accent-20 shadow-[inset_0_0_0_1.5px_var(--accent-light)]' : 'bg-white/[0.06] hover:bg-white/[0.1]')}>
                          <span className="translate-y-px text-[11px] uppercase tracking-[.06em] text-text-40">{STEP_NAME[s.key]}</span>
                          <span className={cn('w-full translate-y-px truncate text-[15px]', filled ? 'text-text' : 'text-text-40')}>{value ? chip(value) : 'выбрать'}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
                {(step?.key === 'effectStyle' && v.kind !== 'none') || (step?.key === 'effectHook' && config.effectHook === 'Слоу-шаттер') ? (
                  <div className="flex justify-end">
                    {step.key === 'effectStyle' && <StyleScopeToggle config={config} onPick={(full) => lab.patch({ effectStyleFull: full })} />}
                    {step.key === 'effectHook' && <SlowShutterExtendToggle config={config} onPick={(value) => lab.patch({ effectHookExtend: value })} />}
                  </div>
                ) : null}
                {step && (step.options.length === 0
                  ? <p className="px-[4px] text-[14px] text-text-60">Загрузка своего звука или видео — как сейчас (в прототипе не подключено)</p>
                  : <ChipRow options={step.options} value={config[step.key] as string | undefined} values={step.key === 'effectStyle' ? styleValues : undefined} onPick={pick} />)}
              </div>
            </>
          )}
          {note && <div className="absolute left-1/2 top-[60px] z-[5] -translate-x-1/2 rounded-[10px] bg-[#2b2145] px-[14px] py-[8px] text-[14px] text-text shadow-[0_8px_28px_rgba(0,0,0,.45)]"><span className="translate-y-px">{note}</span></div>}
        </div>
      </div>

      <PillsFooter
        pills={lab.variants.map((x) => ({
          key: x.id,
          label: label(x),
          icon: <span className="flex items-center gap-[7px]"><i className="h-[9px] w-[9px] rounded-full" style={{ background: x.color }} /><SvgMaskIcon src={iconOf(x.kind).icon} style={{ width: 13, height: 15, color: 'var(--accent-light)' }} /></span>
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
    </aside>
  );
}
