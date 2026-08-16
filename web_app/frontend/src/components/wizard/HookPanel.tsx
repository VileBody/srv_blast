import { ChangeEvent, type RefObject, useEffect, useRef, useState } from 'react';
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
import { SubtitlePreview } from './SubtitlePreview';

/*
 * Этап «Хук» (Figma W18 → W24/32 → W25/34 → W26/28/29/30 → W27 → W31):
 * единый тайминг дропа (чипы во всю высоту панели), строки типов 620×80 со скроллом
 * под градиентный оверлей, «?»-подсказки плашками внутри строк (тексты из макета),
 * настройка в рабочей зоне; «Эффекты» — шаги с подтверждением галочкой.
 */

const HOOK_TYPES: { kind: HookKind; icon: string; iconW: number; iconH: number; hint: string }[] = [
  { kind: 'sound', icon: '/assets/figma/hook-sound.svg', iconW: 16, iconH: 18, hint: 'wizard.fx.hintSound' },
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
const EFFECT_GLUES = effectsRegistry.glue.map((e) => e.label);
const EFFECT_STYLES = effectsRegistry.style.map((e) => e.label);
const MOTIONS = ['Свайп', 'Тап', 'Зум', 'Задержи', 'Голова'];
const THOUGHTS = ['Панчлайн', 'Пропущенное слово', 'Эхо', 'Вопрос', 'Инверсия'];

/*
 * Иконки чипов из Figma. baked — SVG уже содержит фиолетовый круг 40×40;
 * inner — только глиф, круг #5f42b9 рисуем в CSS; спец-случаи (Квадрат, Вопрос) — inline.
 */
type ChipIconDef = { src?: string; inner?: boolean; kind?: 'square' | 'question'; big?: boolean };
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
  'Инверсия': { src: '/assets/figma/thg-inversion-inner.svg', inner: true }
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

/** Первый шаг зависит от типа хука, два следующих общие. */
const FIRST_STEP: Record<HookKind, HookStep> = {
  sound: { key: 'sound', title: 'wizard.fx.loadSound', options: [] },
  object: { key: 'object', title: 'wizard.fx.chooseObject', options: OBJECTS },
  effects: { key: 'effectHook', title: 'wizard.fx.stepFx', options: EFFECT_HOOKS },
  motion: { key: 'motion', title: 'wizard.fx.chooseMotion', options: MOTIONS },
  thought: { key: 'thought', title: 'wizard.fx.chooseThought', options: THOUGHTS }
};

export function hookSteps(kind: HookKind): HookStep[] {
  return [FIRST_STEP[kind], GLUE_STEP, STYLE_STEP];
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

export function StageHooks() {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const setHooks = useWizardStore((state) => state.setHooks);
  const track = useWizardStore((state) => state.track);
  const dropsQuery = useQuery({ queryKey: ['drops'], queryFn: api.drops });
  const [customDrop, setCustomDrop] = useState(false);
  const [hint, setHint] = useState<HookKind | null>(null);

  const drops = dropsQuery.data?.drops ?? [];
  const customActive = Boolean(hooks.dropTime && !drops.some((d) => d.time === hooks.dropTime));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-space-4">
        <h2 className="wizard-h flex items-center gap-space-3">
          <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 15, height: 21, color: 'var(--accent-light)' }} />
          {t('wizard.fx.title')}
        </h2>
        <span className="wizard-body">{t('wizard.fx.chooseDrop')}</span>
      </div>

      {/* Тайминг дропа (Figma 606:217): панель 620×60, активный чип — пил во всю высоту */}
      <div className="mt-[20px] flex h-[60px] shrink-0 items-stretch rounded-r15 bg-grad-soft-10">
        {drops.map((drop) => (
          <button
            key={drop.time}
            type="button"
            className={cn(
              'flex h-full flex-1 items-center justify-center rounded-r15 text-[24px] font-[350] text-text-80 transition hover:text-text max-xl:text-[17px]',
              hooks.dropTime === drop.time && 'border-2 border-accent-light bg-grad-soft-20 !text-text'
            )}
            onClick={() => { setCustomDrop(false); setHooks({ dropTime: drop.time }); }}
          >
            {drop.time}
          </button>
        ))}
        {customDrop ? (
          <input
            autoFocus
            className="soft-input !h-full flex-[1.4] !w-auto"
            placeholder="00:00:00"
            defaultValue={customActive ? hooks.dropTime : ''}
            onChange={(e: ChangeEvent<HTMLInputElement>) => { e.target.value = clampDrop(e.target.value, track?.durationS); }}
            onBlur={(e) => { if (e.target.value) setHooks({ dropTime: clampDrop(e.target.value, track?.durationS) }); setCustomDrop(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
          />
        ) : (
          <button
            type="button"
            className={cn(
              'flex h-full flex-[1.4] items-center justify-center rounded-r15 text-[24px] font-[350] text-text-80 transition hover:text-text max-xl:text-[17px]',
              customActive && 'border-2 border-accent-light bg-grad-soft-20 !text-text'
            )}
            onClick={() => setCustomDrop(true)}
          >
            {customActive ? hooks.dropTime : t('wizard.fx.customDrop')}
          </button>
        )}
      </div>

      <p className="wizard-body mt-[28px] shrink-0">{t('wizard.fx.chooseType')}</p>

      {/* Список типов: строки 620×80, скролл уходит под градиентные фейды (Figma Rectangle 771/772) */}
      <div className="relative mt-[12px] min-h-0 flex-1">
        <span className="pointer-events-none absolute inset-x-0 top-0 z-[2] h-[28px]" style={{ background: 'linear-gradient(180deg, #140e24 0%, rgba(20,14,36,0) 100%)' }} />
        <span className="pointer-events-none absolute inset-x-0 bottom-0 z-[2] h-[28px]" style={{ background: 'linear-gradient(0deg, #140e24 0%, rgba(20,14,36,0) 100%)' }} />
        <div className="no-scrollbar flex h-full flex-col gap-[20px] overflow-y-auto py-[16px]">
          {HOOK_TYPES.map((item) => {
            const active = hooks.kind === item.kind;
            const configured = hookPills(hooks).some((pill) => pill.kind === item.kind);
            const locked = !hooks.dropTime;
            return (
              <button
                key={item.kind}
                type="button"
                disabled={locked}
                className={cn(
                  'relative flex h-[80px] shrink-0 items-center rounded-r15 bg-grad-soft-10 px-[28px] text-left transition hover:bg-grad-soft-20 hover:shadow-[inset_0_0_0_1px_rgba(139,111,230,.55)]',
                  // Подсвечены все настроенные типы, а не только открытый (правка ревью)
                  (active || configured) && 'border-2 border-accent-light',
                  active && 'bg-grad-soft-20',
                  // Типы неактивны, пока не выбран тайминг дропа (правка ревью)
                  locked && 'cursor-not-allowed opacity-45'
                )}
                onClick={() => setHooks({ kind: item.kind })}
              >
                <SvgMaskIcon src={item.icon} style={{ width: item.iconW, height: item.iconH, color: active || configured ? 'var(--accent-light)' : 'var(--text-80)' }} />
                <span className="wizard-body ml-space-4 !text-text">
                  {chip(HOOK_LABELS[item.kind])}
                </span>
                {/* Подсказка-плашка внутри строки (Figma Group 1865/1866): начинается после лейблов, не наезжает */}
                {hint === item.kind && (
                  <span className="absolute right-[84px] top-1/2 z-[1] flex h-[58px] w-[min(390px,58%)] -translate-y-1/2 items-center overflow-hidden rounded-r10 bg-[#2b2145] px-space-4 text-[15px] leading-[1.25] text-text shadow-[0_8px_28px_rgba(0,0,0,.45)] ring-1 ring-[var(--accent-light)]">
                    {t(item.hint)}
                  </span>
                )}
                <span
                  className="absolute right-[28px] z-[2] flex h-[40px] w-[40px] items-center justify-center"
                  onMouseEnter={() => setHint(item.kind)}
                  onMouseLeave={() => setHint(null)}
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
function ChipRow({ options, value, onPick, rightGap = 0, edgePad = 0 }: {
  options: string[];
  value?: string;
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
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={cn('glue-chip !h-[52px] !gap-space-3 !pl-[6px] !pr-space-4 !text-[18px]', value === option && 'is-selected relative z-[2]')}
            onClick={() => { if (!scroll.moved()) onPick(value === option ? undefined : option); }}
          >
            <span className="scale-[0.8]"><ChipIcon label={option} /></span>
            {chip(option)}
          </button>
        ))}
      </div>
    </div>
  );
}

const KIND_ORDER: HookKind[] = ['sound', 'object', 'effects', 'motion', 'thought'];

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
interface FullscreenSoundControls {
  inputRef: RefObject<HTMLInputElement>;
  playing: boolean;
  canPlay: boolean;
  onToggle: () => void;
  onRemove: () => void;
}

/**
 * Плеер использует только заранее подготовленный composite preview. Никаких AE/LLM-вызовов
 * из интерактива: если файла для комбинации ещё нет, остаётся нейтральный Figma-fallback.
 */
function EffectPreview({ style, hook, lyrics }: { style: string; hook?: string; lyrics?: string }) {
  const [broken, setBroken] = useState(false);
  const previewQuery = useQuery({
    queryKey: ['composite-preview', style, hook],
    queryFn: () => api.compositePreview(style, hook!),
    enabled: Boolean(hook),
    staleTime: Infinity,
    retry: false
  });

  useEffect(() => setBroken(false), [previewQuery.data?.previewUrl]);

  return (
    <>
      {hook && previewQuery.data?.previewUrl && !broken && (
        <video key={previewQuery.data.previewUrl} src={previewQuery.data.previewUrl} className="absolute inset-0 h-full w-full object-cover" muted loop playsInline autoPlay preload="metadata" onError={() => setBroken(true)} />
      )}
      {(!previewQuery.data?.previewUrl || broken) && <SubtitlePreview className="absolute inset-0" styleName={style} lyrics={lyrics} effect={hook} />}
    </>
  );
}

function HooksFullscreen({
  onCollapse,
  canContinue,
  onNext,
  soundControls
}: {
  onCollapse: () => void;
  canContinue: boolean;
  onNext: () => void;
  soundControls: FullscreenSoundControls;
}) {
  const { t } = useTranslation();
  const chip = useChip();
  const hooks = useWizardStore((state) => state.hooks);
  const subtitleStyle = useWizardStore((state) => state.subtitles.pool[0] ?? 'Impulse');
  const lyrics = useWizardStore((state) => state.fragmentLyrics || state.lyrics);
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
      <p className="wizard-body px-[28px] leading-[29px]">{title}</p>
      <div className="mt-[28px]">
        <ChipRow options={options} value={config[key] as string | undefined} edgePad={28} onPick={(option) => { setHooks({ config: { [key]: option } }); setStep(index); }} />
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
  const first = steps[0].options.length > 0 ? steps[0] : null;
  const currentDef = steps[Math.min(step, steps.length - 1)];
  const previewHook = (currentDef && config[currentDef.key] as string | undefined)
    ?? config.effectHook
    ?? hookPickLabel(config, (label) => label)
    ?? HOOK_LABELS[kind];

  /** Стрелки плеера листают варианты ВНУТРИ активной группы, не переключая группы. */
  const cycleVariant = (delta: number) => {
    if (!currentDef || currentDef.options.length === 0) return;
    const current = config[currentDef.key] as string | undefined;
    const index = current ? currentDef.options.indexOf(current) : -1;
    const nextIndex = index < 0
      ? (delta > 0 ? 0 : currentDef.options.length - 1)
      : (index + delta + currentDef.options.length) % currentDef.options.length;
    setHooks({ config: { [currentDef.key]: currentDef.options[nextIndex] } });
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

      {first ? section(t(first.title), 0, first.options, first.key) : (
        <div className={cn('h-[175px] shrink-0 rounded-r15 bg-grad-soft-10 p-[28px]', step === 0 && 'shadow-[inset_0_0_0_1px_var(--accent-light)]')}>
          <p className="wizard-body leading-[29px]">{t('wizard.fx.loadSound')}</p>
          <div className="mt-[28px]">
            {config.sound ? (
              <div className="dash-panel-r10 flex h-[60px] w-full items-center gap-space-3 px-space-4 text-[18px] text-text-80">
                <button
                  type="button"
                  disabled={!soundControls.canPlay}
                  aria-label={soundControls.playing ? t('wizard.track.pause') : t('wizard.fx.listenSound')}
                  onClick={soundControls.onToggle}
                  className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-accent-light leading-[0] text-text transition hover:opacity-85 disabled:opacity-40"
                >
                  {soundControls.playing ? (
                    <span className="flex gap-[3px]" aria-hidden="true"><span className="h-[9px] w-[2.5px] rounded-[1px] bg-text" /><span className="h-[9px] w-[2.5px] rounded-[1px] bg-text" /></span>
                  ) : (
                    <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3.5 1.8v8.4L10.5 6 3.5 1.8Z" fill="currentColor" /></svg>
                  )}
                </button>
                <button type="button" className="min-w-0 flex-1 truncate text-left transition hover:text-text" title={t('wizard.track.replaceFile')} onClick={() => soundControls.inputRef.current?.click()}>
                  {config.sound}
                </button>
                <button type="button" aria-label={t('wizard.fx.deleteSound')} onClick={soundControls.onRemove} className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full text-text-60 transition hover:bg-accent-20 hover:text-text">
                  <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                </button>
              </div>
            ) : (
              <button type="button" className="dash-panel-r10 flex h-[60px] w-full items-center justify-center gap-space-3 text-[18px] text-text-80 transition hover:brightness-125" onClick={() => soundControls.inputRef.current?.click()}>
                <span aria-hidden="true" className="flex h-[26px] w-[26px] items-center justify-center rounded-full bg-accent-light leading-[0] text-text">
                  <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M6 2v8M2 6h8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                </span>
                {t('wizard.fx.soundFormats')}
              </button>
            )}
          </div>
        </div>
      )}
      {section(t(steps[1].title), 1, steps[1].options, steps[1].key)}
      {section(t(steps[2].title), 2, steps[2].options, steps[2].key)}
    </div>
  );

  const right = (
    <div className="flex h-full flex-col">
      <div className="group relative h-[665px] shrink-0 overflow-hidden rounded-r15 bg-grad-soft-10">
        <EffectPreview style={subtitleStyle} hook={previewHook} lyrics={lyrics} />
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

        <span className="absolute bottom-[40px] left-0 right-0 z-[4] text-center text-[16px] font-[400] leading-[19px] text-text-60">
          {currentDef && config[currentDef.key] ? chip(config[currentDef.key] as string) : t('wizard.fx.chooseBelow')}
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
  const subtitleStyle = useWizardStore((state) => state.subtitles.pool[0] ?? 'Impulse');
  const lyrics = useWizardStore((state) => state.fragmentLyrics || state.lyrics);
  const setHooks = useWizardStore((state) => state.setHooks);
  const pillsScroll = useDragScroll();
  const soundInputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);

  const kind = hooks.kind;
  const config = (kind && hooks.configs[kind]) || {};
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

  // Прослушивание загруженного звука (правка ревью): blob живёт в ref до замены/удаления
  const soundAudioRef = useRef<HTMLAudioElement | null>(null);
  const soundUrlRef = useRef<string | null>(null);
  const [soundPlaying, setSoundPlaying] = useState(false);

  const onSoundUpload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    soundAudioRef.current?.pause();
    soundAudioRef.current = null;
    setSoundPlaying(false);
    if (soundUrlRef.current) URL.revokeObjectURL(soundUrlRef.current);
    soundUrlRef.current = URL.createObjectURL(file);
    setHooks({ config: { sound: file.name.replace(/\.[^.]+$/, '') } });
  };

  const toggleSoundPlay = () => {
    if (!soundUrlRef.current) return;
    if (!soundAudioRef.current) {
      soundAudioRef.current = new Audio(soundUrlRef.current);
      soundAudioRef.current.onended = () => setSoundPlaying(false);
    }
    if (soundPlaying) {
      soundAudioRef.current.pause();
      setSoundPlaying(false);
    } else {
      void soundAudioRef.current.play();
      setSoundPlaying(true);
    }
  };

  const removeSound = () => {
    soundAudioRef.current?.pause();
    soundAudioRef.current = null;
    setSoundPlaying(false);
    if (soundUrlRef.current) URL.revokeObjectURL(soundUrlRef.current);
    soundUrlRef.current = null;
    // Без звука хук «Звук» перестаёт быть настроенным — пилюля уходит сама
    setHooks({ config: { sound: undefined } });
  };

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
  const canAdvance = Boolean(stepValue) && stepIndex < steps.length - 1;

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
  const soundStep = (
    <>
      <input ref={soundInputRef} type="file" accept="audio/*" className="sr-only" onChange={onSoundUpload} />
      {config.sound ? (
        <div className="dash-panel-r10 flex h-[52px] w-full items-center gap-space-3 px-space-4 text-[18px] text-text-80">
          {/* Плей/пауза загруженного звука (правка ревью) */}
          <button
            type="button"
            aria-label={soundPlaying ? t('wizard.track.pause') : t('wizard.fx.listenSound')}
            onClick={toggleSoundPlay}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full bg-accent-light leading-[0] text-text transition hover:opacity-85"
          >
            {soundPlaying ? (
              <span className="flex gap-[3px]" aria-hidden="true"><span className="h-[9px] w-[2.5px] rounded-[1px] bg-text" /><span className="h-[9px] w-[2.5px] rounded-[1px] bg-text" /></span>
            ) : (
              <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3.5 1.8v8.4L10.5 6 3.5 1.8Z" fill="currentColor" /></svg>
            )}
          </button>
          <button type="button" className="min-w-0 flex-1 truncate text-left transition hover:text-text" title={t('wizard.track.replaceFile')} onClick={() => soundInputRef.current?.click()}>
            {config.sound}
          </button>
          {/* Крестик: удаляет звук и отменяет хук (правка ревью) */}
          <button type="button" aria-label={t('wizard.fx.deleteSound')} onClick={removeSound} className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full text-text-60 transition hover:bg-accent-20 hover:text-text">
            <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="dash-panel-r10 flex h-[52px] w-full items-center justify-center gap-space-3 text-[18px] text-text-80 transition hover:brightness-125"
          onClick={() => soundInputRef.current?.click()}
        >
          <span aria-hidden="true" className="flex h-[26px] w-[26px] items-center justify-center rounded-full bg-accent-light leading-[0] text-text">
            <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M6 2v8M2 6h8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </span>
          {t('wizard.fx.soundFormats')}
        </button>
      )}
    </>
  );

  const settings = kind && stepDef && (
    <div className="shrink-0 rounded-r15 bg-grad-soft-10 p-space-5">
      <div className="mb-space-4 flex items-center justify-between gap-space-3">
        <p className="wizard-body">{t(stepDef.title)}</p>
        <span className="flex items-center gap-space-1 rounded-r40 bg-accent-20 px-space-3 py-space-1 text-[14px] text-text-80">
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
            onPick={(option) => setHooks({ config: { [stepDef.key]: option } })}
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
          soundControls={{
            inputRef: soundInputRef,
            playing: soundPlaying,
            canPlay: Boolean(soundUrlRef.current),
            onToggle: toggleSoundPlay,
            onRemove: removeSound
          }}
        />
      )}
      <div className="card-2 flex min-h-0 flex-1 flex-col gap-space-5 px-space-6 py-space-6 max-lg:px-space-5">
        {/* Figma W41: разворот в фуллскрин — в правом верхнем углу FX-зоны */}
        <div className="flex shrink-0 items-center justify-between gap-space-3">
          <h2 className="wizard-h whitespace-nowrap">{t('wizard.workZone')}</h2>
          {/* Была голая иконка 20×20 без подложки — её просто не замечали. Теперь это
              обычная кнопка с обводкой и подписью: видно, что тут есть широкий режим. */}
          <button
            type="button"
            onClick={() => setFullscreen(true)}
            className="flex h-[37px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent-light bg-grad-soft-20 px-[12px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125"
          >
            <img src="/assets/figma/fx-expand.svg" width="16" height="16" alt="" aria-hidden />
            {t('common.expand')}
          </button>
        </div>

        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10">
          <EffectPreview
            style={subtitleStyle}
            lyrics={lyrics}
            hook={config.effectHook ?? hookPickLabel(config, (label) => label) ?? (kind ? HOOK_LABELS[kind] : undefined)}
          />
          <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          {!kind ? (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[223px] text-center">{t('wizard.fx.empty')}</p>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-space-3 p-space-5 text-center">
              <SvgMaskIcon
                src={HOOK_TYPES.find((item) => item.kind === kind)!.icon}
                style={{ width: 42, height: 46, color: 'var(--accent-light)' }}
              />
              <p className="wizard-body">{chip(HOOK_LABELS[kind])}</p>
              <p className="text-[15px] text-text-60">
                {kind === 'effects'
                  ? [config.effectHook, config.effectGlue, config.effectStyle].filter(Boolean).map((v) => chip(v as string)).join(' · ') || t('wizard.fx.configureThree')
                  : hookPickLabel(config, chip) ?? t('wizard.fx.chooseBelow')}
              </p>
            </div>
          )}
        </div>

        {settings}
      </div>

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
