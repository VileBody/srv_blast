import { ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { usePhone } from '../../lib/usePhone';
import { cn } from '../../lib/cn';
import { useChip } from '../../i18n/useChip';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { LimitsIndicator } from '../ui/LimitsIndicator';
import { Svg, W12, WizardActions } from './WizardFrame';
import { FxVariant, HOOK_LABELS, HookKind, hookPills, selectedEffectStyles, useWizardStore, WizardStateData } from '../../stores/wizardStore';
import { ActionBar, Button, Dialog } from '../ui/kit';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { footageTypePlane } from '../../data/footageTypes';
import { PoolStoryboard, StoryboardSlot } from './PoolStoryboard';
import { poolGuideId, poolStoryboardAvailable, poolTourTotal } from './storyboardData';
import { useGuideLiveDismissed } from '../guidance/guideLiveState';
import { TimelineEntryGuideVisual } from './timelineGuides';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { useScrollGuideIntoView } from '../guidance/useScrollGuideIntoView';
import { useFxLab, useLabPoolRows, variantHookLabel } from './FxLab';
import { selectedStyles } from './hookCatalog';

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
            <span className="w-[30px] shrink-0 text-[9px] leading-none text-white/45"><span className="inline-block">{row.label}</span></span>
            {row.items.map((item) => (
              <span key={item.text} className="relative flex h-full min-w-0 items-center gap-[4px] overflow-hidden rounded-[6px] bg-white/[0.06] px-[6px] text-[9px] leading-none text-white/70">
                {item.phase && <i className={cn('absolute inset-0 rounded-[6px]', on, item.phase === 'a' ? 'guide-sb-a' : 'guide-sb-b')} />}
                {!item.phase && <i className={cn('absolute inset-0 rounded-[6px]', on)} />}
                {item.dot && <i className="relative h-[5px] w-[5px] shrink-0 rounded-full" style={{ background: item.dot }} />}
                <span className="relative truncate text-white">{item.text}</span>
                <b className="relative ml-[2px] font-[400] text-white/55"><span className="inline-block">{item.n}</span></b>
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
    <span className="w12-step">
      <button type="button" aria-label={t('wizard.pool.less')} disabled={value <= min} onClick={() => onChange(value - 1)}><Svg>{W12.minus}</Svg></button>
      <strong className="w12-num"><span className="w12-l">{value}</span></strong>
      <button type="button" aria-label={t('wizard.pool.more')} onClick={() => onChange(value + 1)}><Svg>{W12.plus}</Svg></button>
    </span>
  );
}

function SectionCard({ title, note, warn, children }: { title: string; note: string; warn?: boolean; children: ReactNode }) {
  return (
    <section className={cn('w12-cut w12-pool-sec', warn && 'w12-invalid')}>
      <div className="w12-pool-sec-head">
        <h3>{title}</h3>
        <span className={cn('w12-pool-sec-note', warn && 'w12-warn')}>{note}</span>
      </div>
      <div className="w12-pool-rows">{children}</div>
    </section>
  );
}

function MiniPill({ icon, label, trail }: { icon: ReactNode; label: string; trail?: ReactNode }) {
  return (
    <span className="w12-mini-pill">
      <span className="w12-mini-ic" aria-hidden="true">{icon}</span>
      <span className="w12-l">{label}</span>
      {trail}
    </span>
  );
}

/* Иконки пилюль — белые, в кружке 22 */
const WHITE = 'var(--text)';
const tagIcon = (size = 12) => <SvgMaskIcon src="/assets/figma/icon-tag.svg" style={{ width: size, height: size * 0.81, color: WHITE, transform: 'rotate(-22.23deg)' }} />;
const photoIcon = (size = 12) => <SvgMaskIcon src="/assets/figma/icon-photo.svg" style={{ width: size, height: size * 0.9, color: WHITE }} />;
const boltIcon = (size = 12) => <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: size * 0.65, height: size, color: WHITE }} />;
const strobeIcon = (size = 12) => <SvgMaskIcon src="/assets/figma/icon-strobe.svg" style={{ width: size, height: size, color: WHITE }} />;
const tIcon = (size?: number) => <span className="w12-t-it" style={size ? { fontSize: size } : undefined}>T</span>;

const HOOK_ICON_SRC: Record<HookKind, string> = {
  warmup: '/assets/figma/hook-sound.svg',
  object: '/assets/figma/hook-object.svg',
  effects: '/assets/figma/hook-effects.svg',
  motion: '/assets/figma/hook-motion.svg',
  thought: '/assets/figma/hook-thought.svg',
  none: '/assets/figma/icon-bolt.svg'
};

function hookKindIcon(kind: HookKind, size = 12) {
  return <SvgMaskIcon src={HOOK_ICON_SRC[kind]} style={{ width: size * 0.92, height: size, color: WHITE }} />;
}

export function StageSlice() {
  const { t } = useTranslation();
  const chip = useChip();
  const state = useWizardStore();
  const alloc = state.allocation;

  // Правки стола и закреплённые кадры живут у конкретного ролика. Распределение, которое
  // меняет ролику комбинацию (или перекладывает вайбы — раскадровка тогда собирается
  // заново), их сбросит — поэтому сначала спрашиваем.
  const [pendingAlloc, setPendingAlloc] = useState<null | { patch: Partial<WizardStateData['allocation']>; hit: number[] }>(null);
  const setAllocation = (patch: Partial<WizardStateData['allocation']>) => {
    const before = combosOf(state);
    const after = combosOf({ ...state, allocation: { ...alloc, ...patch } });
    const layout = (list: Combo[]) => JSON.stringify(list.filter((c) => c.group).map((c) => [c.slotIndex, c.group]));
    const relaid = layout(before) !== layout(after);
    const hit = before.filter((c) => {
      const edited = state.montage.videos[c.index]?.edited && state.montage.videos[c.index].sig === c.sig;
      const pinned = Object.keys(state.storyboard.videos[c.slotIndex]?.pins ?? {}).length > 0;
      return (edited && after[c.index]?.sig !== c.sig) || (pinned && relaid);
    }).map((c) => c.index + 1);
    if (hit.length) setPendingAlloc({ patch, hit });
    else state.setAllocation(patch);
  };

  const units = useMemo(() => backgroundUnits(state.background), [state.background]);
  const colorGroup = state.background.color
    ? { label: state.background.strobe ? 'Строб' : 'Цвет', strobe: state.background.strobe }
    : null;
  const fixedCount = colorGroup ? 1 : 0;
  const subtitleStyles = state.subtitles.pool;
  // Режим вариантов FX (по умолчанию): строки секции FX — варианты (стиль внутри варианта),
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
      // Субтитры раздаются по ВИДЕО (всего минус цветные), а не по типам фона: при
      // «Всего видео» больше числа типов сверка с unitKeys.length каждый раз при открытии
      // «Пула» откатывала раздачу человека и навсегда держала «нераспределено: +N».
      const subtitleTarget = Math.max(0, alloc.total - fixedCount);
      if (sameHookKinds && hookSum === hookTarget && sameStyles && (!stylesInPool.length || stylesSum === hookTarget)
        && sameSubtitles && (!subtitleStyles.length || subtitleSum === subtitleTarget)) return;
      state.setAllocation({
        [fxSlice]: sameHookKinds && hookSum === hookTarget ? fxAlloc : distribute(hookKinds, hookTarget),
        styles: sameStyles && stylesSum === hookTarget ? alloc.styles : distribute(stylesInPool, hookTarget),
        subtitles: sameSubtitles && subtitleSum === subtitleTarget ? alloc.subtitles : distribute(subtitleStyles, subtitleTarget)
      });
      return;
    }
    state.setAllocation({
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
  // Секции листаются сами: края тают там, где за ними есть ещё
  const [secFade, setSecFade] = useState({ top: false, bottom: false });
  const syncSecFade = () => {
    const el = distributeGuideTargetRef.current;
    if (!el) return;
    const next = { top: el.scrollTop > 2, bottom: el.scrollTop + el.clientHeight < el.scrollHeight - 2 };
    setSecFade((prev) => (prev.top === next.top && prev.bottom === next.bottom ? prev : next));
  };
  useEffect(syncSecFade);
  useEffect(() => {
    const el = distributeGuideTargetRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(syncSecFade);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
  // Шаги 3–4 — раскадровка справа (PoolStoryboard, только у футажа из вайбов), последний —
  // вход на таймлайн в футере (SliceWorkZone).
  const poolGuideTotal = poolTourTotal(state.background);

  // Явный скролл к цели до собственного instant-scrollIntoView оверлея: без него
  // цель может остаться частично за пределами внешнего скролл-контейнера страницы,
  // и рамка-обводка (она аккуратно клипуется по видимым границам предков) кажется
  // «обрезанной» — хотя технически это верное поведение для частично видимой цели.
  useScrollGuideIntoView(showTotalGuide, totalGuideTargetRef);
  useScrollGuideIntoView(showDistributeGuide, distributeGuideTargetRef);

  return (
    <>
      <Dialog
        open={Boolean(pendingAlloc)}
        title={t('wizard.pool.resetEditsTitle')}
        onClose={() => setPendingAlloc(null)}
        footer={(
          <ActionBar>
            <Button variant="ghost" onClick={() => setPendingAlloc(null)}>{t('wizard.pool.resetEditsCancel')}</Button>
            <Button variant="primary" onClick={() => { if (pendingAlloc) state.setAllocation(pendingAlloc.patch); setPendingAlloc(null); }}>{t('wizard.pool.resetEditsApply')}</Button>
          </ActionBar>
        )}
      >
        <p className="text-ui-16 text-text-80">{t('wizard.pool.resetEditsText', { videos: pendingAlloc?.hit.join(', ') ?? '' })}</p>
      </Dialog>
      {/* «Всего видео» неподвижен; секции листаются под ним */}
      <div ref={totalGuideTargetRef} className="w12-pool-total">
        <h2 className="w12-h2"><span className="w12-l">{t('wizard.pool.total')}</span></h2>
        {/* Кнопка «Распределить» появляется, только когда счётчики разошлись с раскладкой */}
        <span className="w12-pool-total-side">
          {hasUnallocated && (
            <button type="button" className="w12-small-btn w12-accent" onClick={distributeEvenly}><span className="w12-l">{t('wizard.pool.distributeEven')}</span></button>
          )}
          <Stepper value={alloc.total} min={fixedCount + (units.length ? 1 : 0)} onChange={(total) => setAllocation({ total })} />
          <LimitsIndicator />
        </span>
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

      {/* Секции листаются сами, края тают там, где есть ещё — как список типов FX */}
      <div ref={distributeGuideTargetRef} className="w12-pool-scroll" data-fade-t={secFade.top || undefined} data-fade-b={secFade.bottom || undefined} onScroll={syncSecFade}>
        <div className="w12-pool-secs">
          <SectionCard title={t('wizard.pool.background')} note={restNote(bgRest, t('wizard.pool.bgNote', { count: bgTarget }))} warn={bgRest !== 0}>
            {units.map((unit) => (
              <div key={unit.key} className="w12-pool-row">
                <span className="w12-pool-row-l">
                  <MiniPill icon={unit.icon === 'tag' ? tagIcon() : photoIcon()} label={t(unit.labelKey, { name: chip(unit.name) })} />
                  {unit.noHook && <span className="w12-chip w12-chip-sm"><span className="w12-l">{t('wizard.pool.noFx')}</span></span>}
                </span>
                <Stepper value={alloc.background[unit.key] ?? 0} onChange={(value) => setCount('background', unit.key, value)} />
              </div>
            ))}
            {/* Строб/цвет — такая же строка, как у фонов: всегда одно видео, поэтому вместо
                счётчика — шрифт, которым пойдут его субтитры */}
            {colorGroup && (
              <div className="w12-pool-row w12-pool-color">
                <span className="w12-pool-row-l">
                  <MiniPill icon={strobeIcon()} label={chip(colorGroup.label)} />
                  <span className="w12-chip w12-chip-sm"><span className="w12-l">{t('wizard.pool.oneVideo')}</span></span>
                </span>
                {subtitleStyles.length > 0 ? (
                  <span className="w12-pool-font">
                    <span className="w12-pool-font-l">{t('wizard.pool.font')}</span>
                    <span className="w12-types" role="radiogroup" aria-label={t('wizard.pool.font')}>
                      {subtitleStyles.map((style) => {
                        const field = colorGroup.strobe ? 'strobeFont' : 'colorFont';
                        const current = colorGroup.strobe ? alloc.strobeFont : alloc.colorFont;
                        return (
                          <button key={style} type="button" role="radio" className="w12-type" aria-checked={current === style} aria-pressed={current === style} onClick={() => setAllocation({ [field]: style })}>
                            <span className="w12-l">{style}</span>
                          </button>
                        );
                      })}
                    </span>
                  </span>
                ) : <span className="w12-set-note">{t('wizard.pool.noStyles')}</span>}
              </div>
            )}
            {units.length === 0 && !colorGroup && <p className="w12-set-empty">{t('wizard.pool.bgEmpty')}</p>}
          </SectionCard>

          {subtitleStyles.length > 0 && (
            <SectionCard title={t('wizard.pool.subtitles')} note={restNote(subsRest, t('wizard.pool.subsNote', { count: bgTarget }))} warn={subsRest !== 0}>
              {subtitleStyles.map((style) => (
                <div key={style} className="w12-pool-row">
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
                <div key={row.id} className="w12-pool-row">
                  <MiniPill icon={hookKindIcon(row.kind)} label={row.label} trail={<i className="w12-fx-dot" style={{ background: row.color }} aria-hidden="true" />} />
                  <Stepper value={row.count} onChange={(n) => setAllocation({ variants: { ...alloc.variants, [row.id]: Math.max(0, n) } })} />
                </div>
              ))}
            </SectionCard>
          )}

          {!fxLab && hooksInPool.length > 0 && (
            <SectionCard title={t('wizard.pool.fx')} note={restNote(hooksRest, t('wizard.pool.fxNote', { count: hookTarget }))} warn={hooksRest !== 0}>
              {hooksInPool.map((pill) => (
                <div key={pill.kind} className="w12-pool-row">
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
                <div key={style} className="w12-pool-row">
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
        dismissLabel={t('wizard.pool.guideNext')}
        progressLabel={t('wizard.guideProgress', { current: 2, total: poolGuideTotal })}
        onDismiss={() => setDistributeGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={fxLab ? <PoolCombosGuideVisual /> : <PoolDistributeGuideVisual />}
      />
    </>
  );
}

export function combinationAt(
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

/* ── ролики батча: номер, фон, стиль субтитров, вариант FX — общая раскладка «Пула» и стола ── */
export interface Combo {
  index: number;
  /** номер видео в раскадровке и в рендере (index + 1) */
  slotIndex: number;
  /** вайб футажа — у фото, цвета и своих видео его нет */
  group?: string;
  /** ключ фона из распределения «Пула»: footage:…, photo:…, upload:…, __color__ */
  bgKey?: string;
  bgLabel: string;
  sub?: string;
  variant?: FxVariant;
  /** хук возможен: вертикальное видео (не фото, не цвет, не 16:9) — как hook_allowed рендера */
  hookAllowed: boolean;
  /** выход 9:16 (всё, кроме 16:9-футажа и своего видео 16:9) — на нём встаёт рамка */
  vertical: boolean;
  /** комбинация целиком — под неё сделаны правки стола */
  sig: string;
}

/** Ролики батча по распределению — та же раскладка, что у «Комбинаций» и рендера. */
export function combosOf(state: Pick<WizardStateData, 'background' | 'allocation' | 'fxVariants' | 'subtitles'>): Combo[] {
  const { background, allocation: alloc, fxVariants, subtitles } = state;
  const units = backgroundUnits(background);
  const live = fxVariants.filter((v) => !v.draft);
  const hookEntries: [string, number][] = live.map((v) => [v.id, alloc.variants?.[v.id] ?? 0]);
  const colorStyle = background.color ? (background.strobe ? alloc.strobeFont : alloc.colorFont) ?? subtitles.pool[0] : undefined;
  const total = Math.max(1, alloc.total);
  return Array.from({ length: total }, (_, i) => {
    const c = combinationAt(i, Object.entries(alloc.background), Object.entries(alloc.subtitles), hookEntries, [], units, Boolean(background.color), colorStyle);
    const unit = units.find((u) => u.key === c.bg);
    const variant = live.find((v) => v.id === c.hook);
    return {
      index: i,
      slotIndex: i + 1,
      group: c.bg?.startsWith('footage:') ? c.bg.slice('footage:'.length) : undefined,
      bgKey: c.bg,
      bgLabel: c.bg === '__color__' ? (background.strobe ? 'Строб' : 'Цвет') : unit?.name ?? c.bg?.split(':')[1] ?? '—',
      sub: c.sub,
      variant,
      // хук рендер ставит только на вертикальное видео (зеркало hook_allowed в render_job)
      hookAllowed: Boolean(unit && !unit.noHook),
      vertical: c.bg === '__color__' || Boolean(c.bg?.startsWith('photo:')) || Boolean(unit && !unit.noHook),
      sig: [c.bg ?? '', c.sub ?? '', variant?.id ?? ''].join('|')
    };
  });
}


/** index/onIndex — видео на экране снаружи (его же открывает таймлайн); onOpenTimeline — кнопка «Таймлайн» в шапке;
 *  edited — у видео на экране есть ручные правки с таймлайна (пилюля «Изменён» над чипами ролика). */
export function SliceWorkZone({ ready, canContinue, loading, onBack, onNext, index: indexProp, onIndex, onOpenTimeline, edited }: {
  ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void;
  index?: number; onIndex?: (index: number) => void; onOpenTimeline?: (index: number) => void; edited?: boolean;
}) {
  const { t } = useTranslation();
  const chip = useChip();
  const state = useWizardStore();
  const alloc = state.allocation;
  const units = useMemo(() => backgroundUnits(state.background), [state.background]);
  const [indexState, setIndexState] = useState(0);
  const index = indexProp ?? indexState;
  const setIndex = (next: number) => { setIndexState(next); onIndex?.(next); };

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

  // Последний шаг тура «Пула» — вход на таймлайн. Ждёт живого закрытия предыдущего шага:
  // замены кадра (если раскадровка есть) или распределения.
  const timelineGuideRef = useRef<HTMLButtonElement>(null);
  const withStoryboard = poolStoryboardAvailable(state.background);
  const prevGuideDismissed = useGuideLiveDismissed(poolGuideId(withStoryboard ? 'replace' : 'distribute', fxLab));
  const [timelineGuideDismissed, setTimelineGuideDismissed] = useGuideDismiss(poolGuideId('timeline', fxLab), false);
  const showTimelineGuide = Boolean(onOpenTimeline) && prevGuideDismissed && !timelineGuideDismissed;
  useMarkGuideSeen(poolGuideId('timeline', fxLab), showTimelineGuide);
  const openTimeline = () => { if (!timelineGuideDismissed) setTimelineGuideDismissed(true); onOpenTimeline?.(safeIndex); };
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
    <aside className="w12-col-aside">
      <div className="w12-card w12-pv-card">
        {/* Одно видео батча: листалка переключает видео, внутри — его реальные клипы и замена кадров */}
        <div className="w12-aside-head w12-combo-head">
          <h2>{t('wizard.pool.combinations')}</h2>
          <div className="w12-pager">
            <button type="button" aria-label={t('wizard.pool.prevCombo')} disabled={total < 2} onClick={() => setIndex((safeIndex - 1 + total) % total)}><Svg>{W12.left}</Svg></button>
            <span className="w12-num"><span className="w12-l">{safeIndex + 1} / {total}</span></span>
            <button type="button" aria-label={t('wizard.pool.nextCombo')} disabled={total < 2} onClick={() => setIndex((safeIndex + 1) % total)}><Svg>{W12.right}</Svg></button>
          </div>
        </div>

        <PoolStoryboard slots={slots} current={safeIndex} chips={chips} edited={edited} />
      </div>

      <div className="w12-card w12-foot-card">
        {/* Вход на таймлайн — главное действие футера: там ролик на экране собирается по кадрам.
            «Сгенерировать» рядом с «Назад» и того же тона — отправить можно и без таймлайна. */}
        {onOpenTimeline && (
          <button ref={timelineGuideRef} type="button" className="w12-tl-entry" onClick={openTimeline}>
            <span className="w12-tl-entry-ic"><svg viewBox="0 0 20 20" className="w12-i" aria-hidden="true"><path d="M2 5h16M2 10h16M2 15h16M6 3v4m5 1v4m4 1v4" /></svg></span>
            <span className="w12-tl-entry-t"><b className="w12-l">{t('wizard.pool.openTimeline')}</b><small className="w12-l">{t('wizard.pool.timelineHint')}</small></span>
            <span className="w12-tl-entry-go"><Svg>{W12.right}</Svg></span>
          </button>
        )}
        <WizardActions ready={ready} loading={loading} onBack={onBack} onNext={onNext} nextLabel={t('wizard.pool.generate')} tone={onOpenTimeline ? 'field' : undefined} />
      </div>

      <ActionGuideOverlay
        open={showTimelineGuide}
        targetRef={timelineGuideRef}
        title={t('wizard.pool.guideTimelineTitle')}
        text={t('wizard.pool.guideTimelineText')}
        dismissLabel={t('wizard.pool.guideDismiss')}
        progressLabel={t('wizard.guideProgress', { current: poolTourTotal(state.background), total: poolTourTotal(state.background) })}
        onDismiss={() => setTimelineGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<TimelineEntryGuideVisual />}
      />
    </aside>
  );
}
