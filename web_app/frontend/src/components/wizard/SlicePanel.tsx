import React, { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePhone } from '../../lib/usePhone';
import { cn } from '../../lib/cn';
import { useChip } from '../../i18n/useChip';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { LimitsIndicator } from '../ui/LimitsIndicator';
import { BackSquareButton } from './WizardFrame';
import { HOOK_LABELS, HookKind, hookPills, selectedEffectStyles, useWizardStore, WizardStateData } from '../../stores/wizardStore';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';

/** Мини-визуал первой подсказки пула: счётчик роликов растёт. */
/**
 * Мини-визуал «Сколько видео сделать»: один пил-степпер (как в реальном UI), «+»
 * проседает под «нажатием», а цифра листается вверх новым значением — показываем
 * ДЕЙСТВИЕ (жми +, счётчик растёт), а не абстрактное сравнение двух чисел.
 */
/**
 * Мини-визуал «Сколько видео сделать»: полный цикл степпера — «+» дважды (4→5→6),
 * потом «−» дважды (6→5→4), луп. Три цифры кросс-фейдятся по расписанию (guide-pool-digit-4/5/6),
 * кнопки проседают ровно в момент своего «нажатия» (guide-pool-plus/-minus).
 * Цифры и +/− — белые (были в цвет текста/приглушённые, плохо читались);
 * весь текст опущен на 1px — тот же приём, что и в остальных гайдах.
 */
function PoolTotalGuideVisual() {
  return (
    <div className="guide-track-piece guide-mode-delay-1 flex w-full items-center justify-center" aria-hidden="true">
      <span className="flex h-[38px] items-center gap-[10px] rounded-r15 bg-accent-20 px-[14px] text-[15px] leading-none text-white shadow-[inset_0_0_0_1px_var(--accent-light)]">
        <span className="guide-pool-minus flex h-[22px] w-[22px] items-center justify-center rounded-[7px] bg-white/10 text-white">−</span>
        <span className="relative h-[18px] w-[14px] overflow-hidden">
          <strong className="guide-pool-digit-4 absolute inset-0 flex items-center justify-center font-[400] leading-none text-white" style={{ transform: 'translateY(1px)' }}>4</strong>
          <strong className="guide-pool-digit-5 absolute inset-0 flex items-center justify-center font-[400] leading-none text-white" style={{ transform: 'translateY(1px)' }}>5</strong>
          <strong className="guide-pool-digit-6 absolute inset-0 flex items-center justify-center font-[400] leading-none text-white" style={{ transform: 'translateY(1px)' }}>6</strong>
        </span>
        <span className="guide-pool-plus flex h-[22px] w-[22px] items-center justify-center rounded-[7px] bg-accent-light text-white">+</span>
      </span>
    </div>
  );
}

/** Мини-визуал второй подсказки пула: распределение по группам. */
function PoolDistributeGuideVisual() {
  const rows = ['Фон', 'Субтитры', 'Хук'];
  return (
    <div className="flex w-full flex-col gap-[6px]" aria-hidden="true">
      {rows.map((label, index) => (
        <span
          key={label}
          className={cn(
            'guide-mode-reveal flex h-[22px] items-center justify-between rounded-[7px] bg-white/[0.05] px-[8px] text-[9px] leading-none text-white/70',
            index === 0 ? 'guide-mode-delay-1' : index === 1 ? 'guide-mode-delay-2' : 'guide-mode-delay-3'
          )}
        >
          <span className="truncate leading-none" style={{ transform: 'translateY(1px)' }}>{label}</span>
          {/* Пульс идёт по очереди строка за строкой (задержка = index) — читается как
              «каждая группа получает свою долю», а не как случайное мигание. Цифра и
              +/− — белые (в цвет акцента читались плохо); 1px-сдвиг — на вложенном
              спане, отдельно от пульса: бегущая анимация transform иначе затёрла бы
              статичный translateY, заданный на том же элементе. */}
          <span className="guide-pulse flex items-center gap-[4px] rounded-[5px] bg-accent-20 px-[5px] py-[1px]" style={{ animationDelay: `${index * 0.7}s` }}>
            <span className="flex items-center gap-[4px] leading-none text-white" style={{ transform: 'translateY(1px)' }}>
              <span>−</span><b className="leading-none">2</b><span>+</span>
            </span>
          </span>
        </span>
      ))}
    </div>
  );
}

/*
 * Этап «Пул» (Figma W19 → W33): «Всего видео» закреплён сверху, секции скроллятся
 * под него с фейдом. Ручные значения не трогаем — показываем «нераспределено: ±N».
 */

/** Ключ юнита — стабильный (идёт в allocation), подпись собирается через i18n при рендере. */
export function backgroundUnits(bg: WizardStateData['background']): { key: string; labelKey: string; name: string; icon: 'tag' | 'photo'; noHook: boolean }[] {
  return [
    ...bg.sourceVideos.map((plan, index) => ({ key: `upload:${plan.id}`, labelKey: 'wizard.pool.ownVideoUnit', name: `${index + 1} · ${plan.format}`, icon: 'tag' as const, noHook: plan.format === '16:9' })),
    ...bg.footage.map((vibe) => ({
      key: `footage:${vibe}`,
      labelKey: 'wizard.pool.vibeUnit',
      name: vibe,
      icon: 'tag' as const,
      noHook: (bg.footageFormats?.[vibe] ?? (bg.footageType === 'cine16x9' ? '16:9' : '9:16')) === '16:9'
    })),
    ...bg.photo.map((vibe) => ({ key: `photo:${vibe}`, labelKey: 'wizard.pool.photoUnit', name: vibe, icon: 'photo' as const, noHook: true }))
  ];
}

export function compatibleHookTarget(
  bg: WizardStateData['background'],
  allocation: Record<string, number>
): number {
  const units = backgroundUnits(bg);
  return Object.entries(allocation).reduce((total, [key, count]) => {
    const unit = units.find((candidate) => candidate.key === key);
    return total + (unit && !unit.noHook ? count : 0);
  }, 0);
}

function distribute(keys: string[], total: number): Record<string, number> {
  const result: Record<string, number> = {};
  if (!keys.length) return result;
  const base = Math.floor(total / keys.length);
  let rest = total - base * keys.length;
  for (const key of keys) {
    result[key] = base + (rest > 0 ? 1 : 0);
    if (rest > 0) rest -= 1;
  }
  return result;
}

function Stepper({ value, onChange, min = 0 }: { value: number; onChange: (next: number) => void; min?: number }) {
  const { t } = useTranslation();
  return (
    <span className="count-stepper">
      <button type="button" aria-label={t('wizard.pool.less')} disabled={value <= min} onClick={() => onChange(value - 1)}>−</button>
      <strong>{value}</strong>
      <button type="button" aria-label={t('wizard.pool.more')} onClick={() => onChange(value + 1)}>+</button>
    </span>
  );
}

function SectionCard({ title, note, warn, children }: { title: string; note: string; warn?: boolean; children: ReactNode }) {
  return (
    <section className={cn('rounded-r15 bg-grad-soft-10 p-space-5', warn && 'shadow-[inset_0_0_0_1.5px_var(--warning)]')}>
      <div className="mb-space-4 flex items-baseline justify-between gap-space-3">
        <h3 className="text-[24px] font-[400] text-text max-xl:text-[20px]">{title}</h3>
        <span className={cn('text-[15px] max-md:text-right', warn ? 'text-[var(--warning)]' : 'text-text-60')}>{note}</span>
      </div>
      <div className="flex flex-col gap-space-3">{children}</div>
    </section>
  );
}

function MiniPill({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <span className="mini-pill">
      <span className="mini-pill-icon" aria-hidden="true">{icon}</span>
      {label}
    </span>
  );
}

/* Иконки пилов: белые; масштаб задаётся от высоты пила (25px-бокс → 13px, 44px-бокс → 20px) */
const WHITE = 'var(--text)';
const tagIcon = (size = 13) => <SvgMaskIcon src="/assets/figma/icon-tag.svg" style={{ width: size, height: size * 0.81, color: WHITE, transform: 'rotate(-22.23deg)' }} />;
const photoIcon = (size = 13) => <SvgMaskIcon src="/assets/figma/icon-photo.svg" style={{ width: size, height: size * 0.9, color: WHITE }} />;
const boltIcon = (size = 13) => <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: size * 0.65, height: size, color: WHITE }} />;
const strobeIcon = (size = 13) => <SvgMaskIcon src="/assets/figma/icon-strobe.svg" style={{ width: size, height: size, color: WHITE }} />;
/* «T» опущена на 1px — компенсация вертикальной метрики (правка ревью) */
const tIcon = (size = 13) => <em className="font-bold italic leading-none" style={{ color: WHITE, fontSize: size, marginTop: 1 }}>T</em>;

const HOOK_ICON_SRC: Record<HookKind, string> = {
  warmup: '/assets/figma/hook-sound.svg',
  object: '/assets/figma/hook-object.svg',
  effects: '/assets/figma/hook-effects.svg',
  motion: '/assets/figma/hook-motion.svg',
  thought: '/assets/figma/hook-thought.svg',
  none: '/assets/figma/icon-bolt.svg'
};

function hookKindIcon(kind: HookKind, size = 13) {
  return <SvgMaskIcon src={HOOK_ICON_SRC[kind]} style={{ width: size * 0.92, height: size, color: WHITE }} />;
}

export function StageSlice() {
  const { t } = useTranslation();
  const chip = useChip();
  const state = useWizardStore();
  const alloc = state.allocation;
  const setAllocation = state.setAllocation;

  const units = useMemo(() => backgroundUnits(state.background), [state.background]);
  const colorGroup = state.background.color
    ? { label: state.background.strobe ? 'Строб' : 'Цвет', strobe: state.background.strobe }
    : null;
  const fixedCount = colorGroup ? 1 : 0;
  const subtitleStyles = state.subtitles.pool;
  const hooksInPool = hookPills(state.hooks);
  const stylesInPool = selectedEffectStyles(state.hooks);

  useEffect(() => {
    const unitKeys = units.map((u) => u.key);
    const known = Object.keys(alloc.background);
    const sameKeys = unitKeys.length === known.length && unitKeys.every((key) => known.includes(key));
    if (alloc.seeded && sameKeys) {
      const hookKinds = hooksInPool.map((pill) => pill.kind);
      const allocatedHookKinds = Object.keys(alloc.hooks);
      const sameHookKinds = hookKinds.length === allocatedHookKinds.length
        && hookKinds.every((kind) => allocatedHookKinds.includes(kind));
      const hookTarget = compatibleHookTarget(state.background, alloc.background);
      const hookSum = Object.values(alloc.hooks).reduce((sum, count) => sum + count, 0);
      const allocatedStyles = Object.keys(alloc.styles ?? {});
      const sameStyles = stylesInPool.length === allocatedStyles.length
        && stylesInPool.every((style) => allocatedStyles.includes(style));
      const stylesSum = Object.values(alloc.styles ?? {}).reduce((sum, count) => sum + count, 0);
      const allocatedSubtitles = Object.keys(alloc.subtitles);
      const sameSubtitles = subtitleStyles.length === allocatedSubtitles.length
        && subtitleStyles.every((style) => allocatedSubtitles.includes(style));
      const subtitleSum = Object.values(alloc.subtitles).reduce((sum, count) => sum + count, 0);
      if (sameHookKinds && hookSum === hookTarget && sameStyles && (!stylesInPool.length || stylesSum === hookTarget)
        && sameSubtitles && (!subtitleStyles.length || subtitleSum === unitKeys.length)) return;
      setAllocation({
        hooks: sameHookKinds && hookSum === hookTarget ? alloc.hooks : distribute(hookKinds, hookTarget),
        styles: sameStyles && stylesSum === hookTarget ? alloc.styles : distribute(stylesInPool, hookTarget),
        subtitles: sameSubtitles && subtitleSum === unitKeys.length ? alloc.subtitles : distribute(subtitleStyles, unitKeys.length)
      });
      return;
    }
    setAllocation({
      seeded: true,
      total: unitKeys.length + fixedCount,
      background: distribute(unitKeys, unitKeys.length),
      subtitles: distribute(subtitleStyles, unitKeys.length),
      hooks: distribute(hooksInPool.map((p) => p.kind), units.filter((u) => !u.noHook).length),
      styles: distribute(stylesInPool, units.filter((u) => !u.noHook).length),
      strobeFont: alloc.strobeFont ?? subtitleStyles[0],
      colorFont: alloc.colorFont ?? subtitleStyles[0]
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [units, fixedCount, subtitleStyles.join(','), hooksInPool.map((p) => p.kind).join(','), stylesInPool.join(',')]);

  const bgSum = Object.values(alloc.background).reduce((a, b) => a + b, 0);
  const bgTarget = alloc.total - fixedCount;
  const bgRest = bgTarget - bgSum;

  const subsSum = Object.values(alloc.subtitles).reduce((a, b) => a + b, 0);
  const subsRest = bgTarget - subsSum;

  const hookTarget = compatibleHookTarget(state.background, alloc.background);
  const hooksSum = Object.values(alloc.hooks).reduce((a, b) => a + b, 0);
  const hooksRest = hookTarget - hooksSum;
  const stylesSum = Object.values(alloc.styles ?? {}).reduce((a, b) => a + b, 0);
  const stylesRest = stylesInPool.length ? hookTarget - stylesSum : 0;

  const setCount = (slice: 'background' | 'subtitles' | 'hooks' | 'styles', key: string, value: number) =>
    setAllocation({ [slice]: { ...alloc[slice], [key]: Math.max(0, value) } });

  const distributeEvenly = () => {
    const backgroundTarget = Math.max(0, alloc.total - fixedCount);
    const background = distribute(units.map((unit) => unit.key), backgroundTarget);
    const footageTarget = Object.entries(background).reduce((sum, [key, count]) => {
      const unit = units.find((candidate) => candidate.key === key);
      return sum + (unit && !unit.noHook ? count : 0);
    }, 0);
    setAllocation({
      background,
      subtitles: distribute(subtitleStyles, backgroundTarget),
      hooks: distribute(hooksInPool.map((pill) => pill.kind), footageTarget),
      styles: distribute(stylesInPool, footageTarget)
    });
  };

  const isPhone = usePhone();
  const restNote = (rest: number, base: string) => {
    const tail = rest === 0 ? '' : rest > 0 ? t('wizard.pool.unallocated', { n: rest }) : t('wizard.pool.over', { n: -rest });
    return [base, tail].filter(Boolean).join(' · ');
  };

  const totalGuideTargetRef = useRef<HTMLDivElement>(null);
  const distributeGuideTargetRef = useRef<HTMLDivElement>(null);
  const hasUnallocated = bgRest !== 0 || subsRest !== 0 || hooksRest !== 0 || stylesRest !== 0;
  // distribute объявлен первым: idle-условие total-гайда («мы ещё не ушли дальше»)
  // на его dismissed-значение ссылается. visible=false у distribute: точный
  // пререквизит «total уже закрыт» тут не собрать (totalGuideDismissed объявлен
  // НИЖЕ) — показ отмечаем отдельно через useMarkGuideSeen после showDistributeGuide.
  const [distributeGuideDismissed, setDistributeGuideDismissed] = useGuideDismiss('pool-distribute', hasUnallocated, false);
  const [totalGuideDismissed, setTotalGuideDismissed] = useGuideDismiss('pool-total', !distributeGuideDismissed, true);
  const showTotalGuide = !totalGuideDismissed;
  const showDistributeGuide = totalGuideDismissed && !distributeGuideDismissed;
  useMarkGuideSeen('pool-distribute', totalGuideDismissed);

  // Явный скролл к цели до собственного instant-scrollIntoView оверлея: без него
  // цель может остаться частично за пределами внешнего скролл-контейнера страницы,
  // и рамка-обводка (она аккуратно клипуется по видимым границам предков) кажется
  // «обрезанной» — хотя технически это верное поведение для частично видимой цели.
  useScrollGuideIntoView(showTotalGuide, totalGuideTargetRef);
  useScrollGuideIntoView(showDistributeGuide, distributeGuideTargetRef);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* «Всего видео» неподвижен; секции скроллятся под ним */}
      <div ref={totalGuideTargetRef} className="relative z-[5] shrink-0">
        <div className="relative flex h-[80px] items-center justify-between rounded-r15 border-2 border-accent-light bg-grad-soft-10 px-space-6 max-md:h-auto max-md:flex-wrap max-md:gap-x-[10px] max-md:gap-y-[8px] max-md:px-space-4 max-md:py-[10px]">
          <span className="wizard-h !text-[28px] max-xl:!text-[22px] max-md:!text-[18px]">{t('wizard.pool.total')}</span>
          {/* Figma W19: кружок-индикатор лимита в 20px справа от «+» (W46 — поповер по ховеру).
              Телефон: заголовок, степпер и кружок — одна строка; «Распределить» появляется
              отдельной строкой только когда счётчик ушёл от раскладки, после нажатия исчезает. */}
          <span className="relative flex items-center gap-[20px] max-md:contents">
            {(bgRest !== 0 || subsRest !== 0 || hooksRest !== 0 || stylesRest !== 0) && (
              <button type="button" onClick={distributeEvenly} className="flex h-[34px] items-center whitespace-nowrap rounded-r10 border border-accent bg-grad-soft-20 px-[14px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125 max-md:order-last max-md:h-[30px] max-md:basis-full max-md:justify-center max-md:text-[13px]">
                {t('wizard.pool.distributeEven')}
              </button>
            )}
            <span className="flex items-center gap-[20px] max-md:ml-auto max-md:gap-[12px]">
              <Stepper value={alloc.total} min={fixedCount + (units.length ? 1 : 0)} onChange={(total) => setAllocation({ total })} />
              <LimitsIndicator />
            </span>
          </span>
        </div>
      </div>

      <ActionGuideOverlay
        open={showTotalGuide}
        targetRef={totalGuideTargetRef}
        title={t('wizard.pool.guideTotalTitle')}
        text={t('wizard.pool.guideTotalText')}
        dismissLabel={t('wizard.pool.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: 2 })}
        onDismiss={() => setTotalGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<PoolTotalGuideVisual />}
      />

      {/* Скролл секций с постоянными фейдами сверху/снизу — как на списке типов хука */}
      <div ref={distributeGuideTargetRef} className="relative mt-space-5 min-h-0 flex-1">
        <div className="no-scrollbar flex h-full flex-col gap-space-5 overflow-y-auto py-[12px]" style={{ maskImage: 'linear-gradient(to bottom, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%)', WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 24px, #000 calc(100% - 24px), transparent 100%)' }}>
        <SectionCard title={t('wizard.pool.background')} note={restNote(bgRest, t('wizard.pool.bgNote', { count: bgTarget }))} warn={bgRest !== 0}>
          {units.map((unit) => (
            <div key={unit.key} className="flex items-center justify-between gap-space-3">
              <span className="flex min-w-0 items-center gap-space-3">
                <MiniPill icon={unit.icon === 'tag' ? tagIcon() : photoIcon()} label={t(unit.labelKey, { name: chip(unit.name) })} />
                {unit.noHook && <span className="shrink-0 whitespace-nowrap rounded-r9 border border-border px-space-2 py-[2px] text-[12px] leading-none text-text-60">{t('wizard.pool.noFx')}</span>}
              </span>
              <Stepper value={alloc.background[unit.key] ?? 0} onChange={(value) => setCount('background', unit.key, value)} />
            </div>
          ))}
          {colorGroup && (
            <div className="rounded-r10 bg-grad-soft-10 p-space-4">
              <MiniPill icon={strobeIcon()} label={t('wizard.pool.colorVideo', { label: chip(colorGroup.label) })} />
              <div className="mt-space-3 flex flex-wrap items-center gap-space-3 pl-[25px] max-md:pl-0">
                <span className="translate-y-px text-[15px] text-text-60">{t('wizard.pool.chooseFont')}</span>
                <span className="flex flex-wrap gap-space-2">
                  {subtitleStyles.map((style) => {
                    const field = colorGroup.strobe ? 'strobeFont' : 'colorFont';
                    const current = colorGroup.strobe ? alloc.strobeFont : alloc.colorFont;
                    return (
                      <button
                        key={style}
                        type="button"
                        className={cn('translate-y-[2px] rounded-r9 px-space-3 py-[3px] text-[13px] transition', current === style ? 'bg-accent-20 text-text shadow-[inset_0_0_0_1px_var(--accent-light)]' : 'text-text-60 hover:text-text')}
                        onClick={() => setAllocation({ [field]: style })}
                      >
                        {style.toLowerCase()}
                      </button>
                    );
                  })}
                  {subtitleStyles.length === 0 && <span className="text-[13px] text-text-40">{t('wizard.pool.noStyles')}</span>}
                </span>
              </div>
            </div>
          )}
          {units.length === 0 && !colorGroup && <p className="text-[15px] text-text-60">{t('wizard.pool.bgEmpty')}</p>}
        </SectionCard>

        {subtitleStyles.length > 0 && (
          <SectionCard title={t('wizard.pool.subtitles')} note={restNote(subsRest, t('wizard.pool.subsNote', { count: bgTarget }))} warn={subsRest !== 0}>
            {subtitleStyles.map((style) => (
              <div key={style} className="flex items-center justify-between gap-space-3">
                <MiniPill icon={tIcon()} label={style} />
                <Stepper value={alloc.subtitles[style] ?? 0} onChange={(value) => setCount('subtitles', style, value)} />
              </div>
            ))}
          </SectionCard>
        )}

        {hooksInPool.length > 0 && (
          <SectionCard title={t('wizard.pool.fx')} note={restNote(hooksRest, t('wizard.pool.fxNote', { count: hookTarget }))} warn={hooksRest !== 0}>
            {hooksInPool.map((pill) => (
              <div key={pill.kind} className="flex items-center justify-between gap-space-3">
                {/* Иконка конкретного типа хука вместо молнии — легче ориентироваться (правка ревью) */}
                <MiniPill icon={hookKindIcon(pill.kind)} label={chip(pill.label)} />
                <Stepper value={alloc.hooks[pill.kind] ?? 0} onChange={(value) => setCount('hooks', pill.kind, value)} />
              </div>
            ))}
          </SectionCard>
        )}

        {stylesInPool.length > 0 && (
          <SectionCard title={t('wizard.pool.styles')} note={restNote(stylesRest, isPhone ? '' : t('wizard.pool.stylesNote', { count: hookTarget }))} warn={stylesRest !== 0}>
            {stylesInPool.map((style) => (
              <div key={style} className="flex items-center justify-between gap-space-3">
                <MiniPill icon={boltIcon()} label={chip(style)} />
                <Stepper value={alloc.styles?.[style] ?? 0} onChange={(value) => setCount('styles', style, value)} />
              </div>
            ))}
          </SectionCard>
        )}
        </div>
      </div>

      <ActionGuideOverlay
        open={showDistributeGuide}
        targetRef={distributeGuideTargetRef}
        title={t('wizard.pool.guideDistributeTitle')}
        text={t('wizard.pool.guideDistributeText')}
        dismissLabel={t('wizard.pool.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setDistributeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<PoolDistributeGuideVisual />}
      />
    </div>
  );
}

function combinationAt(
  index: number,
  bg: [string, number][],
  subs: [string, number][],
  hooks: [string, number][],
  styles: [string, number][],
  units: ReturnType<typeof backgroundUnits>,
  hasColor: boolean,
  colorStyle?: string
): { bg?: string; sub?: string; hook?: string; style?: string } {
  const expand = (pairs: [string, number][]) => pairs.flatMap(([key, count]) => Array.from({ length: count }, () => key));
  const bgList = expand(bg);
  if (hasColor) bgList.push('__color__');
  const subList = expand(subs);
  const hookList = expand(hooks);
  const styleList = expand(styles);
  const bgKey = bgList[index];
  const unit = units.find((candidate) => candidate.key === bgKey);
  const hookAllowed = Boolean(unit && !unit.noHook);
  const nonColorIndex = bgList.slice(0, index).filter((key) => key !== '__color__').length;
  const hookIndex = bgList.slice(0, index).filter((key) => {
    const previous = units.find((candidate) => candidate.key === key);
    return previous && !previous.noHook;
  }).length;
  return {
    bg: bgKey,
    sub: bgKey === '__color__' ? colorStyle : subList[nonColorIndex],
    hook: hookAllowed ? hookList[hookIndex] : undefined,
    style: hookAllowed ? styleList[hookIndex] : undefined
  };
}

export function SliceWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const chip = useChip();
  const state = useWizardStore();
  const alloc = state.allocation;
  const units = useMemo(() => backgroundUnits(state.background), [state.background]);
  const [index, setIndex] = useState(0);

  const total = Math.max(1, alloc.total);
  const safeIndex = Math.min(index, total - 1);
  const colorStyle = state.background.color
    ? (state.background.strobe ? alloc.strobeFont : alloc.colorFont) ?? state.subtitles.pool[0]
    : undefined;
  const combo = combinationAt(
    safeIndex,
    Object.entries(alloc.background),
    Object.entries(alloc.subtitles),
    Object.entries(alloc.hooks),
    Object.entries(alloc.styles ?? {}),
    units,
    Boolean(state.background.color),
    colorStyle
  );
  // имя бакета хранится по-русски (по нему матчит бэк) — показываем через словарь
  const bgLabel = combo.bg === '__color__'
    ? chip(state.background.strobe ? 'Строб' : 'Цвет')
    : combo.bg ? (units.find(unit => unit.key === combo.bg)?.name ?? chip(combo.bg.split(':')[1])) : undefined;
  const hookLabel = combo.hook ? chip(HOOK_LABELS[combo.hook as HookKind]) : undefined;
  const hookConfig = combo.hook ? state.hooks.configs[combo.hook as HookKind] : undefined;
  const transitionLabel = hookConfig?.effectGlue ? chip(hookConfig.effectGlue) : t('wizard.pool.notSelected');
  const styleLabel = combo.style ? chip(combo.style) : t('wizard.pool.noStyleSelected');

  // Стрелки клавиатуры листают комбинации, пока фокус внутри панели
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (total < 2) return;
    if (event.key === 'ArrowLeft') setIndex((safeIndex - 1 + total) % total);
    if (event.key === 'ArrowRight') setIndex((safeIndex + 1) % total);
  };

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full" onKeyDown={onKeyDown}>
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        {/* Полная конфигурация одной вариации; листать можно кнопками или клавиатурой. */}
        <div className="mb-space-5 flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.pool.combinations')}</h2>
          <div className="flex h-[30px] shrink-0 items-center gap-[10px] rounded-[15px] px-[12px]" style={{ background: 'var(--grad-whitey)' }}>
            <button
              type="button"
              aria-label={t('wizard.pool.prevCombo')}
              onClick={() => setIndex((safeIndex - 1 + total) % total)}
              disabled={total < 2}
              className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30"
            >
              <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)', transform: 'rotate(180deg)' }} />
            </button>
            <span className="text-[16px] font-[350] leading-none text-accent">{safeIndex + 1}/{total}</span>
            <button
              type="button"
              aria-label={t('wizard.pool.nextCombo')}
              onClick={() => setIndex((safeIndex + 1) % total)}
              disabled={total < 2}
              className="flex items-center transition-opacity hover:opacity-60 disabled:opacity-30"
            >
              <SvgMaskIcon src="/assets/figma/home-arrow.svg" style={{ width: 7, height: 11, color: 'var(--accent)' }} />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden rounded-r15 border border-[rgba(139,111,230,.28)] bg-grad-soft-10">
          <div className="grid h-[52px] grid-cols-[132px_minmax(0,1fr)] max-md:grid-cols-[96px_minmax(0,1fr)] items-center gap-space-4 border-b border-[rgba(246,245,253,.09)] bg-[rgba(139,111,230,.08)] px-space-5 text-[13px] uppercase tracking-[.08em] text-text-40 max-md:h-[36px] max-md:px-[14px] max-md:text-[11px]">
            <span>{t('wizard.pool.parameter')}</span>
            <span>{t('wizard.pool.selectedValue')}</span>
          </div>
          <div className="flex h-[calc(100%_-_52px)] flex-col">
            {[
              [t('wizard.pool.background'), bgLabel ?? t('wizard.pool.notSelected'), combo.bg === '__color__' ? strobeIcon(18) : combo.bg?.startsWith('photo') ? photoIcon(18) : tagIcon(18)],
              [t('wizard.pool.subtitles'), combo.sub ?? t('wizard.pool.notSelected'), tIcon(18)],
              [t('wizard.pool.hook'), hookLabel ?? t('wizard.pool.noHookSelected'), boltIcon(18)],
              [t('wizard.pool.transition'), transitionLabel, <img key="transition" src="/assets/figma/combo-transition.svg" width="22" height="22" alt="" />],
              [t('wizard.pool.style'), styleLabel, <img key="style" src="/assets/figma/combo-style.svg" width="22" height="22" alt="" />]
            ].map(([label, value, icon]) => (
              <div key={String(label)} className="grid min-h-0 flex-1 grid-cols-[132px_minmax(0,1fr)] max-md:grid-cols-[96px_minmax(0,1fr)] items-center gap-space-4 border-b border-[rgba(246,245,253,.07)] px-space-5 last:border-0 max-md:min-h-[40px] max-md:px-[14px]">
                <span className="text-[15px] text-text-40">{label}</span>
                <span className="flex min-w-0 items-center gap-space-3 text-[19px] text-text-80 max-md:gap-[8px] max-md:text-[14px]">
                  <span className="flex h-[36px] w-[36px] shrink-0 items-center justify-center rounded-r10 bg-accent-20 max-md:h-[26px] max-md:w-[26px] max-md:rounded-[7px] max-md:[&>*]:scale-[.7]" aria-hidden="true">{icon}</span>
                  <span className="truncate">{value}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card-2 flex h-[140px] shrink-0 items-center gap-[20px] px-space-6 py-space-6 max-lg:px-space-5">
        <BackSquareButton onClick={onBack} />
        <button type="button" disabled={!canContinue || loading} onClick={onNext} className={cn('soft-btn h-[60px] flex-1 gap-space-3', ready && 'soft-btn-ready')}>
          {loading ? <span className="spinner" /> : (<>
            <span aria-hidden="true">✦</span>
            {t('wizard.pool.generate')}
          </>)}
        </button>
      </div>
    </aside>
  );
}
