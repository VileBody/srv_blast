import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import effectsRegistry from '../../data/effects-registry.json';
import { HookConfig, HookKind, TimelinePace, TimelineStyleRange, useWizardStore } from '../../stores/wizardStore';
import { EFFECT_HOOKS, EffectPreview, MOTIONS, NO_GLUE, OBJECTS, THOUGHTS, previewIdFor } from './HookPanel';
import { PACES, dropOrphanStyleLabels, useRecipeCuts } from './storyboardData';
import { usePlaybackUrl } from './useFragmentAudio';
import { Trans, useTranslation } from 'react-i18next';
import { ActionGuideOverlay } from '../guidance/ActionGuideOverlay';
import { useGuideDismiss, useMarkGuideSeen } from '../guidance/useGuideDismiss';
import { TimelineCutsGuideVisual, TimelineLibraryGuideVisual, TimelinePaceGuideVisual } from './timelineGuides';
import './FxTimeline.css';

/*
 * Полноэкранный таймлайн шага FX — рецепт ролика, общий для всех видео батча.
 *
 * Референс — CapCut/AE. Склейки не придумываются здесь: их считает оркестратор тем же
 * кодом, что и рендер (темп «авто» = разбиение рендера по битам трека, «реже/чаще» —
 * от той же сетки). Кадры в превью — реальные клипы первого выбранного вайба (образец;
 * итоговые исходники каждого видео собираются на «Пуле»). Хук всегда привязан к дропу,
 * стили лежат по границам кадров, до двух одновременно.
 *
 * Что уходит в рендер сейчас: склейки (через раскадровку «Пула»), хук (конфиг шага FX),
 * переход «ко всем склейкам» и набор стилей (конфиг шага FX). Разные переходы на разных
 * стыках и точные диапазоны стилей хранятся в рецепте, но рендер пока ставит один
 * переход на ролик и стиль до дропа/на весь ролик — это следующий шаг F3-оверлея.
 */

const FPS = 30;
const TD = 0.36;
const X0 = 20;
const pad = (n: number) => String(n).padStart(2, '0');
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const tc = (t: number) => { const f = Math.floor(Math.max(0, t) * FPS + 1e-6); return `${pad(Math.floor(f / FPS / 60))}:${pad(Math.floor(f / FPS) % 60)}.${pad(f % FPS)}`; };
const secs = (t: number) => `${t.toFixed(1).replace('.', ',')} с`;
const kadr = (n: number) => n % 10 === 1 && n % 100 !== 11 ? 'кадр' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'кадра' : 'кадров';
const eOut = (p: number) => 1 - Math.pow(1 - p, 4);
/* Корень приложения масштабируется CSS-zoom: координаты мыши и getBoundingClientRect —
   визуальные пиксели, а left/top у fixed-элементов умножаются на zoom ещё раз. */
const zoomScale = () => Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
const eIO = (p: number) => p < 0.5 ? 8 * p ** 4 : 1 - Math.pow(-2 * p + 2, 4) / 2;

/* ── тонкие глифы (один язык на всю библиотеку и дорожки) ── */
const C = (x: number, y: number, r: number, a = '') => `<circle cx="${x}" cy="${y}" r="${r}" ${a}/>`;
const P = (d: string, a = '') => `<path d="${d}" ${a}/>`;
const FILL = 'fill="currentColor" stroke="none"';
function star(n: number, r1: number, r2: number) { let d = ''; for (let k = 0; k < n * 2; k++) { const r = k % 2 ? r2 : r1; const a = Math.PI * k / n - Math.PI / 2; d += `${k ? 'L' : 'M'}${(12 + r * Math.cos(a)).toFixed(2)} ${(12 + r * Math.sin(a)).toFixed(2)}`; } return `${d}Z`; }
const ICONS: Record<string, string> = {
  back: P('M15 5l-7 7 7 7'), play: P('M6 3.5v13l11-6.5L6 3.5Z', FILL), pause: P('M6 4h3v12H6zM11 4h3v12h-3z', FILL),
  undo: P('M9 7L4.5 11.5 9 16') + P('M5 11.5h9a5 5 0 0 1 0 10h-2'), redo: P('M15 7l4.5 4.5L15 16') + P('M19 11.5h-9a5 5 0 0 0 0 10h2'),
  magnet: P('M6 4v7a6 6 0 0 0 12 0V4') + P('M6 8h4M14 8h4') + P('M10 4v7a2 2 0 0 0 4 0V4'),
  minus: P('M6 12h12'), plus: P('M12 6v12M6 12h12'), check: P('M5 12.5l4.5 4.5L19 7.5'),
  keys: '<rect x="3" y="6" width="18" height="12" rx="2.5"/>' + P('M7 10h.01M10.5 10h.01M14 10h.01M17 10h.01M8 14h8'),
  lock: '<rect x="6" y="11" width="12" height="9" rx="2"/>' + P('M9 11V8a3 3 0 0 1 6 0v3'),
  eye: P('M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z') + C(12, 12, 2.8),
  eyeoff: P('M2.5 12s3.5-6.5 9.5-6.5c2 0 3.7.7 5.1 1.7M21.5 12s-3.5 6.5-9.5 6.5c-2 0-3.7-.7-5.1-1.7') + P('M4 20L20 4'),
  chev: P('M6 9l6 6 6-6'), trash: P('M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12'),
  film: '<rect x="4" y="5" width="16" height="14" rx="2"/>' + P('M8 5v14M16 5v14M4 9.5h4M4 14.5h4M16 9.5h4M16 14.5h4'),
  frames: '<rect x="3" y="7" width="5" height="10" rx="1.5"/><rect x="9.5" y="7" width="5" height="10" rx="1.5"/><rect x="16" y="7" width="5" height="10" rx="1.5"/>',
  audio: P('M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10'),
  warmup: P('M3 12h3l2-5 3.5 10 3-7 1.5 2H21'), object: C(9, 9, 5) + '<rect x="11" y="11" width="9" height="9" rx="1"/>',
  effects: P('M13 2.5L5 13.5h6l-1 8 8-11h-6z'),
  motion: P('M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10V9a1.5 1.5 0 0 1 3 0v2M15 10.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-.8a5 5 0 0 1-4.1-2.2L4.4 14.6a1.5 1.5 0 0 1 2.4-1.8L9 15'),
  thought: P('M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8l-4 3.5V17H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z'),
  circle: C(12, 12, 7.5), square: '<rect x="5" y="5" width="14" height="14" rx="1"/>', rhomb: P('M12 3.5l8.5 8.5-8.5 8.5L3.5 12z'),
  star5: P(star(5, 8.8, 3.8)), star10: P(star(10, 8.8, 5.2)), bolt: P('M13 2.5L5 13.5h6l-1 8 8-11h-6z'),
  shutter: C(12, 12, 8.5) + P('M9.2 4L15.6 15M17.6 6L11.3 17M20.4 12.4H7.7M14.8 20L8.4 9M6.4 18l6.3-11M3.6 11.6h12.7'),
  slowshutter: P('M3 8h8M2 12h10M3 16h8') + C(17, 12, 4), negzoom: C(12, 12, 8) + P('M12 4a8 8 0 0 0 0 16z', FILL),
  swipe: P('M4 12h15M14 7l5 5-5 5') + P('M4 8v8'), tap: C(12, 12, 2.5, FILL) + C(12, 12, 6) + C(12, 12, 9.5, 'opacity=".45"'),
  pinch: P('M4 4l6 6M4 4h4.5M4 4v4.5M20 20l-6-6M20 20h-4.5M20 20v-4.5'), hold: C(12, 12, 8, 'opacity=".4"') + P('M12 4a8 8 0 0 1 8 8') + C(12, 12, 2.5, FILL),
  head: P('M9 21v-3.3A6.5 6.5 0 1 1 18.6 11l1.6 3h-2.2v2.8A1.7 1.7 0 0 1 16.3 18.5H14V21'),
  punch: P('M12 5v9') + C(12, 18.5, 1.1, FILL), missing: P('M3 9h18M3 15h5M16 15h5') + P('M10 13.5h4v3h-4z', 'stroke-dasharray="1.5 1.5"'),
  echo: C(7.5, 12, 2.5) + P('M12.5 8.5a5 5 0 0 1 0 7M16 6a8.5 8.5 0 0 1 0 12'),
  question: P('M9.3 9a2.8 2.8 0 1 1 3.9 2.6c-.8.3-1.2 1-1.2 1.8v.6') + C(12, 18, 1.1, FILL), inversion: P('M7 7.5h11l-3.5-3.5M17 16.5H6l3.5 3.5'),
  t_none: C(12, 12, 8) + P('M6.5 17.5l11-11'), t_snap: P('M12 3v18') + '<rect x="4" y="6" width="5" height="12" rx="1"/>' + P('M15 6h5v12h-5z', FILL),
  t_minimax: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8.5" y="8.5" width="7" height="7" rx="1"/>', t_extract: P('M12 19V6M7 11l5-5 5 5') + P('M4 20h16'),
  t_invert: C(12, 12, 8) + P('M12 4a8 8 0 0 1 0 16z', FILL), t_flash: C(12, 12, 3) + P('M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8'),
  xerox: P('M5 6h14M5 10h14M5 14h14M5 18h9'), glitch: P('M4 6h10M9 10h11M4 14h8M11 18h9'), neon: P('M4 17c3-9 5-1 8-8s4.5 1 8-4'),
  oldcam: '<rect x="3" y="9" width="12" height="10" rx="2"/>' + P('M15 12.5l5.5-3v9l-5.5-3') + C(6.5, 5.5, 2.3) + C(11.5, 5.5, 2.3),
  bw: C(12, 12, 8) + P('M12 4a8 8 0 0 0 0 16z', FILL), crystal: P('M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z'),
  night: P('M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z') + C(12, 12, 2.5, FILL), wave: P('M3 12c2-4.5 4-4.5 6 0s4 4.5 6 0 4-4.5 6 0')
};
const GLYPH: Record<string, string> = {
  'Круг': 'circle', 'Квадрат': 'square', 'Ромб': 'rhomb', 'Звезда-5': 'star5', 'Звезда-10': 'star10',
  'Молния': 'bolt', 'Затвор': 'shutter', 'Слоу-шаттер': 'slowshutter', 'Негатив зум': 'negzoom',
  'Свайп': 'swipe', 'Тап': 'tap', 'Зум': 'pinch', 'Задержи': 'hold', 'Голова': 'head',
  'Панчлайн': 'punch', 'Пропущенное слово': 'missing', 'Эхо': 'echo', 'Вопрос': 'question', 'Инверсия': 'inversion',
  [NO_GLUE]: 't_none', 'Щелчок': 't_snap', 'Минимакс': 't_minimax', 'Экстракт': 't_extract', 'Инверт': 't_invert', 'Вспышка': 't_flash',
  'Ксерокс': 'xerox', 'Глитч': 'glitch', 'Неон': 'neon', 'Старая камера': 'oldcam', 'Ч/Б': 'bw', 'Crystal Glow': 'crystal', 'Night Vision': 'night', 'Wave': 'wave'
};
const META: Record<string, string> = {
  'Молния': 'вспышка на дропе', 'Затвор': 'шторка на дропе', 'Слоу-шаттер': 'от дропа · тянется вправо', 'Негатив зум': 'инверсия и зум на дропе',
  'Свайп': 'интро в такт до дропа', 'Тап': 'интро в такт до дропа', 'Зум': 'интро в такт до дропа', 'Задержи': 'интро в такт до дропа', 'Голова': 'интро в такт до дропа',
  'Панчлайн': 'голос поверх трека', 'Пропущенное слово': 'голос поверх трека', 'Эхо': 'голос поверх трека', 'Вопрос': 'голос поверх трека', 'Инверсия': 'голос поверх трека',
  'Круг': 'форма на склейке до дропа', 'Квадрат': 'форма на склейке до дропа', 'Ромб': 'форма на склейке до дропа', 'Звезда-5': 'форма на склейке до дропа', 'Звезда-10': 'форма на склейке до дропа',
  [NO_GLUE]: 'жёсткая склейка', 'Щелчок': 'шторка слева направо', 'Минимакс': 'влёт с зумом', 'Экстракт': 'выезд снизу', 'Инверт': 'негатив на стыке', 'Вспышка': 'белая вспышка',
  'Ксерокс': 'жёсткий контраст и зерно', 'Глитч': 'цифровые сбои и сдвиги', 'Неон': 'светящиеся насыщенные цвета', 'Старая камера': 'сепия, виньетка, плёнка',
  'Ч/Б': 'выжженные чёрные', 'Crystal Glow': 'мягкое свечение', 'Night Vision': 'прибор ночного видения', 'Wave': 'синий тон и волна'
};
const TRANSITION_ANIM: Record<string, string> = { 'Щелчок': 'snap', 'Минимакс': 'minimax', 'Экстракт': 'extract', 'Инверт': 'invert', 'Вспышка': 'flash' };
const GLUES = [NO_GLUE, ...effectsRegistry.glue.map((e) => e.label)];
const STYLES = effectsRegistry.style.map((e) => e.label);

function Glyph({ name, size = 18, sw = 1.5 }: { name?: string; size?: number; sw?: number }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: ICONS[name ?? ''] ?? '' }} />;
}
function Ic({ label, kind, on, size = 32 }: { label?: string; kind: 'hook' | 'style' | 'trans' | 'frame'; on?: boolean; size?: number }) {
  return <span className={`fxt-ic k-${kind}${on ? ' on' : ''}`} style={{ width: size, height: size }}><Glyph name={GLYPH[label ?? ''] ?? label} size={Math.round(size * 0.53)} /></span>;
}

/* ── хуки: какой ключ конфига у какого типа и где он стоит относительно дропа ── */
type HookCatalogKind = Exclude<HookKind, 'none'>;
const HOOK_CATS: { kind: HookCatalogKind; label: string; icon: string; key: keyof HookConfig | null; options: string[] }[] = [
  { kind: 'warmup', label: 'Прогрев', icon: 'warmup', key: null, options: [] },
  { kind: 'object', label: 'Объект', icon: 'object', key: 'object', options: OBJECTS },
  { kind: 'effects', label: 'Эффекты', icon: 'effects', key: 'effectHook', options: EFFECT_HOOKS },
  { kind: 'motion', label: 'Движение', icon: 'motion', key: 'motion', options: MOTIONS },
  { kind: 'thought', label: 'Мысль', icon: 'thought', key: 'thought', options: THOUGHTS }
];
/* Длина интро F4 = LEAD_BY_DEVICE (mlcore/hooks/f4_motion/overlay.py), ×128/bpm кроме «фиксированных». */
const MOTION_LEAD: Record<string, [number, boolean]> = { 'Свайп': [4.304, false], 'Тап': [4.304, true], 'Зум': [4.204, false], 'Задержи': [4.304, true], 'Голова': [4.004, false] };

function hookLabel(kind: HookKind | undefined, config: HookConfig | undefined): string | undefined {
  if (!kind || !config) return undefined;
  if (kind === 'warmup') return config.sound ? (config.warmupKind === 'video' ? 'Своё видео' : 'Свой звук') : undefined;
  const cat = HOOK_CATS.find((c) => c.kind === kind);
  return cat?.key ? (config[cat.key] as string | undefined) : undefined;
}

/** Интервал хука на дорожке (секунды от начала отрывка) — как его ставит рендер. */
/** Длительность хуков «Эффектов» — default_duration из mlcore/hooks/f3_effect/manifest.json. */
const EFFECT_HOOK_SEC: Record<string, number> = { 'Молния': 0.63, 'Затвор': 0.6, 'Слоу-шаттер': 0.5, 'Негатив зум': 0.25 };

/** Длина слоу-шаттера — ровно как в рендере (overlay.py `__f3_hookDur`): стандарт,
 *  до 3-й склейки после дропа (если их меньше — до конца), до конца ролика. */
type SlowExtend = '' | 'after_drop:3' | 'to_end';
function slowShutterEnd(extend: SlowExtend, drop: number, dur: number, bounds: number[]): number {
  const base = drop + EFFECT_HOOK_SEC['Слоу-шаттер'];
  if (extend === 'to_end') return Math.max(base, dur);
  if (extend === 'after_drop:3') { const after = bounds.slice(1, -1).filter((b) => b > drop + 1e-3); return Math.max(base, after.length >= 3 ? after[2] : dur); }
  return Math.min(dur, base);
}
const SLOW_EXTENDS: [SlowExtend, string][] = [['', 'Стандарт'], ['after_drop:3', '3 кадра'], ['to_end', 'До конца']];

function hookSpan(kind: HookKind, config: HookConfig, drop: number, dur: number, bpm: number, bounds: number[]): [number, number] | null {
  if (kind === 'effects') {
    if (config.effectHook === 'Слоу-шаттер') return [drop, slowShutterEnd((config.effectHookExtend ?? '') as SlowExtend, drop, dur, bounds)];
    return [drop, Math.min(dur, drop + (EFFECT_HOOK_SEC[config.effectHook ?? ''] ?? 0.6))];
  }
  if (kind === 'motion') {
    const [lead, fixed] = MOTION_LEAD[config.motion ?? ''] ?? [4.3, false];
    const eff = fixed || !bpm ? lead : lead * 128 / bpm;
    return [Math.max(0, drop - eff), drop];
  }
  if (kind === 'thought') return [Math.max(0, drop - 2.8), drop];
  if (kind === 'object') return [0, drop];
  if (kind === 'warmup') {
    if (config.warmupKind === 'video') return [Math.max(0, drop - 1 - (config.videoDuration ?? 3)), Math.max(0, drop - 0.5)];
    return [Math.min(0.5, drop), Math.max(0, drop - 0.5)];
  }
  return null;
}

function styleFilter(active: string[], t: number): string {
  const f: string[] = [];
  if (active.includes('Ксерокс')) f.push('grayscale(1) contrast(2.3) brightness(1.08)');
  if (active.includes('Ч/Б')) f.push('grayscale(1) contrast(1.45) brightness(.9)');
  if (active.includes('Неон')) f.push('saturate(2.2) contrast(1.2) hue-rotate(-12deg) brightness(1.08)');
  if (active.includes('Старая камера')) f.push('sepia(.62) contrast(1.12) brightness(.93)');
  if (active.includes('Crystal Glow')) f.push('brightness(1.14) saturate(1.35)');
  if (active.includes('Night Vision')) f.push('grayscale(1) sepia(1) hue-rotate(58deg) saturate(3.4) brightness(1.1)');
  if (active.includes('Глитч')) f.push(`hue-rotate(${Math.sin(t * 37) > 0.7 ? 40 : 0}deg) saturate(1.3)`);
  if (active.includes('Wave')) f.push('hue-rotate(190deg) saturate(1.4)');
  return f.join(' ');
}

type Sel = { type: 'frame'; i: number } | { type: 'cut'; i: number } | { type: 'hook' } | { type: 'style'; uid: number } | { type: 'sub'; i: number } | null;
type LibKind = 'hook' | 'trans' | 'style';
interface LibItem { kind: LibKind; label: string; hookKind?: HookCatalogKind }
type PlaceTarget = { lane: string; a: number; b: number; bad?: boolean; label?: string } | { join: number };
interface Snapshot { cuts: number[] | null; transitions: Record<number, string>; styles: TimelineStyleRange[] }

/* ── тултипы: фиксированный слой, не режутся контейнерами, у края раскрываются вниз ── */
function useTooltips(root: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = document.createElement('div');
    el.className = 'fxt-tip';
    el.setAttribute('role', 'tooltip');
    root.current?.appendChild(el);
    let timer = 0; let target: Element | null = null; let hiddenAt = 0;
    const hide = () => { window.clearTimeout(timer); if (el.classList.contains('show')) hiddenAt = performance.now(); el.classList.remove('show'); target = null; };
    const show = (t: HTMLElement, instant: boolean) => {
      if (!t.isConnected) return;
      el.textContent = t.dataset.tip ?? '';
      el.classList.toggle('inst', instant);
      const z = zoomScale(); const b = t.getBoundingClientRect();
      const r = { left: b.left / z, top: b.top / z, bottom: b.bottom / z, width: b.width / z };
      const w = el.offsetWidth; const h = el.offsetHeight;
      let top = r.top - h - 8; let below = false;
      if (top < 6) { top = r.bottom + 8; below = true; }
      el.style.setProperty('--ty', below ? '-3px' : '3px');
      el.style.left = `${clamp(r.left + r.width / 2 - w / 2, 6, innerWidth / z - w - 6)}px`;
      el.style.top = `${top}px`;
      el.classList.add('show');
    };
    const over = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return;
      const t = (e.target as Element | null)?.closest<HTMLElement>('[data-tip]') ?? null;
      if (t === target) return;
      hide();
      if (!t) return;
      target = t;
      const warm = performance.now() - hiddenAt < 450;
      timer = window.setTimeout(() => show(t, warm), warm ? 0 : 380);
    };
    const node = root.current;
    node?.addEventListener('pointerover', over);
    node?.addEventListener('pointerdown', hide, true);
    return () => { node?.removeEventListener('pointerover', over); node?.removeEventListener('pointerdown', hide, true); el.remove(); };
  }, [root]);
}

/* ── библиотека (не зависит от времени — не перерисовывается каждый кадр) ── */
const Library = memo(function Library({ tab, setTab, hooksOn, open, setOpen, used, activeHookKind, onDemo, onAdd, onDragStart }: {
  tab: LibKind; setTab: (tab: LibKind) => void; hooksOn: boolean; open: Record<string, boolean>; setOpen: (kind: string) => void;
  used: (item: LibItem) => boolean; activeHookKind?: HookKind; onDemo: (item: LibItem) => void; onAdd: (item: LibItem) => void;
  onDragStart: (item: LibItem, e: ReactPointerEvent) => void;
}) {
  const effective = tab === 'hook' && !hooksOn ? 'trans' : tab;
  const row = (item: LibItem, disabled = false, meta?: string) => {
    const on = used(item);
    return (
      <div key={`${item.kind}:${item.label}`} className={`fxt-item${on ? ' on' : ''}${disabled ? ' off' : ''}`} tabIndex={0}
        onPointerDown={(e) => { if (!disabled && !(e.target as Element).closest('[data-act]')) onDragStart(item, e); }}
        onKeyDown={(e) => { if (e.key === 'Enter' && !disabled) onAdd(item); }}>
        <Ic label={item.label} kind={item.kind} on={on} />
        <span className="nm"><b>{item.label}</b><small>{meta ?? META[item.label] ?? ''}</small></span>
        {!disabled && (
          <span className="acts">
            {previewFor(item) && <button type="button" data-act className="fxt-mini" aria-label={`Показать «${item.label}» в превью`} onClick={() => onDemo(item)}><Glyph name="play" size={12} /></button>}
            <button type="button" data-act className="fxt-mini" aria-label={`Добавить «${item.label}» на таймлайн`} onClick={() => onAdd(item)}><Glyph name="plus" size={14} sw={1.8} /></button>
          </span>
        )}
      </div>
    );
  };
  return (
    <section className="fxt-panel fxt-lib" aria-label="Библиотека FX">
      <div className="fxt-lib-h">
        <h2><span className="tx">Библиотека</span></h2>
        <div className="fxt-tabs" role="tablist">
          {([['hook', 'Хуки', HOOK_CATS.reduce((n, c) => n + c.options.length, 0)], ['trans', 'Переходы', GLUES.length], ['style', 'Стили', STYLES.length]] as const).map(([id, label, count]) => {
            const off = id === 'hook' && !hooksOn;
            return (
              <button key={id} type="button" role="tab" aria-selected={effective === id} aria-disabled={off} data-tip={off ? 'Хуки в 16:9 пока не работают' : undefined} onClick={() => { if (!off) setTab(id); }}>
                {off && <Glyph name="lock" size={12} sw={2} />}<span className="tx">{label}</span><span className="c tx">{count}</span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="fxt-lib-b">
        {effective === 'hook' && HOOK_CATS.map((cat) => (
          <div key={cat.kind} className="fxt-acc" data-open={Boolean(open[cat.kind])}>
            <button type="button" className="fxt-acc-h" aria-expanded={Boolean(open[cat.kind])} onClick={() => setOpen(cat.kind)}>
              <span className={`fxt-ic k-hook sm${activeHookKind === cat.kind ? ' on' : ''}`}><Glyph name={cat.icon} size={14} /></span>
              <span className="name">{cat.label}</span><span className="c">{cat.options.length || ''}</span>
              {activeHookKind === cat.kind && <span className="was">выбран на шаге FX</span>}
              <span className="chev"><Glyph name="chev" size={16} /></span>
            </button>
            {open[cat.kind] && (
              <div className="fxt-grid">
                {cat.kind === 'warmup'
                  ? row({ kind: 'hook', label: 'Свой звук или видео', hookKind: 'warmup' }, true, 'загружается на шаге FX')
                  : cat.options.map((label) => row({ kind: 'hook', label, hookKind: cat.kind }))}
              </div>
            )}
          </div>
        ))}
        {effective === 'trans' && <div className="fxt-grid">{GLUES.map((label) => row({ kind: 'trans', label }))}</div>}
        {effective === 'style' && <div className="fxt-grid">{STYLES.map((label) => row({ kind: 'style', label }))}</div>}
      </div>
    </section>
  );
});

function previewFor(item: LibItem): string | undefined {
  if (item.kind === 'trans') return previewIdFor('effectGlue', item.label);
  if (item.kind === 'style') return previewIdFor('effectStyle', item.label);
  if (item.hookKind === 'object') return previewIdFor('object', item.label);
  if (item.hookKind === 'motion') return previewIdFor('motion', item.label);
  if (item.hookKind === 'effects') return previewIdFor('effectHook', item.label);
  return undefined;
}

export function FxTimeline({ onClose }: { onClose: () => void }) {
  const rootRef = useRef<HTMLDivElement>(null);
  useTooltips(rootRef);
  const track = useWizardStore((s) => s.track);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const background = useWizardStore((s) => s.background);
  const hooks = useWizardStore((s) => s.hooks);
  const setHooks = useWizardStore((s) => s.setHooks);
  const clearHook = useWizardStore((s) => s.clearHook);
  const timeline = useWizardStore((s) => s.timeline);
  const setTimeline = useWizardStore((s) => s.setTimeline);
  const asr = useWizardStore((s) => s.asr);
  const recipe = useRecipeCuts();

  /* ── форматы: только те, что есть среди исходников шага «Фон» ── */
  const formats = useMemo(() => {
    const out = new Set<string>();
    for (const vibe of background.footage) out.add(background.footageFormats?.[vibe] ?? (background.footageType === 'cine16x9' ? '16:9' : '9:16'));
    for (const plan of background.sourceVideos) out.add(plan.format);
    if (background.photo.length || background.color) out.add('9:16');
    if (!out.size) out.add('9:16');
    return out;
  }, [background]);
  const [format, setFormat] = useState<'9:16' | '16:9'>(() => (formats.has('9:16') ? '9:16' : '16:9'));
  const hooksOn = format === '9:16';

  /* ── время: всё в секундах от начала отрывка ── */
  const start = recipe.window?.start ?? 0;
  const dur = recipe.window ? recipe.window.end - recipe.window.start : 15;
  const drop = recipe.window?.drop != null ? recipe.window.drop - start : null;
  const bpm = recipe.data?.bpm ?? 0;
  const [dragCuts, setDragCuts] = useState<number[] | null>(null);
  const cuts = useMemo(() => dragCuts ?? (recipe.cuts ?? []).map((c) => c - start), [dragCuts, recipe.cuts, start]);
  const bounds = useMemo(() => [0, ...cuts, dur], [cuts, dur]);
  const shots = bounds.length - 1;
  const beats = useMemo(() => (recipe.data?.beats ?? []).map((b) => b - start), [recipe.data, start]);

  const kind = hooks.kind;
  const config = (kind && hooks.configs[kind]) || {};
  const activeHookLabel = hookLabel(kind, config);
  const defaultGlue = config.effectGlue ?? NO_GLUE;
  const transitionAt = (i: number) => timeline.transitions[i] ?? defaultGlue;
  const hookRange = kind && kind !== 'none' && drop !== null && activeHookLabel && hooksOn ? hookSpan(kind, config, drop, dur, bpm, bounds) : null;

  /* ── образец: реальные клипы первого вайба (итог по видео — на «Пуле») ── */
  const sampleVibe = background.footage[0];
  const samplePick = useQuery({
    queryKey: ['timeline-sample', sampleVibe, timingFrom, timingTo, recipe.cuts?.join(',')],
    queryFn: () => api.storyboardPick({ clipFrom: timingFrom, clipTo: timingTo, cuts: recipe.cuts ?? [], videos: [{ index: 1, group: String(sampleVibe), seedKey: `timeline-sample:${track?.id ?? ''}` }] }),
    enabled: Boolean(sampleVibe && recipe.cuts && !dragCuts),
    staleTime: Infinity,
    retry: 1
  });
  const sampleClips = samplePick.data?.videos[0]?.clips ?? [];

  /* ── субтитры: слова примерки, сгруппированные в короткие фразы ── */
  // Дорожка субтитров — ровно те же слова и тайминги, что на шаге «Текст» (с правками
  // и фокус-словами), по пилюле на слово. Своих группировок нет: как слова соберутся в
  // строки, решает выбранный стиль субтитров уже в рендере.
  const subs = useMemo(() => {
    const words = asr.status === 'COMPLETED' ? asr.words : [];
    return words
      .map((w) => ({ a: w.tStart - start, b: w.tEnd - start, text: w.text, focus: Boolean(w.focus) }))
      .filter((s) => s.b > 0 && s.a < dur);
  }, [asr, start, dur]);

  /* ── состояние экрана ── */
  const [tab, setTab] = useState<LibKind>('hook');
  const [open, setOpenState] = useState<Record<string, boolean>>(() => (kind && kind !== 'none' ? { [kind]: true } : { effects: true }));
  const [sel, setSel] = useState<Sel>(null);
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [snap, setSnap] = useState(true);
  const [vis, setVis] = useState({ hook: true, s0: true, s1: true, subs: true });
  const [demo, setDemo] = useState<{ previewId: string; label: string } | null>(null);
  const [pop, setPop] = useState<{ i: number; x: number; y: number } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [keysOpen, setKeysOpen] = useState(false);

  /* ── подсказки: библиотека → склейки → частота. Информационные (действия не ждут),
        поэтому без idle-реактивации; показ отмечается по факту. Склейки и частота — только
        когда склейки посчитаны, иначе указывать не на что. ── */
  const { t: tr } = useTranslation();
  const libGuideRef = useRef<HTMLElement | null>(null);
  const cutsGuideRef = useRef<HTMLDivElement | null>(null);
  const paceGuideRef = useRef<HTMLDivElement>(null);
  const [paceGuideDismissed, setPaceGuideDismissed] = useGuideDismiss('timeline-pace', false);
  const [cutsGuideDismissed, setCutsGuideDismissed] = useGuideDismiss('timeline-cuts', false);
  const [libGuideDismissed, setLibGuideDismissed] = useGuideDismiss('timeline-library', false);
  const cutsReady = !recipe.loading && !recipe.error && shots > 0;
  const showLibGuide = !libGuideDismissed;
  const showCutsGuide = libGuideDismissed && !cutsGuideDismissed && cutsReady;
  const showPaceGuide = libGuideDismissed && cutsGuideDismissed && !paceGuideDismissed && cutsReady;
  useMarkGuideSeen('timeline-library', showLibGuide);
  useMarkGuideSeen('timeline-cuts', showCutsGuide);
  useMarkGuideSeen('timeline-pace', showPaceGuide);
  // Esc закрывает подсказку (её собственный обработчик) — таймлайн при этом закрываться не должен.
  const guideOpenRef = useRef(false);
  guideOpenRef.current = showLibGuide || showCutsGuide || showPaceGuide;
  useLayoutEffect(() => { libGuideRef.current = rootRef.current?.querySelector('.fxt-lib') ?? null; });
  const toastTimer = useRef(0);
  const say = useCallback((msg: string) => { setToast(msg); window.clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), 2400); }, []);
  // Во время игры время ведёт звук (тик читает audio.currentTime) — перемотка двигает
  // и его, иначе клик по кадру/линейке откатывался следующим же тиком.
  const seek = useCallback((v: number) => {
    const x = clamp(v, 0, dur - 1 / FPS); tRef.current = x; setT(x);
    const audio = audioRef.current;
    if (audio) { try { audio.currentTime = start + x; } catch { /* ещё не загрузился */ } }
  }, [dur, start]);

  /* ── история рецепта ── */
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const [, bump] = useState(0);
  const snapshot = (): Snapshot => ({ cuts: timeline.cuts ? [...timeline.cuts] : null, transitions: { ...timeline.transitions }, styles: timeline.styles.map((s) => ({ ...s })) });
  const remember = () => { past.current.push(snapshot()); if (past.current.length > 80) past.current.shift(); future.current = []; bump((n) => n + 1); };
  const undo = () => { const prev = past.current.pop(); if (!prev) return; future.current.push(snapshot()); setTimeline({ ...prev, edited: true }); setSel(null); bump((n) => n + 1); };
  const redo = () => { const next = future.current.pop(); if (!next) return; past.current.push(snapshot()); setTimeline({ ...next, edited: true }); setSel(null); bump((n) => n + 1); };

  /* ── звук отрывка: превью играет под настоящую музыку ── */
  const audioUrl = usePlaybackUrl(track);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  useEffect(() => {
    if (!audioUrl) return undefined;
    const audio = new Audio(audioUrl);
    audio.preload = 'auto';
    audioRef.current = audio;
    return () => { audio.pause(); audioRef.current = null; };
  }, [audioUrl]);
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playing) { audio.currentTime = start + tRef.current; void audio.play().catch(() => undefined); } else audio.pause();
  }, [playing, start]);

  /* ── транспорт ── */
  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0; let last = 0;
    const tick = (ts: number) => {
      const audio = audioRef.current;
      let next: number;
      if (audio && !audio.paused && audio.readyState >= 2) next = audio.currentTime - start;
      else { const dt = last ? (ts - last) / 1000 : 0; next = tRef.current + dt; }
      last = ts;
      if (next >= dur) { next = 0; if (audio) audio.currentTime = start; }
      tRef.current = next;
      setT(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, dur, start]);

  /* ── геометрия: превью = рамка видео; таймлайн по ширине ── */
  const mainRef = useRef<HTMLElement>(null);
  const tlRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ w: 360, h: 640 });
  const [scrollW, setScrollW] = useState(800);
  useLayoutEffect(() => {
    const main = mainRef.current; const tl = tlRef.current; const sc = scrollRef.current;
    if (!main || !tl || !sc) return undefined;
    const fit = () => {
      const H = main.clientHeight - 28;
      if (window.innerWidth <= 1023) { const w = Math.min(main.clientWidth - 32, format === '9:16' ? 320 : 640); setStageSize({ w, h: format === '9:16' ? w * 16 / 9 : w * 9 / 16 }); }
      else if (format === '9:16') setStageSize({ w: Math.round(H * 9 / 16), h: H });
      else { const h = H - tl.offsetHeight - 12; let w = Math.round(h * 16 / 9); const maxW = main.clientWidth * 0.56; if (w > maxW) w = maxW; setStageSize({ w, h: Math.round(w * 9 / 16) }); }
      setScrollW(sc.clientWidth);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(main); ro.observe(tl);
    return () => ro.disconnect();
  }, [format]);
  const pps = Math.max(10, (scrollW - X0 * 2) / dur) * zoom;
  const tx = (s: number) => X0 + s * pps;
  const xt = (x: number) => (x - X0) / pps;
  const canvasW = tx(dur) + X0;
  const frameAt = (v: number) => { let f = 0; while (f < shots - 1 && v >= bounds[f + 1]) f++; return f; };
  const fNow = frameAt(t);

  /* ── стили на дорожках ── */
  const styles = timeline.styles;
  const styleFree = (lane: 0 | 1, a: number, b: number, except?: number) => !styles.some((o) => o.uid !== except && o.lane === lane && a < o.b && b > o.a);
  const setStyles = (next: TimelineStyleRange[]) => setTimeline({ styles: next });
  const ensureStyleInConfig = (label: string) => {
    if (!kind) return;
    const current = config.effectStyles?.length ? config.effectStyles : (config.effectStyle ? [config.effectStyle] : []);
    if (!current.includes(label)) setHooks({ config: { effectStyles: [...current, label], effectStyle: label } });
  };
  const addStyle = (label: string, frame: number, lanePref: 0 | 1 = 0) => {
    // Стили — часть настройки хука (или «Без хука»): без неё стилю негде жить, и
    // на дорожке он висел бы, не попадая в рендер.
    if (!kind) { say('Сначала выбери хук или «Без хука» на шаге FX — стили живут в его настройке'); return; }
    for (const lane of [lanePref, (1 - lanePref) as 0 | 1]) {
      if (styleFree(lane, frame, frame + 1)) {
        remember();
        const uid = Math.max(0, ...styles.map((s) => s.uid)) + 1;
        setStyles([...styles, { uid, style: label, lane, a: frame, b: frame + 1 }]);
        ensureStyleInConfig(label);
        setSel({ type: 'style', uid });
        say(`${label} — кадр ${frame + 1}. Тяни края, чтобы растянуть`);
        return;
      }
    }
    say('На этом кадре уже два стиля — это максимум');
  };
  const setTransition = (i: number, label: string) => { remember(); setTimeline({ transitions: { ...timeline.transitions, [i]: label } }); };
  const setTransitionAll = (label: string) => {
    remember();
    setTimeline({ transitions: Object.fromEntries(cuts.map((_, i) => [i, label])) });
    if (kind) setHooks({ config: { effectGlue: label } });
    say(`${label} — на всех склейках`);
  };
  const addHook = (item: LibItem) => {
    const cat = HOOK_CATS.find((c) => c.kind === item.hookKind);
    if (!cat?.key || !hooksOn) return;
    if (drop === null) { say('Сначала выбери дроп на шаге FX — хук встаёт на него'); return; }
    const prev = activeHookLabel && activeHookLabel !== item.label ? activeHookLabel : null;
    const sameKind = kind === cat.kind;
    // Переходы и стили рецепта переезжают к новому хуку: без них он неполный и не
    // попал бы в пул. Хук другого типа не стирается — на шаге FX их может быть
    // несколько (у каждого своя доля роликов), поэтому говорим об этом прямо.
    const carry: Partial<HookConfig> = sameKind ? {} : { effectGlue: config.effectGlue, effectStyle: config.effectStyle, effectStyles: config.effectStyles };
    setHooks({ kind: cat.kind, config: { ...carry, [cat.key]: item.label } as Partial<HookConfig> });
    setSel({ type: 'hook' });
    say(!prev ? `${item.label} встал на дроп ${tc(drop)}`
      : sameKind ? `Хук заменён: ${prev} → ${item.label}`
        : `${item.label} встал на дроп. «${prev}» остаётся в пуле — снять его можно на шаге FX`);
  };
  const addFromLib = useCallback((item: LibItem) => {
    if (item.kind === 'hook') return addHook(item);
    if (item.kind === 'style') return addStyle(item.label, sel?.type === 'frame' ? sel.i : frameAt(tRef.current));
    if (sel?.type === 'cut') { setTransition(sel.i, item.label); say(`Склейка ${sel.i + 1}: ${item.label}`); } else setTransitionAll(item.label);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, styles, timeline.transitions, cuts, kind, config, drop, hooksOn]);
  const del = () => {
    if (!sel) return;
    if (sel.type === 'hook' && kind && kind !== 'none') {
      // Снять = убрать хук из пула совсем (не только из вида таймлайна). Переходы и
      // стили рецепта остаются — ролик становится «Без хука».
      const none = hooks.configs.none ?? {};
      const keepStyles = [...new Set([...(none.effectStyles ?? []), ...(config.effectStyles ?? (config.effectStyle ? [config.effectStyle] : []))])];
      clearHook(kind);
      setHooks({ kind: 'none', config: { effectGlue: none.effectGlue ?? config.effectGlue, effectStyles: keepStyles, effectStyle: keepStyles[keepStyles.length - 1] } });
      say('Хук снят — переходы и стили остались, ролик идёт «Без хука»');
    }
    else if (sel.type === 'style') { remember(); const next = styles.filter((s) => s.uid !== sel.uid); dropOrphanStyleLabels(styles, next); setStyles(next); }
    else if (sel.type === 'cut') setTransition(sel.i, NO_GLUE);
    else return;
    setSel(null);
  };

  /* ── drag: склейки, стили, библиотека ── */
  const cvRef = useRef<HTMLDivElement>(null);
  const laneRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const drag = useRef<null | { kind: 'scrub' } | { kind: 'cut'; i: number; cuts: number[] } | { kind: 'sedge'; uid: number; side: 'l' | 'r' } | { kind: 'smove'; uid: number; grab: number } | { kind: 'hookbody'; x0: number; warned: boolean } | { kind: 'hookedge' } | { kind: 'lib'; item: LibItem; x0: number; y0: number; live: boolean }>(null);
  const [ghost, setGhost] = useState<{ item: LibItem; x: number; y: number } | null>(null);
  const [place, setPlace] = useState<PlaceTarget | null>(null);
  const [snapLine, setSnapLine] = useState<number | null>(null);
  const cvX = (clientX: number) => (clientX - (cvRef.current?.getBoundingClientRect().left ?? 0)) / zoomScale();
  const laneAt = (x: number, y: number) => {
    for (const [name, el] of Object.entries(laneRefs.current)) { const r = el?.getBoundingClientRect(); if (r && y >= r.top && y < r.bottom && x >= r.left && x <= r.right) return name; }
    const sr = scrollRef.current?.getBoundingClientRect();
    return sr && y >= sr.top - 30 && y < sr.bottom && x >= sr.left && x <= sr.right ? 'timeline' : null;
  };
  const snapT = (v: number) => {
    if (!snap) return { v, snapped: false };
    const targets = [...(drop !== null ? [drop] : []), tRef.current, ...beats];
    let best: { s: number; d: number } | null = null;
    for (const s of targets) { const d = Math.abs(tx(s) - tx(v)); if (d < 8 && (!best || d < best.d)) best = { s, d }; }
    return best ? { v: best.s, snapped: true } : { v, snapped: false };
  };
  const libTarget = (item: LibItem, x: number, y: number): PlaceTarget | null => {
    const ln = laneAt(x, y); if (!ln) return null;
    const v = clamp(xt(cvX(x)), 0, dur - 0.001);
    if (item.kind === 'hook') { if (!hooksOn || drop === null) return null; const cat = HOOK_CATS.find((c) => c.kind === item.hookKind); if (!cat?.key) return null; const span = hookSpan(cat.kind, { ...config, [cat.key]: item.label }, drop, dur, bpm, bounds); return span ? { lane: 'hook', a: span[0], b: span[1], label: 'на дроп' } : null; }
    if (item.kind === 'style') { const f = frameAt(v); const pref: 0 | 1 = ln === 's1' ? 1 : 0; for (const L of [pref, (1 - pref) as 0 | 1]) if (styleFree(L, f, f + 1)) return { lane: `s${L}`, a: bounds[f], b: bounds[f + 1] }; return { lane: `s${pref}`, a: bounds[f], b: bounds[f + 1], bad: true, label: 'Уже два стиля' }; }
    if (!cuts.length) return null;
    let k = 0; cuts.forEach((c, i) => { if (Math.abs(c - v) < Math.abs(cuts[k] - v)) k = i; });
    return { join: k };
  };
  const onLibDragStart = useCallback((item: LibItem, e: ReactPointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { kind: 'lib', item, x0: e.clientX, y0: e.clientY, live: false };
  }, []);

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = drag.current; if (!d) return;
      if (d.kind === 'scrub') { seek(xt(cvX(e.clientX))); return; }
      if (d.kind === 'cut') {
        const b = [0, ...d.cuts, dur]; const lo = b[d.i] + 0.4; const hi = b[d.i + 2] - 0.4;
        const r = snapT(xt(cvX(e.clientX))); const v = clamp(r.v, lo, hi);
        const next = [...d.cuts]; next[d.i] = Math.round(v * 1000) / 1000; d.cuts = next; setDragCuts(next);
        setSnapLine(r.snapped && v > lo && v < hi ? v : null); return;
      }
      if (d.kind === 'hookedge') {
        // Слоу-шаттер тянется только до длин, которые умеет рендер, — прилипаем к ближайшей.
        if (drop === null) return;
        const v = xt(cvX(e.clientX));
        let best: SlowExtend = '';
        for (const [opt] of SLOW_EXTENDS) if (Math.abs(slowShutterEnd(opt, drop, dur, bounds) - v) < Math.abs(slowShutterEnd(best, drop, dur, bounds) - v)) best = opt;
        if (best !== (config.effectHookExtend ?? '')) setHooks({ config: { effectHookExtend: best } });
        return;
      }
      if (d.kind === 'hookbody') { if (!d.warned && Math.abs(e.clientX - d.x0) > 6) { d.warned = true; say('Хук привязан к дропу — сдвинуть его нельзя. Дроп выбирается на шаге FX'); } return; }
      if (d.kind === 'sedge') {
        const s = styles.find((x) => x.uid === d.uid); if (!s) return;
        const v = xt(cvX(e.clientX)); let k = 0; bounds.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(bounds[k] - v)) k = i; });
        if (d.side === 'l') { const a = clamp(k, 0, s.b - 1); if (a !== s.a && styleFree(s.lane, a, s.b, s.uid)) setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a } : x)); }
        else { const bb = clamp(k, s.a + 1, shots); if (bb !== s.b && styleFree(s.lane, s.a, bb, s.uid)) setStyles(styles.map((x) => x.uid === s.uid ? { ...x, b: bb } : x)); }
        return;
      }
      if (d.kind === 'smove') {
        const s = styles.find((x) => x.uid === d.uid); if (!s) return;
        const span = s.b - s.a; const a = clamp(frameAt(xt(cvX(e.clientX))) - d.grab, 0, shots - span);
        const ln = laneAt(e.clientX, e.clientY); const lane: 0 | 1 = ln === 's1' ? 1 : ln === 's0' ? 0 : s.lane;
        if ((a !== s.a || lane !== s.lane) && styleFree(lane, a, a + span, s.uid)) setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a, b: a + span, lane } : x));
        return;
      }
      if (d.kind === 'lib') {
        if (!d.live) { if (Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < 5) return; d.live = true; document.body.style.cursor = 'grabbing'; }
        setGhost({ item: d.item, x: e.clientX / zoomScale(), y: e.clientY / zoomScale() });
        setPlace(libTarget(d.item, e.clientX, e.clientY));
      }
    };
    const up = (e: PointerEvent) => {
      const d = drag.current; drag.current = null; setSnapLine(null);
      if (!d) return;
      if (d.kind === 'cut') { setDragCuts(null); setTimeline({ cuts: d.cuts.map((c) => c + start), edited: true }); return; }
      if (d.kind === 'hookedge') { document.body.style.cursor = ''; say(`Слоу-шаттер: ${SLOW_EXTENDS.find(([o]) => o === (config.effectHookExtend ?? ''))?.[1].toLowerCase()}`); return; }
      if (d.kind === 'lib') {
        document.body.style.cursor = ''; setGhost(null); setPlace(null);
        if (!d.live) return;
        const target = libTarget(d.item, e.clientX, e.clientY); if (!target) return;
        if ('join' in target) { setTransition(target.join, d.item.label); setSel({ type: 'cut', i: target.join }); say(`Склейка ${target.join + 1}: ${d.item.label}`); return; }
        if (target.bad) return;
        if (d.item.kind === 'hook') addHook(d.item);
        else addStyle(d.item.label, frameAt(target.a + 1e-3), target.lane === 's1' ? 1 : 0);
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  });

  const onCanvasDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('.fxt-join')) return;
    setPop(null);
    if (target.closest('.fxt-ruler')) { setDemo(null); drag.current = { kind: 'scrub' }; seek(xt(cvX(e.clientX))); return; }
    const edge = target.closest<HTMLElement>('.fxt-edge');
    const st = target.closest<HTMLElement>('.fxt-clip.st');
    if (edge?.dataset.cut) { remember(); drag.current = { kind: 'cut', i: Number(edge.dataset.cut), cuts: [...cuts] }; e.preventDefault(); return; }
    if (st) {
      const uid = Number(st.dataset.uid); setSel({ type: 'style', uid }); remember();
      const s = styles.find((x) => x.uid === uid);
      drag.current = edge ? { kind: 'sedge', uid, side: edge.dataset.side === 'l' ? 'l' : 'r' } : { kind: 'smove', uid, grab: frameAt(xt(cvX(e.clientX))) - (s?.a ?? 0) };
      e.preventDefault(); return;
    }
    if (target.closest('[data-hookedge]')) { setSel({ type: 'hook' }); drag.current = { kind: 'hookedge' }; document.body.style.cursor = 'ew-resize'; e.preventDefault(); return; }
    if (target.closest('.fxt-clip.hk')) { setSel({ type: 'hook' }); drag.current = { kind: 'hookbody', x0: e.clientX, warned: false }; return; }
    const sb = target.closest<HTMLElement>('.fxt-clip.sb');
    if (sb) { const i = Number(sb.dataset.sub); setSel({ type: 'sub', i }); seek(subs[i].a + 0.02); return; }
    const fr = target.closest<HTMLElement>('.fxt-clip.fr');
    if (fr) { const i = Number(fr.dataset.frame); setSel({ type: 'frame', i }); if (tRef.current < bounds[i] || tRef.current >= bounds[i + 1]) seek(bounds[i] + 0.001); setDemo(null); return; }
    setSel(null);
  };

  /* ── клавиатура ── */
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).matches?.('input, textarea')) return;
      if (e.code === 'Space') { e.preventDefault(); if (document.activeElement instanceof HTMLButtonElement) document.activeElement.blur(); setDemo(null); setPlaying((p) => !p); return; }
      if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if ((e.ctrlKey || e.metaKey) && e.code === 'KeyY') { e.preventDefault(); redo(); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { del(); return; }
      if (e.key === 'Escape') { if (guideOpenRef.current) return; if (pop) setPop(null); else if (sel) setSel(null); else onClose(); return; }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); seek(tRef.current + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 1 : 1 / FPS)); return; }
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); const next = e.key === 'ArrowDown' ? bounds.find((x) => x > tRef.current + 0.01) : [...bounds].reverse().find((x) => x < tRef.current - 0.01); if (next !== undefined) seek(next); }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });

  /* ── превью: клипы образца по склейкам ── */
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const since = t - bounds[fNow];
  const trLabel = fNow > 0 ? transitionAt(fNow - 1) : NO_GLUE;
  const trAnim = TRANSITION_ANIM[trLabel];
  const inTr = Boolean(trAnim) && since < TD;
  const p = inTr ? since / TD : 1;
  useEffect(() => {
    videoRefs.current.forEach((video, i) => {
      if (!video) return;
      const clip = sampleClips[i];
      const visible = i === fNow || (inTr && i === fNow - 1);
      if (!visible || !clip) { if (!video.paused) video.pause(); return; }
      const want = clip.previewOffset + (i === fNow ? t - bounds[i] : bounds[i + 1] - bounds[i]);
      if (!playing || Math.abs(video.currentTime - want) > 0.25) { try { video.currentTime = Math.max(0, want); } catch { /* метаданные ещё не пришли */ } }
      if (playing && video.paused) void video.play().catch(() => undefined);
      if (!playing && !video.paused) video.pause();
    });
  });
  const activeStyles = styles.filter((s) => vis[s.lane ? 's1' : 's0'] && t >= bounds[s.a] && t < bounds[s.b]).map((s) => s.style);
  const inHook = hookRange && vis.hook && t >= hookRange[0] && t < hookRange[1];
  const flash = inHook && kind === 'effects' && activeHookLabel === 'Молния' ? Math.max(0, 0.8 * (t - hookRange[0] < 0.06 ? (t - hookRange[0]) / 0.06 : 1 - (t - hookRange[0] - 0.06) / 0.22)) : 0;
  const trFlash = inTr && trAnim === 'flash' ? (p < 0.25 ? p / 0.25 * 0.95 : Math.max(0, 0.95 * (1 - (p - 0.25) / 0.6))) : 0;
  const shotStyle = (i: number): React.CSSProperties => {
    if (!(inTr && i === fNow)) return {};
    const e = eOut(p);
    if (trAnim === 'snap') return { clipPath: `inset(0 ${(100 - 100 * eIO(p)).toFixed(2)}% 0 0)` };
    if (trAnim === 'minimax') return { opacity: e, transform: `scale(${1.35 - 0.32 * e})` };
    if (trAnim === 'extract') return { transform: `translateY(${((1 - e) * 100).toFixed(2)}%) scaleY(${1 + 0.3 * (1 - e)})` };
    if (trAnim === 'invert') return { filter: `invert(${p < 0.45 ? 1 : 1 - (p - 0.45) / 0.55})` };
    if (trAnim === 'flash') return { opacity: p > 0.25 ? 1 : 0 };
    return {};
  };

  /* ── подписи выделения ── */
  const selInfo = () => {
    if (!sel) return <span className="muted">Выбери кадр, склейку или эффект на дорожке</span>;
    if (sel.type === 'frame') return <><Ic kind="frame" label="frames" size={24} /><span className="nm">Кадр {pad(sel.i + 1)}</span><span className="meta num">{tc(bounds[sel.i])} → {tc(bounds[sel.i + 1])} · {secs(bounds[sel.i + 1] - bounds[sel.i])}</span></>;
    if (sel.type === 'cut') { const label = transitionAt(sel.i); return <><Ic kind="trans" label={label} size={24} /><span className="nm">Склейка {sel.i + 1} → {sel.i + 2}</span><span className="meta">{label}</span><button type="button" className="fxt-pill" onClick={() => setTransitionAll(label)}><span className="tx">Ко всем склейкам</span></button></>; }
    if (sel.type === 'hook' && hookRange && activeHookLabel) return <><Ic kind="hook" label={activeHookLabel} size={24} /><span className="nm">{activeHookLabel}</span><span className="meta num">{hookRange[1] <= (drop ?? 0) + 1e-3 ? 'заканчивается на дропе' : 'стартует с дропа'} · {secs(hookRange[1] - hookRange[0])}</span>{kind === 'effects' && config.effectHook === 'Слоу-шаттер' && <div className="fxt-seg" role="group" aria-label="Длина слоу-шаттера">{SLOW_EXTENDS.map(([opt, lab]) => <button key={opt || 'std'} type="button" aria-pressed={(config.effectHookExtend ?? '') === opt} onClick={() => setHooks({ config: { effectHookExtend: opt } })}><span className="tx">{lab}</span></button>)}</div>}<button type="button" className="fxt-icon" aria-label="Снять хук" data-tip="Снять хук · Delete" onClick={del}><Glyph name="trash" size={17} /></button></>;
    if (sel.type === 'style') { const s = styles.find((x) => x.uid === sel.uid); if (!s) return null; return <><Ic kind="style" label={s.style} size={24} /><span className="nm">{s.style}</span><span className="meta num">{s.b - s.a > 1 ? `кадры ${s.a + 1}–${s.b}` : `кадр ${s.a + 1}`} · {secs(bounds[s.b] - bounds[s.a])}</span><button type="button" className="fxt-pill" onClick={() => { if (styleFree(s.lane, 0, shots, s.uid)) { remember(); setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a: 0, b: shots } : x)); } else say('На этой дорожке мешает другой стиль — перенеси его на «Стиль 2»'); }}><span className="tx">На весь отрывок</span></button><button type="button" className="fxt-icon" aria-label="Удалить" data-tip="Удалить · Delete" onClick={del}><Glyph name="trash" size={17} /></button></>; }
    if (sel.type === 'sub') { const s = subs[sel.i]; return s ? <><Ic kind="trans" label="thought" size={24} /><span className="nm">«{s.text}»</span><span className="meta num">{tc(s.a)} → {tc(s.b)} · тайминг и стиль — на шаге «Текст»</span></> : null; }
    return null;
  };

  const paceGlyph = (k: number) => { const n = [3, 5, 7][k]; let d = ''; for (let j = 1; j < n; j++) d += `M${(2 + j * 20 / n).toFixed(1)} 8v8`; return `<rect x="2" y="7" width="20" height="10" rx="2.5"/><path d="${d}"/>`; };
  const paceTip = (pace: TimelinePace) => { const n = recipe.data?.cuts[pace].length; const label = { sparse: 'Реже', auto: 'Авто — как посчитал рендер по темпу', dense: 'Чаще' }[pace]; return n !== undefined ? `${label} · ${n + 1} ${kadr(n + 1)}` : label; };
  const used = useCallback((item: LibItem) => item.kind === 'hook' ? activeHookLabel === item.label : item.kind === 'style' ? styles.some((s) => s.style === item.label) : Object.values(timeline.transitions).includes(item.label) || (defaultGlue === item.label && item.label !== NO_GLUE), [activeHookLabel, styles, timeline.transitions, defaultGlue]);
  const toggleOpen = useCallback((k: string) => setOpenState((o) => ({ ...o, [k]: !o[k] })), []);
  const onDemo = useCallback((item: LibItem) => { const id = previewFor(item); if (id) { setPlaying(false); setDemo({ previewId: id, label: item.label }); } }, []);

  /* ── линейка ── */
  const step = pps > 90 ? 0.25 : pps > 40 ? 0.5 : 1;
  const lab = pps > 70 ? 1 : pps > 30 ? 2 : 5;
  const ticks: { v: number; maj: boolean }[] = [];
  for (let v = 0; v <= dur + 1e-6; v += step) ticks.push({ v, maj: Math.abs(v / lab - Math.round(v / lab)) < 1e-6 });
  const windowLabel = `${timingFrom} – ${timingTo}`;

  const laneHead = (cls: string, icon: string, k: 'frame' | 'hook' | 'style' | 'trans', name: string, visKey?: keyof typeof vis) => (
    <div className={`fxt-lh ${cls}${visKey === 'hook' && !hooksOn ? ' dis' : ''}`}>
      <span className={`fxt-ic k-${k} sm`}><Glyph name={icon} size={13} /></span>
      <span className="nm">{name}</span>
      {visKey === 'hook' && !hooksOn
        ? <span className="eye lk" data-tip="Хуки в 16:9 пока не работают"><Glyph name="lock" size={14} /></span>
        : visKey && <button type="button" className="eye" aria-pressed={vis[visKey]} aria-label="Показывать в превью" onClick={() => setVis((v) => ({ ...v, [visKey]: !v[visKey] }))}><Glyph name={vis[visKey] ? 'eye' : 'eyeoff'} size={15} /></button>}
    </div>
  );

  return (
    <div ref={rootRef} className="fxt" data-format={format} role="dialog" aria-label="Таймлайн FX">
      <header className="fxt-top">
        <button type="button" className="fxt-back" onClick={onClose}><Glyph name="back" size={18} /><span className="tx">FX</span></button>
        <div className="fxt-proj"><b className="tx">{track?.filename ?? 'Трек'}</b><span className="tx num">{windowLabel}</span></div>
        <div className="fxt-spacer" />
        <div className="fxt-seg" role="group" aria-label="Формат превью">
          {(['9:16', '16:9'] as const).map((f) => {
            const ok = formats.has(f);
            const tip = !ok ? (f === '16:9' ? 'Нет исходников 16:9 — они выбираются на шаге «Фон»' : 'Нет вертикальных исходников — они выбираются на шаге «Фон»') : f === '16:9' ? 'Превью 16:9 — хуки в этом формате пока не работают' : 'Превью 9:16';
            return (
              <button key={f} type="button" aria-pressed={format === f} aria-disabled={!ok} data-tip={tip} onClick={() => { if (!ok) { say(tip); return; } setFormat(f); setSel(null); setDemo(null); }}>
                <i className="fxt-ratio" style={f === '9:16' ? { width: 9, height: 15 } : { width: 16, height: 9 }} /><span className="tx">{f}</span>
              </button>
            );
          })}
        </div>
        <div className="fxt-spacer" />
        <button type="button" className="fxt-icon" aria-label="Горячие клавиши" data-tip="Горячие клавиши" onClick={() => setKeysOpen((v) => !v)}><Glyph name="keys" size={20} /></button>
        <button type="button" className="fxt-primary" onClick={onClose}><span className="tx">Готово</span></button>
      </header>

      <main ref={mainRef} className="fxt-main">
        <Library tab={tab} setTab={setTab} hooksOn={hooksOn} open={open} setOpen={toggleOpen} used={used} activeHookKind={kind} onDemo={onDemo} onAdd={addFromLib} onDragStart={onLibDragStart} />

        <section className="fxt-panel fxt-pv" aria-label="Превью">
          <div className="fxt-stage" style={{ width: stageSize.w, height: stageSize.h }}>
            <div className="fxt-fx" style={{ filter: styleFilter(activeStyles, t) }}>
              {Array.from({ length: shots }, (_, i) => {
                const clip = sampleClips[i];
                const visible = i === fNow || (inTr && i === fNow - 1);
                return clip?.previewUrl
                  ? <video key={`${clip.fileName}:${i}`} ref={(el) => { videoRefs.current[i] = el; }} className={`shot${visible ? ' on' : ''}`} src={clip.previewUrl} muted playsInline preload="auto" style={{ zIndex: i === fNow ? 2 : 1, ...shotStyle(i) }} />
                  : visible && <div key={`ph${i}`} className="fxt-ph" style={{ zIndex: i === fNow ? 2 : 1, background: `linear-gradient(145deg, hsl(${(i * 53 + 260) % 360} 45% 22%), hsl(${(i * 53 + 305) % 360} 35% 10%))`, ...shotStyle(i) }}><span>КАДР {pad(i + 1)}</span></div>;
              })}
            </div>
            <div className="fxt-ov fxt-flash" style={{ opacity: flash }} />
            <div className="fxt-ov fxt-trflash" style={{ opacity: trFlash }} />
            {demo && <div className="fxt-demo"><EffectPreview previewId={demo.previewId} /></div>}
            <div className="fxt-chip"><Glyph name="film" size={12} /><span className="tx">{sampleVibe ? `Пример кадра · вайб «${sampleVibe}»` : 'Кадры — условные примеры'}</span></div>
            {demo && <div className="fxt-chip demo"><span className="tx">Пример · {demo.label}</span></div>}
            <button type="button" className="fxt-play" aria-label={playing ? 'Пауза' : 'Воспроизвести'} aria-pressed={playing} onClick={() => { setDemo(null); setPlaying((v) => !v); }}>
              {playing ? <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 4h3v12H6zM11 4h3v12h-3z" fill="currentColor" /></svg> : <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M6 3.5v13l11-6.5L6 3.5Z" fill="currentColor" /></svg>}
            </button>
          </div>
        </section>

        <section ref={tlRef} className="fxt-panel fxt-tl" aria-label="Таймлайн">
          <div className="fxt-bar">
            <button type="button" className="fxt-icon" aria-label="Отменить" data-tip="Отменить · Ctrl+Z" disabled={!past.current.length} onClick={undo}><Glyph name="undo" size={18} /></button>
            <button type="button" className="fxt-icon" aria-label="Вернуть" data-tip="Вернуть · Ctrl+Shift+Z" disabled={!future.current.length} onClick={redo}><Glyph name="redo" size={18} /></button>
            <span className="fxt-sep" />
            <div className="fxt-sel">{selInfo()}</div>
            <div ref={paceGuideRef} className="fxt-pace">
              <span className="lbl">Склейки</span>
              <div className="fxt-seg" role="group" aria-label="Частота склеек">
                {PACES.map((pace, k) => (
                  <button key={pace} type="button" aria-pressed={recipe.pace === pace} aria-label={paceTip(pace)} data-tip={paceTip(pace)} onClick={() => { if (recipe.pace !== pace) { remember(); recipe.setPace(pace); setSel(null); } }}>
                    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: paceGlyph(k) }} />
                  </button>
                ))}
              </div>
            </div>
            <span className="fxt-sep" />
            <button type="button" className="fxt-icon" aria-label="Прилипание" aria-pressed={snap} data-tip="Прилипание к битам и дропу" onClick={() => setSnap((v) => !v)}><Glyph name="magnet" size={18} /></button>
            <div className="fxt-zoom">
              <button type="button" className="fxt-icon" aria-label="Уменьшить масштаб" onClick={() => setZoom((z) => clamp(z / 1.4, 1, 6))}><Glyph name="minus" size={16} /></button>
              <input type="range" min={1} max={6} step={0.05} value={zoom} aria-label="Масштаб таймлайна" onChange={(e) => setZoom(Number(e.target.value))} />
              <button type="button" className="fxt-icon" aria-label="Увеличить масштаб" onClick={() => setZoom((z) => clamp(z * 1.4, 1, 6))}><Glyph name="plus" size={16} /></button>
            </div>
          </div>
          <div className="fxt-body">
            <div className="fxt-heads">
              <div className="rh" />
              {laneHead('h-frames', 'frames', 'frame', 'Кадры')}
              {laneHead('h-hook', 'effects', 'hook', 'Хук', 'hook')}
              {laneHead('h-s0', 'crystal', 'style', 'Стиль 1', 's0')}
              {laneHead('h-s1', 'crystal', 'style', 'Стиль 2', 's1')}
              {laneHead('h-subs', 'thought', 'trans', 'Субтитры', 'subs')}
              {laneHead('h-audio', 'audio', 'trans', 'Биты')}
            </div>
            <div ref={scrollRef} className="fxt-scroll" onWheel={(e) => { if (e.ctrlKey || e.metaKey) { setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 1, 6)); } else if (zoom > 1 && Math.abs(e.deltaY) > Math.abs(e.deltaX) && scrollRef.current) scrollRef.current.scrollLeft += e.deltaY; }}>
              <div ref={cvRef} className="fxt-cv" style={{ width: canvasW }} onPointerDown={onCanvasDown}>
                <div className="fxt-ruler">
                  {ticks.map(({ v, maj }) => <span key={v}><i className={`fxt-tick${maj ? ' maj' : ''}`} style={{ left: tx(v) }} />{maj && <span className="fxt-tlab num" style={{ left: tx(v) }}>{pad(Math.floor((start + v) / 60))}:{pad(Math.round(start + v) % 60)}</span>}</span>)}
                  {drop !== null && <div className="fxt-dropflag num" style={{ left: tx(drop) }}><Glyph name="lock" size={10} sw={2} /><span>Дроп {tc(start + drop)}</span></div>}
                </div>
                <div ref={(el) => { laneRefs.current.frames = el; cutsGuideRef.current = el; }} className={`fxt-lane l-frames${place && 'join' in place ? ' over' : ''}`}>
                  {recipe.loading && <span className="fxt-hint" style={{ left: X0 + 8 }}>Считаем склейки по темпу трека…</span>}
                  {recipe.error && <span className="fxt-hint" style={{ left: X0 + 8, color: 'var(--warning)' }}>Не удалось посчитать склейки: {recipe.error.message}</span>}
                  {!recipe.loading && !recipe.error && Array.from({ length: shots }, (_, i) => {
                    const x = tx(bounds[i]); const w = tx(bounds[i + 1]) - x;
                    return (
                      <div key={`f${i}`} className={`fxt-clip fr${sel?.type === 'frame' && sel.i === i ? ' sel' : ''}${i === fNow ? ' cur' : ''}`} data-frame={i} style={{ left: x + 1, width: w - 2 }}>
                        {i > 0 && <i className="fxt-edge l" data-cut={i - 1} />}
                        <span className="n num">{pad(i + 1)}</span>
                        {w > 86 && <span className="d num">{secs(bounds[i + 1] - bounds[i])}</span>}
                        {i < shots - 1 && <i className="fxt-edge r" data-cut={i} />}
                      </div>
                    );
                  })}
                  {!recipe.loading && !recipe.error && cuts.map((c, i) => {
                    const label = transitionAt(i);
                    return (
                      <button key={`j${i}`} type="button" className={`fxt-join${label === NO_GLUE ? ' none' : ''}${sel?.type === 'cut' && sel.i === i ? ' sel' : ''}${place && 'join' in place && place.join === i ? ' target' : ''}`} style={{ left: tx(c) }} aria-label={`Склейка ${i + 1}: ${label}`}
                        onClick={(e) => { setSel({ type: 'cut', i }); seek(Math.max(0, c - 0.5)); const z = zoomScale(); const r = e.currentTarget.getBoundingClientRect(); setPop({ i, x: (r.left + r.width / 2) / z, y: r.top / z }); }}>
                        {label === NO_GLUE ? <Glyph name="plus" size={12} sw={2} /> : <Ic kind="trans" label={label} on size={24} />}
                      </button>
                    );
                  })}
                </div>
                <div ref={(el) => { laneRefs.current.hook = el; }} className={`fxt-lane l-hook${place && 'lane' in place && place.lane === 'hook' ? ' over' : ''}`}>
                  {!hooksOn
                    ? <><div className="fxt-lockz" /><span className="fxt-hint" style={{ left: X0 + 8 }}>Хуки в 16:9 пока не работают — выбранный хук останется в 9:16</span></>
                    : hookRange && activeHookLabel
                      ? (() => { const x = tx(hookRange[0]); const w = Math.max(18, tx(hookRange[1]) - x); return <><div className={`fxt-clip hk${sel?.type === 'hook' ? ' sel' : ''}${vis.hook ? '' : ' off'}`} style={{ left: x, width: w }}><Glyph name="lock" size={11} sw={2} /><Glyph name={GLYPH[activeHookLabel]} size={13} />{w >= 80 && <span className="lab">{activeHookLabel}</span>}{config.effectHook === 'Слоу-шаттер' && kind === 'effects' && <i className="fxt-edge r" data-hookedge="r" data-tip="Тяни: стандарт · 3 кадра · до конца" />}</div>{w < 80 && <span className="fxt-outlab" style={{ left: x + w + 8 }}>{activeHookLabel}</span>}</>; })()
                      : <span className="fxt-hint" style={{ left: tx(drop ?? 0) + 10 }}>{drop === null ? 'Выбери дроп на шаге FX — хук встанет на него' : 'Хук встанет на дроп'}</span>}
                </div>
                {([0, 1] as const).map((L) => (
                  <div key={L} ref={(el) => { laneRefs.current[`s${L}`] = el; }} className={`fxt-lane l-s${L}${place && 'lane' in place && place.lane === `s${L}` ? ' over' : ''}`}>
                    {!styles.some((s) => s.lane === L) && <span className="fxt-hint" style={{ left: X0 + 8 }}>{L ? 'Второй стиль поверх первого — до двух на кадр' : 'Перетащи стиль — он ляжет по границам кадров'}</span>}
                    {styles.filter((s) => s.lane === L && s.b <= shots).map((s) => {
                      const x = tx(bounds[s.a]); const w = tx(bounds[s.b]) - x;
                      return (
                        <div key={s.uid} className={`fxt-clip st${sel?.type === 'style' && sel.uid === s.uid ? ' sel' : ''}${vis[L ? 's1' : 's0'] ? '' : ' off'}`} data-uid={s.uid} style={{ left: x + 1, width: w - 2 }}>
                          <i className="fxt-edge l" data-side="l" /><Glyph name={GLYPH[s.style]} size={13} /><span className="lab">{s.style}</span><i className="fxt-edge r" data-side="r" />
                        </div>
                      );
                    })}
                  </div>
                ))}
                <div className="fxt-lane l-subs">
                  {!subs.length && <span className="fxt-hint" style={{ left: X0 + 8 }}>Субтитры появятся после примерки на шаге «Текст»</span>}
                  {subs.map((s, i) => { const x = tx(Math.max(0, s.a)); const w = tx(Math.min(dur, s.b)) - x; return <div key={`s${i}`} className={`fxt-clip sb${s.focus ? ' focus' : ''}${sel?.type === 'sub' && sel.i === i ? ' sel' : ''}${t >= s.a && t < s.b ? ' cur' : ''}${vis.subs ? '' : ' off'}`} data-sub={i} style={{ left: x + 1, width: Math.max(2, w - 2) }} title={s.focus ? `${s.text} · фокус-слово` : s.text}><span className="lab">{s.focus ? '★ ' : ''}{s.text}</span></div>; })}
                </div>
                <div className="fxt-lane l-audio">
                  {beats.map((b, k) => <i key={k} className={`fxt-beat${drop !== null && Math.round((b - drop) * (bpm || 120) / 60) % 4 === 0 ? ' down' : ''}`} style={{ left: tx(b) }} />)}
                  {!beats.length && <span className="fxt-hint" style={{ left: X0 + 8 }}>Биты появятся вместе со склейками</span>}
                </div>
                {drop !== null && <div className="fxt-dropline" style={{ left: tx(drop) }} />}
                <div className="fxt-phl" style={{ left: tx(t) }} />
                {snapLine !== null && <div className="fxt-snapl" style={{ left: tx(snapLine) }} />}
                {place && 'lane' in place && (() => {
                  const el = laneRefs.current[place.lane]; if (!el) return null;
                  return <div className={`fxt-place ${place.bad ? 'bad' : place.lane === 'hook' ? 'hk' : 'st'}`} style={{ left: tx(place.a), width: Math.max(18, tx(place.b) - tx(place.a)), top: el.offsetTop + 6, height: el.offsetHeight - 12 }}>{place.label && <span className="why">{place.label}</span>}</div>;
                })()}
              </div>
            </div>
          </div>
          <div className={`fxt-toast${toast ? ' show' : ''}`}><span className="tx">{toast}</span></div>
        </section>
      </main>

      {ghost && <div className="fxt-ghost" style={{ left: ghost.x, top: ghost.y }}><Ic kind={ghost.item.kind} label={ghost.item.label} on size={26} /><span className="tx">{ghost.item.label}</span></div>}

      {pop && (() => {
        const cur = transitionAt(pop.i);
        const vw = window.innerWidth / zoomScale(); const vh = window.innerHeight / zoomScale();
        const left = clamp(pop.x - 132, 8, vw - 272);
        return (
          <div className="fxt-pop" role="dialog" aria-label={`Переход на склейке ${pop.i + 1}`} style={{ left, bottom: Math.max(8, vh - pop.y + 10) }} onPointerDown={(e) => e.stopPropagation()}>
            <div className="mstage" style={{ width: 169, height: 300 }}>
              <EffectPreview previewId={previewIdFor('effectGlue', cur)} />
            </div>
            <h3>Переход на склейке {pop.i + 1}</h3>
            {GLUES.map((label) => (
              <button key={label} type="button" className="fxt-opt" aria-pressed={label === cur} onClick={() => { setTransition(pop.i, label); setPop(null); }}>
                <Ic kind="trans" label={label} on={label === cur} size={26} /><span className="tx">{label}</span>{label === cur && <span className="ck"><Glyph name="check" size={16} sw={2} /></span>}
              </button>
            ))}
            <div className="foot"><button type="button" className="fxt-pill" onClick={() => { setTransitionAll(cur); setPop(null); }}><span className="tx">Текущий ко всем склейкам</span></button></div>
          </div>
        );
      })()}

      <ActionGuideOverlay
        open={showLibGuide}
        targetRef={libGuideRef}
        title={tr('wizard.fxTimeline.guideLibraryTitle')}
        text={<Trans i18nKey="wizard.fxTimeline.guideLibraryText" components={{
          plus: <b className="font-[700] text-text" />,
          play: <span className="relative -top-px inline-block text-[0.72em]" />
        }} />}
        dismissLabel={tr('wizard.fxTimeline.guideNext')}
        progressLabel={tr('wizard.guideProgress', { current: 1, total: 3 })}
        onDismiss={() => setLibGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<TimelineLibraryGuideVisual />}
      />
      <ActionGuideOverlay
        open={showCutsGuide}
        targetRef={cutsGuideRef}
        title={tr('wizard.fxTimeline.guideCutsTitle')}
        text={tr('wizard.fxTimeline.guideCutsText')}
        dismissLabel={tr('wizard.fxTimeline.guideNext')}
        progressLabel={tr('wizard.guideProgress', { current: 2, total: 3 })}
        onDismiss={() => setCutsGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<TimelineCutsGuideVisual />}
      />
      <ActionGuideOverlay
        open={showPaceGuide}
        targetRef={paceGuideRef}
        title={tr('wizard.fxTimeline.guidePaceTitle')}
        text={tr('wizard.fxTimeline.guidePaceText')}
        dismissLabel={tr('wizard.fxTimeline.guideDismiss')}
        progressLabel={tr('wizard.guideProgress', { current: 3, total: 3 })}
        onDismiss={() => setPaceGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<TimelinePaceGuideVisual />}
      />

      {keysOpen && (
        <div className="fxt-pop" style={{ right: 16, top: 64, width: 280 }} onPointerDown={(e) => e.stopPropagation()}>
          {[['Играть / пауза', 'Пробел'], ['На кадр назад / вперёд', '← →'], ['К прошлой / следующей склейке', '↑ ↓'], ['Удалить выбранное', 'Delete'], ['Отменить / вернуть', 'Ctrl+Z / Ctrl+Shift+Z'], ['Масштаб', 'Ctrl + колесо'], ['Снять выбор / закрыть', 'Esc']].map(([a, b]) => (
            <div key={a} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '6px 0', color: 'var(--fxt-t80)', fontSize: 13 }}><span className="tx">{a}</span><kbd className="tx" style={{ font: 'inherit', fontSize: 11.5, color: 'var(--fxt-t60)', padding: '2px 7px', borderRadius: 6, background: 'var(--fxt-t06)' }}>{b}</kbd></div>
          ))}
        </div>
      )}
    </div>
  );
}
