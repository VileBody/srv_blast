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
import { footageTypePlane } from '../../data/footageTypes';
import { PoolStoryboard, StoryboardSlot } from './PoolStoryboard';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';
import { useFxLab, useLabPoolRows, variantHookLabel } from './FxLab';
import { selectedStyles } from './HookPanel';

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

/**
 * Шаг 2 тура «Пула» в режиме вариантов: ролик = фон + субтитры + вариант FX. Слева —
 * слоты с долями, справа — ролик, который перебирает сочетания (как «Комбинации» на экране);
 * подсвечены ровно те фон и вариант, что сейчас в ролике.
 */
function PoolCombosGuideVisual() {
  // 3 ролика: доли в каждом слоте в сумме дают 3
  const rows: { label: string; items: { text: string; n: number; dot?: string; phase?: 'a' | 'b' }[] }[] = [
    { label: 'Фон', items: [{ text: 'Ночной город', n: 2, phase: 'a' }, { text: 'Неон', n: 1, phase: 'b' }] },
    { label: 'Текст', items: [{ text: 'Jakson', n: 3 }] },
    { label: 'FX', items: [{ text: 'Молния', n: 2, dot: '#8b6fe6', phase: 'a' }, { text: 'Свайп', n: 1, dot: '#e38fb5', phase: 'b' }] }
  ];
  const on = 'bg-accent-20 text-white shadow-[inset_0_0_0_1px_var(--accent-light)]';
  return (
    <div className="relative mx-auto flex h-[84px] w-[284px] max-w-full items-center gap-[12px]" aria-hidden="true">
      <span className="flex min-w-0 flex-1 flex-col gap-[5px]">
        {rows.map((row, r) => (
          <span key={row.label} className={cn('guide-mode-reveal flex h-[22px] items-center gap-[5px]', `guide-mode-delay-${r + 1}`)}>
            <span className="w-[30px] shrink-0 text-[9px] leading-none text-white/45"><span className="inline-block translate-y-px">{row.label}</span></span>
            {row.items.map((item) => (
              <span key={item.text} className="relative flex h-full min-w-0 items-center gap-[4px] overflow-hidden rounded-[6px] bg-white/[0.06] px-[6px] text-[9px] leading-none text-white/70">
                {item.phase && <i className={cn('absolute inset-0 rounded-[6px]', on, item.phase === 'a' ? 'guide-sb-a' : 'guide-sb-b')} />}
                {!item.phase && <i className={cn('absolute inset-0 rounded-[6px]', on)} />}
                {item.dot && <i className="relative h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: item.dot }} />}
                <span className="relative translate-y-px truncate text-white">{item.text}</span>
                <b className="relative ml-[2px] font-[400] text-white/55"><span className="inline-block translate-y-px">{item.n}</span></b>
              </span>
            ))}
          </span>
        ))}
      </span>
      <span className="guide-mode-reveal guide-mode-delay-4 relative h-[84px] w-[47px] shrink-0 overflow-hidden rounded-[8px] bg-black ring-1 ring-white/10">
        <i className="guide-sb-a absolute inset-0" style={{ background: 'linear-gradient(160deg, #3b2f6e, #120b24 70%)' }} />
        <i className="guide-sb-b absolute inset-0" style={{ background: 'linear-gradient(200deg, #2f5a6e, #0b1a24 70%)' }} />
        <span className="absolute inset-x-[4px] bottom-[4px] flex flex-col gap-[2px]">
          <i className="h-[3px] w-[70%] rounded-full bg-white/45" />
          <i className="h-[3px] w-[45%] rounded-full bg-white/30" />
          <span className="relative h-[5px] w-[5px]">
            <i className="guide-sb-a absolute inset-0 rounded-full" style={{ background: '#8b6fe6' }} />
            <i className="guide-sb-b absolute inset-0 rounded-full" style={{ background: '#e38fb5' }} />
          </span>
        </span>
      </span>
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

/**
 * Id подсказок «Пула». В режиме вариантов FX тур свой (другие тексты и визуал шага 2) — и
 * id свои: у старых «видел» записан у всех, кто проходил прежний тур.
 */
export function poolGuideId(id: 'total' | 'distribute' | 'storyboard' | 'replace', variants: boolean): string {
  return `${variants ? 'pool2' : 'pool'}-${id}`;
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

function MiniPill({ icon, label, trail }: { icon: ReactNode; label: string; trail?: ReactNode }) {
  return (
    <span className="mini-pill">
      <span className="mini-pill-icon" aria-hidden="true">{icon}</span>
      {label}
      {trail}
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
  // Режим вариантов FX (?fxLab=1): строки секции FX — варианты (стиль внутри варианта),
  // доли — allocation.variants. Классические хуки и отдельная секция стилей в нём не участвуют.
  const fxLab = useFxLab();
  const labRows = useLabPoolRows();
  const hooksInPool = fxLab ? [] : hookPills(state.hooks);
  const stylesInPool = fxLab ? [] : selectedEffectStyles(state.hooks);
  const fxKeys = fxLab ? labRows.map((row) => row.id) : hooksInPool.map((pill) => pill.kind);
  const fxAlloc: Record<string, number> = fxLab ? (alloc.variants ?? {}) : alloc.hooks;
  const fxSlice = fxLab ? 'variants' as const : 'hooks' as const;
  // Вариант, который начали, но не довели (в пул он не попадает, а генерацию держит).
  const labIncomplete = fxLab && state.fxVariants.some((v) => !v.draft && !labRows.some((row) => row.id === v.id));

  useEffect(() => {
    const unitKeys = units.map((u) => u.key);
    const known = Object.keys(alloc.background);
    const sameKeys = unitKeys.length === known.length && unitKeys.every((key) => known.includes(key));
    if (alloc.seeded && sameKeys) {
      const hookKinds = fxKeys;
      const allocatedHookKinds = Object.keys(fxAlloc);
      const sameHookKinds = hookKinds.length === allocatedHookKinds.length
        && hookKinds.every((kind) => allocatedHookKinds.includes(kind));
      const hookTarget = compatibleHookTarget(state.background, alloc.background);
      const hookSum = Object.values(fxAlloc).reduce((sum, count) => sum + count, 0);
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
        [fxSlice]: sameHookKinds && hookSum === hookTarget ? fxAlloc : distribute(hookKinds, hookTarget),
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
      [fxSlice]: distribute(fxKeys, units.filter((u) => !u.noHook).length),
      styles: distribute(stylesInPool, units.filter((u) => !u.noHook).length),
      strobeFont: alloc.strobeFont ?? subtitleStyles[0],
      colorFont: alloc.colorFont ?? subtitleStyles[0]
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [units, fixedCount, subtitleStyles.join(','), fxKeys.join(','), stylesInPool.join(',')]);

  const bgSum = Object.values(alloc.background).reduce((a, b) => a + b, 0);
  const bgTarget = alloc.total - fixedCount;
  const bgRest = bgTarget - bgSum;

  const subsSum = Object.values(alloc.subtitles).reduce((a, b) => a + b, 0);
  const subsRest = bgTarget - subsSum;

  const hookTarget = compatibleHookTarget(state.background, alloc.background);
  // В режиме вариантов считаем только варианты на экране (удалённые/недонастроенные не в счёт).
  const hooksSum = fxLab ? labRows.reduce((n, row) => n + row.count, 0) : Object.values(alloc.hooks).reduce((a, b) => a + b, 0);
  const hooksRest = fxKeys.length ? hookTarget - hooksSum : 0;
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
      [fxSlice]: distribute(fxKeys, footageTarget),
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
  const [distributeGuideDismissed, setDistributeGuideDismissed] = useGuideDismiss(poolGuideId('distribute', fxLab), hasUnallocated, false);
  const [totalGuideDismissed, setTotalGuideDismissed] = useGuideDismiss(poolGuideId('total', fxLab), !distributeGuideDismissed, true);
  const showTotalGuide = !totalGuideDismissed;
  const showDistributeGuide = totalGuideDismissed && !distributeGuideDismissed;
  useMarkGuideSeen(poolGuideId('distribute', fxLab), totalGuideDismissed);
  // Шаги 3–4 — раскадровка справа (PoolStoryboard). Она есть только у футажа из вайбов,
  // без неё серия остаётся из двух шагов.
  const storyboardAvailable = footageTypePlane(state.background.footageType) === 'vibes' && units.some((unit) => unit.key.startsWith('footage:'));
  const poolGuideTotal = storyboardAvailable ? 4 : 2;

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
        text={t(fxLab ? 'wizard.pool.guideTotalTextVariants' : 'wizard.pool.guideTotalText')}
        dismissLabel={t('wizard.pool.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 1, total: poolGuideTotal })}
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

        {fxLab && (
          <SectionCard
            title={t('wizard.pool.fx')}
            note={!labRows.length ? t('wizard.pool.fxNoVariants')
              : labIncomplete ? t('wizard.pool.fxIncomplete')
                : restNote(hooksRest, t('wizard.pool.fxNote', { count: hookTarget }))}
            warn={!labRows.length || labIncomplete || hooksRest !== 0}
          >
            {labRows.map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-space-3">
                <MiniPill icon={hookKindIcon(row.kind)} label={row.label} trail={<i className="ml-[8px] inline-block h-[8px] w-[8px] shrink-0 rounded-full" style={{ background: row.color }} aria-hidden="true" />} />
                <Stepper value={row.count} onChange={row.set} />
              </div>
            ))}
          </SectionCard>
        )}

        {!fxLab && hooksInPool.length > 0 && (
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

        {!fxLab && stylesInPool.length > 0 && (
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
        title={t(fxLab ? 'wizard.pool.guideDistributeTitleVariants' : 'wizard.pool.guideDistributeTitle')}
        text={t(fxLab ? 'wizard.pool.guideDistributeTextVariants' : 'wizard.pool.guideDistributeText')}
        dismissLabel={storyboardAvailable ? t('wizard.pool.guideNext') : t('wizard.pool.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: poolGuideTotal })}
        onDismiss={() => setDistributeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={fxLab ? <PoolCombosGuideVisual /> : <PoolDistributeGuideVisual />}
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
  // Режим вариантов: «хук» комбинации — id варианта, склейка и стиль берутся из него же.
  // Порядок — как в списке вариантов: так же раскладывает ролики бэк (render_job).
  const fxLab = useFxLab();
  const labVariants = state.fxVariants.filter((v) => !v.draft);
  const hookEntries: [string, number][] = fxLab
    ? labVariants.map((v) => [v.id, alloc.variants?.[v.id] ?? 0])
    : Object.entries(alloc.hooks);
  const styleEntries: [string, number][] = fxLab ? [] : Object.entries(alloc.styles ?? {});
  const colorStyle = state.background.color
    ? (state.background.strobe ? alloc.strobeFont : alloc.colorFont) ?? state.subtitles.pool[0]
    : undefined;
  const combo = combinationAt(
    safeIndex,
    Object.entries(alloc.background),
    Object.entries(alloc.subtitles),
    hookEntries,
    styleEntries,
    units,
    Boolean(state.background.color),
    colorStyle
  );
  // имя бакета хранится по-русски (по нему матчит бэк) — показываем через словарь
  const bgLabel = combo.bg === '__color__'
    ? chip(state.background.strobe ? 'Строб' : 'Цвет')
    : combo.bg ? (units.find(unit => unit.key === combo.bg)?.name ?? chip(combo.bg.split(':')[1])) : undefined;
  const comboVariant = fxLab && combo.hook ? labVariants.find((v) => v.id === combo.hook) : undefined;
  const comboStyle = comboVariant ? selectedStyles(comboVariant.config)[0] : combo.style;
  const hookLabel = comboVariant ? variantHookLabel(comboVariant, chip) : combo.hook ? chip(HOOK_LABELS[combo.hook as HookKind]) : undefined;
  const hookConfig = comboVariant ? comboVariant.config : combo.hook ? state.hooks.configs[combo.hook as HookKind] : undefined;
  const transitionLabel = hookConfig?.effectGlue ? chip(hookConfig.effectGlue) : t('wizard.pool.notSelected');
  const styleLabel = comboStyle ? chip(comboStyle) : t('wizard.pool.noStyleSelected');

  /*
   * Раскадровка: у каждого видео батча — свой вайб и свои реальные клипы по склейкам
   * рецепта. Видео листаются пилюлей в шапке, стрелки ← → внутри — кадры видео.
   * Раскадровка есть у футажа из вайбов; у фото, строба и своих исходников — пояснение.
   */
  const plane = footageTypePlane(state.background.footageType);
  const slots: StoryboardSlot[] = useMemo(() => Array.from({ length: total }, (_, i) => {
    const bgKey = combinationAt(
      i, Object.entries(alloc.background), Object.entries(alloc.subtitles), hookEntries,
      styleEntries, units, Boolean(state.background.color), colorStyle
    ).bg;
    if (bgKey?.startsWith('footage:') && plane === 'vibes') return { index: i + 1, group: bgKey.slice('footage:'.length) };
    const reason = bgKey === '__color__' ? 'Строб и цвет собираются из цветовых планов — исходники не нужны'
      : bgKey?.startsWith('photo:') ? 'Фото подберутся при генерации — раскадровка пока только для видео'
        : bgKey?.startsWith('upload:') ? 'Своё видео — ваши клипы пойдут в том порядке, в каком загружены'
          : bgKey?.startsWith('footage:') ? 'Раскадровка пока только для вайбов — коллекция подберётся при генерации'
            : 'Фон этого видео ещё не распределён';
    return { index: i + 1, reason };
  }), [total, alloc, units, state.background.color, colorStyle, plane]);
  const chips = [
    { icon: combo.bg === '__color__' ? strobeIcon(14) : combo.bg?.startsWith('photo') ? photoIcon(14) : tagIcon(14), text: bgLabel ?? t('wizard.pool.notSelected'), off: !bgLabel },
    { icon: tIcon(14), text: combo.sub ?? t('wizard.pool.notSelected'), off: !combo.sub },
    { icon: comboVariant ? <i className="inline-block h-[8px] w-[8px] rounded-full" style={{ background: comboVariant.color }} aria-hidden="true" /> : boltIcon(14), text: hookLabel ?? t('wizard.pool.noHookSelected'), off: !hookLabel },
    { icon: <img src="/assets/figma/combo-transition.svg" width="16" height="16" alt="" />, text: transitionLabel, off: !hookConfig?.effectGlue },
    { icon: <img src="/assets/figma/combo-style.svg" width="16" height="16" alt="" />, text: styleLabel, off: !comboStyle }
  ];

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        {/* Одно видео батча: пилюля листает видео, внутри — его реальные клипы и замена кадров. */}
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

        <PoolStoryboard slots={slots} current={safeIndex} chips={chips} />
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
