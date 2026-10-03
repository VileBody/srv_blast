import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { formatTimePrecise, formatTimeRange } from '../../../lib/timeFormat';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../lib/api';
import { isVideoUrl } from '../../../lib/media';
import effectsRegistry from '../../../data/effects-registry.json';
import { HookConfig, HookKind, MontageVideo, TimelinePace, TimelineRecipe, TimelineStyleRange, textSettingsFor, useWizardStore } from '../../../stores/wizardStore';
import { KANT_STYLES, styleIdOf } from '../../../lib/subtitleText';
import { EFFECT_HOOKS, MOTIONS, NO_GLUE, OBJECTS, THOUGHTS, previewIdFor } from '../hookCatalog';
import { PACES, fmtSec, useRecipeCuts } from '../storyboardData';
import { secondsToDropTime, usePlaybackUrl } from '../useFragmentAudio';
import { peakLevels, useTrackPeaks } from '../trackPeaks';
import { usePhone } from '../../../lib/usePhone';
import { useLowData } from '../../../lib/network';
import { SubtitleTextCustomization } from '../SubtitlesPanel';
import { SubtitleCanvas, type SubtitleCanvasProps } from '../SubtitleCanvas';
import { StoryboardReplaceGuideVisual, TimelineEntryGuideVisual } from '../timelineGuides';
import { useGuideDismiss, useMarkGuideSeen } from '../../guidance/useGuideDismiss';
import { useTranslation } from 'react-i18next';
import { useChip } from '../../../i18n/useChip';
import { ActionGuideOverlay } from '../../guidance/ActionGuideOverlay';
import '../FxTimeline.css';
import './montage.css';
import './montage.mobile.css';
import { useCombos, type Combo } from './combos';
import { FrameDock, FrameView, useFramesOf, type Frame } from './sources';
import { createLedger, ledgerPush, ledgerRedo, ledgerUndo, type HistKey } from './history';

/*
 * Монтажный стол — финальный экран батча, открывается с «Пула». Основа — таймлайн FX: та же
 * шапка, библиотека, превью и дорожки. Стол всегда про один ролик батча (переключатель в
 * шапке): кадры — его раскадровка «Пула» (выбираются прямо в превью, как на «Пуле»), хук,
 * переходы на стыках, стили по кадрам и стиль субтитров — его собственные. Правка ложится
 * в этот ролик; «Во все N» у выделенного переносит её на весь батч. Отсюда же — рендер.
 */
type VideoFx = MontageVideo;
const SUB_STYLES: readonly { id: string; name: string }[] = [
  { id: 'brat', name: 'Brat' }, { id: 'jakson', name: 'Jakson' }, { id: 'impulse', name: 'Impulse' },
  { id: 'tape', name: 'Tape' }, { id: 'trendy', name: 'Trendy' },
  // тайтлы — тот же список, что разбирает styleIdOf (lib/subtitleText)
  ...KANT_STYLES,
];

const FPS = 30;
const TD = 0.36;
const X0 = 20;
const pad = (n: number) => String(n).padStart(2, '0');
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
// время в подписях стола — тот же вид, что на всём визарде (мм:сс.сс), а не кадры: «.12» читали как сотые
const tc = (t: number) => formatTimePrecise(t);
/** тач-экран без наведения: «по ховеру» там не срабатывает никогда */
const COARSE_POINTER = typeof window !== 'undefined' && window.matchMedia?.('(hover: none)').matches;
const eOut = (p: number) => 1 - Math.pow(1 - p, 4);
const zoomScale = () => Number.parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
const eIO = (p: number) => p < 0.5 ? 8 * p ** 4 : 1 - Math.pow(-2 * p + 2, 4) / 2;
// внутренние подписи хука «Прогрев» (не из каталога): показываются через перевод, см. useFxName
const OWN_VIDEO = '__own_video__';
const OWN_SOUND = '__own_sound__';
/**
 * Название эффекта на языке интерфейса. Сами подписи в сторе остаются русскими — по ним
 * матчит бэк и effects-registry; переводим только показ (словарь chip, как на шаге FX).
 */
function useFxName() {
  const { t } = useTranslation();
  const chip = useChip();
  return (label?: string) => (!label ? '' : label === OWN_VIDEO ? t('wizard.montage.ownVideo') : label === OWN_SOUND ? t('wizard.montage.ownSound') : chip(label));
}

/* ── тонкие глифы (один язык на всю библиотеку и дорожки) ── */
const C = (x: number, y: number, r: number, a = '') => `<circle cx="${x}" cy="${y}" r="${r}" ${a}/>`;
const P = (d: string, a = '') => `<path d="${d}" ${a}/>`;
const FILL = 'fill="currentColor" stroke="none"';
function star(n: number, r1: number, r2: number) { let d = ''; for (let k = 0; k < n * 2; k++) { const r = k % 2 ? r2 : r1; const a = Math.PI * k / n - Math.PI / 2; d += `${k ? 'L' : 'M'}${(12 + r * Math.cos(a)).toFixed(2)} ${(12 + r * Math.sin(a)).toFixed(2)}`; } return `${d}Z`; }
const ICONS: Record<string, string> = {
  back: P('M15 5l-7 7 7 7'), fwd: P('M9 5l7 7-7 7'), close: P('M6.5 6.5l11 11M17.5 6.5l-11 11'), play: P('M6 3.5v13l11-6.5L6 3.5Z', FILL), pause: P('M6 4h3v12H6zM11 4h3v12h-3z', FILL),
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
  audio: P('M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10'), text: P('M5 6h14M12 6v13M9 19h6'),
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  table: '<rect x="3" y="4" width="18" height="8" rx="2"/>' + P('M3 16h18M3 20h12'),
  chain: P('M4 12h11M11 7l5 5-5 5') + P('M20 5v14'), reroll: P('M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4'),
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
  shake: P('M3 12l3-5 3 10 3-10 3 10 3-10 3 5'), oneshot: C(12, 12, 8) + C(12, 12, 3, FILL) + P('M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3'),
  overlay: '<rect x="3" y="7" width="13" height="13" rx="2"/>' + P('M8 7V5a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-4'),
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
/*
 * Подписи эффектов. Свои — в локали (wizard.montage.meta.<подпись>), эффекты реестра (пресеты
 * Kant) — в wizard.montage.fxMeta: так у них есть английский. Для переходов и стилей fxMeta
 * смотрим первым: «Инверсия» — и хук «Мысль», и стиль Kant, и общая meta давала стилю
 * подпись хука («голос поверх трека»). META — подпись из реестра для эффекта, которого ещё
 * нет в локали: только в ru, английского текста у неё нет.
 */
const META: Record<string, string> = {};
function useMetaOf() {
  const { t, i18n } = useTranslation();
  return (label: string, kind?: LibKind) => {
    const fxKey = `wizard.montage.fxMeta.${label}`;
    if ((kind === 'style' || kind === 'trans') && i18n.exists(fxKey)) return t(fxKey);
    const key = `wizard.montage.meta.${label}`;
    if (i18n.exists(key)) return t(key);
    if (i18n.exists(fxKey)) return t(fxKey);
    return i18n.language.startsWith('ru') ? META[label] ?? '' : '';
  };
}
/*
 * Эффекты, заведённые в реестр со своими полями стола (montageGroup / montageGlyph / meta) —
 * например, пресеты Kant: глиф и подпись берутся из реестра, без правки этих таблиц.
 * Ручные записи выше важнее: реестр только дополняет.
 */
interface RegistryFx { label: string; montageGroup?: string; montageGlyph?: string; meta?: string }
const REGISTRY_FX: RegistryFx[] = [...(effectsRegistry.glue as RegistryFx[]), ...(effectsRegistry.style as RegistryFx[])];
for (const e of REGISTRY_FX) {
  if (e.montageGlyph && !GLYPH[e.label]) GLYPH[e.label] = e.montageGlyph;
  if (e.meta && !META[e.label]) META[e.label] = e.meta;
}
/** Дописать в группы стола эффекты реестра с montageGroup этой группы (порядок — как в реестре). */
function withRegistryItems(groups: Group[], list: RegistryFx[]): Group[] {
  return groups.map((g) => ({ ...g, items: [...g.items, ...list.filter((e) => e.montageGroup === g.id && !g.items.includes(e.label)).map((e) => e.label)] }));
}
const TRANSITION_ANIM: Record<string, string> = { 'Щелчок': 'snap', 'Минимакс': 'minimax', 'Экстракт': 'extract', 'Инверт': 'invert', 'Вспышка': 'flash' };
const GLUES = [NO_GLUE, ...effectsRegistry.glue.map((e) => e.label)];
const STYLES = effectsRegistry.style.map((e) => e.label);

function Glyph({ name, size = 18, sw = 1.5 }: { name?: string; size?: number; sw?: number }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: ICONS[name ?? ''] ?? '' }} />;
}
function Ic({ label, kind, on, size = 32 }: { label?: string; kind: 'hook' | 'style' | 'trans' | 'frame' | 'text'; on?: boolean; size?: number }) {
  return <span className={`fxt-ic k-${kind}${on ? ' on' : ''}`} style={{ width: size, height: size }}><Glyph name={GLYPH[label ?? ''] ?? label} size={Math.round(size * 0.53)} /></span>;
}
function Thumb({ url, size = 32, ratio = 16 / 9, on }: { url?: string | null; size?: number; ratio?: number; on?: boolean }) {
  return <span className={`mt-thumb${on ? ' on' : ''}`} style={{ width: Math.round(size / ratio * 1.0), height: size }}>{url && <img src={url} alt="" draggable={false} />}</span>;
}

/* ── хуки ── */
type HookCatalogKind = Exclude<HookKind, 'none'>;
const HOOK_CATS: { kind: HookCatalogKind; label: string; icon: string; key: keyof HookConfig | null; options: string[] }[] = [
  { kind: 'warmup', label: 'Прогрев', icon: 'warmup', key: null, options: [] },
  { kind: 'object', label: 'Объект', icon: 'object', key: 'object', options: OBJECTS },
  { kind: 'effects', label: 'Эффекты', icon: 'effects', key: 'effectHook', options: EFFECT_HOOKS },
  { kind: 'motion', label: 'Движение', icon: 'motion', key: 'motion', options: MOTIONS },
  { kind: 'thought', label: 'Мысль', icon: 'thought', key: 'thought', options: THOUGHTS }
];
const MOTION_LEAD: Record<string, [number, boolean]> = { 'Свайп': [4.304, false], 'Тап': [4.304, true], 'Зум': [4.204, false], 'Задержи': [4.304, true], 'Голова': [4.004, false] };
function hookLabel(kind: HookKind | undefined, config: HookConfig | undefined): string | undefined {
  if (!kind || !config) return undefined;
  if (kind === 'warmup') return config.sound ? (config.warmupKind === 'video' ? OWN_VIDEO : OWN_SOUND) : undefined;
  const cat = HOOK_CATS.find((c) => c.kind === kind);
  return cat?.key ? (config[cat.key] as string | undefined) : undefined;
}
const EFFECT_HOOK_SEC: Record<string, number> = { 'Молния': 0.63, 'Затвор': 0.6, 'Слоу-шаттер': 0.5, 'Негатив зум': 0.25 };
type SlowExtend = '' | 'after_drop:3' | 'to_end';
function slowShutterEnd(extend: SlowExtend, drop: number, dur: number, bounds: number[]): number {
  const base = drop + EFFECT_HOOK_SEC['Слоу-шаттер'];
  if (extend === 'to_end') return Math.max(base, dur);
  if (extend === 'after_drop:3') { const after = bounds.slice(1, -1).filter((b) => b > drop + 1e-3); return Math.max(base, after.length >= 3 ? after[2] : dur); }
  return Math.min(dur, base);
}
/** вариант длины слоу-шаттера → ключ подписи (wizard.montage.*) */
const SLOW_EXTENDS: [SlowExtend, string][] = [['', 'slowStd'], ['after_drop:3', 'slow3'], ['to_end', 'slowEnd']];
function hookSpan(kind: HookKind, config: HookConfig, drop: number, dur: number, bpm: number, bounds: number[]): [number, number] | null {
  if (kind === 'effects') {
    if (config.effectHook === 'Слоу-шаттер') return [drop, slowShutterEnd((config.effectHookExtend ?? '') as SlowExtend, drop, dur, bounds)];
    return [drop, Math.min(dur, drop + (EFFECT_HOOK_SEC[config.effectHook ?? ''] ?? 0.6))];
  }
  if (kind === 'motion') { const [lead, fixed] = MOTION_LEAD[config.motion ?? ''] ?? [4.3, false]; const eff = fixed || !bpm ? lead : lead * 128 / bpm; return [Math.max(0, drop - eff), drop]; }
  if (kind === 'thought') return [Math.max(0, drop - 2.8), drop];
  if (kind === 'object') return [0, drop];
  if (kind === 'warmup') return [Math.min(0.5, drop), Math.max(0, drop - 0.5)];
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
const frameIndex = (bounds: number[], v: number) => { let f = 0; while (f < bounds.length - 2 && v >= bounds[f + 1]) f++; return f; };

/* ── субтитры в превью: стиль ролика и настройки текста шага «Текст», слова по таймингам примерки ── */
// раскладка и тайминг — тот же JS-модуль AE-геометрии, что в превью шага «Текст»
type SubProps = Omit<SubtitleCanvasProps, 'time' | 'rest'>;
interface Word { a: number; b: number; text: string; focus: boolean; idx: number }

/* ── кадр ролика: клипы по склейкам, переход на стыке, стили, вспышка хука, субтитры ── */
export interface StageFx { transitionAt: (i: number) => string; styles: TimelineStyleRange[]; hookKind?: HookKind; hookLabel?: string; hookRange: [number, number] | null; frameUrl?: string | null }
export function Stage({ frames, bounds, t, playing = false, fx, sub, w, h, className = '', children }: {
  frames: Frame[]; bounds: number[]; t: number; playing?: boolean; fx: StageFx;
  sub?: SubProps; w: number; h: number; className?: string; children?: ReactNode;
}) {
  const [subError, setSubError] = useState<string | null>(null);
  const fxName = useFxName();
  const shots = Math.max(1, bounds.length - 1);
  const fNow = frameIndex(bounds, t);
  const since = t - (bounds[fNow] ?? 0);
  const trLabel = fNow > 0 ? fx.transitionAt(fNow - 1) : NO_GLUE;
  const trAnim = TRANSITION_ANIM[trLabel];
  const inTr = Boolean(trAnim) && since < TD;
  const p = inTr ? since / TD : 1;
  const activeStyles = fx.styles.filter((s) => t >= bounds[s.a] && t < bounds[Math.min(s.b, shots)]).map((s) => s.style);
  const inHook = fx.hookRange && t >= fx.hookRange[0] && t < fx.hookRange[1];
  const flash = inHook && fx.hookKind === 'effects' && fx.hookLabel === 'Молния' && fx.hookRange ? Math.max(0, 0.8 * (t - fx.hookRange[0] < 0.06 ? (t - fx.hookRange[0]) / 0.06 : 1 - (t - fx.hookRange[0] - 0.06) / 0.22)) : 0;
  const cover = inHook && (fx.hookKind === 'motion' || fx.hookKind === 'thought');
  const trFlash = inTr && trAnim === 'flash' ? (p < 0.25 ? p / 0.25 * 0.95 : Math.max(0, 0.95 * (1 - (p - 0.25) / 0.6))) : 0;
  const shotStyle = (i: number): CSSProperties => {
    if (!(inTr && i === fNow)) return {};
    const e = eOut(p);
    if (trAnim === 'snap') return { clipPath: `inset(0 ${(100 - 100 * eIO(p)).toFixed(2)}% 0 0)` };
    if (trAnim === 'minimax') return { opacity: e, transform: `scale(${1.35 - 0.32 * e})` };
    if (trAnim === 'extract') return { transform: `translateY(${((1 - e) * 100).toFixed(2)}%) scaleY(${1 + 0.3 * (1 - e)})` };
    if (trAnim === 'invert') return { filter: `invert(${p < 0.45 ? 1 : 1 - (p - 0.45) / 0.55})` };
    if (trAnim === 'flash') return { opacity: p > 0.25 ? 1 : 0 };
    return {};
  };
  return (
    <div className={`fxt-stage ${className}`} style={{ width: w, height: h }}>
      <div className="fxt-fx" style={{ filter: styleFilter(activeStyles, t) }}>
        {Array.from({ length: shots }, (_, i) => {
          const frame = frames[i];
          const visible = i === fNow || (inTr && i === fNow - 1);
          // следующий кадр уже на странице (невидимый): к склейке его клип подгружен
          const ahead = i === fNow + 1;
          if (!visible && !ahead) return null;
          if (ahead && !visible) return frame ? <FrameView key={`${frame.id}:${i}`} frame={frame} t={t} at={0} playing={false} className="shot" style={{ zIndex: 0, opacity: 0 }} /> : null;
          const style = { zIndex: i === fNow ? 2 : 1, ...shotStyle(i) };
          if (!frame) return <ShotPlaceholder key={`ph${i}`} i={i} style={style} />;
          return <FrameView key={`${frame.id}:${i}`} frame={frame} t={t} at={t - (bounds[i] ?? 0)} playing={playing && i === fNow} className="shot on" style={style} />;
        })}
      </div>
      {cover && <div className="mt-cover"><span>{fxName(fx.hookLabel)}</span></div>}
      {/* рамка ролика — поверх всего, как в рендере (PNG-маска на весь кадр) */}
      {fx.frameUrl && <img className="mt-frame" src={fx.frameUrl} alt="" draggable={false} />}
      <div className="fxt-ov fxt-flash" style={{ opacity: flash }} />
      <div className="fxt-ov fxt-trflash" style={{ opacity: trFlash }} />
      {sub && sub.words.length > 0 && (
        <div className="w12 mt-sub">
          <SubtitleCanvas {...sub} time={t} rest={!playing} onError={setSubError} />
          {subError && <div className="w12-sub-error">{subError}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

function ShotPlaceholder({ i, style }: { i: number; style?: CSSProperties }) {
  const { t: tr } = useTranslation();
  return <div className="fxt-ph" style={{ ...style, background: `linear-gradient(145deg, hsl(${(i * 53 + 260) % 360} 45% 22%), hsl(${(i * 53 + 305) % 360} 35% 10%))` }}><span>{tr('wizard.montage.shotPh', { n: pad(i + 1) })}</span></div>;
}

/**
 * Неподвижный кадр ролика: картинка кадра под временем t (JPEG прослойки, см. FrameView thumb),
 * его стили, плашка хука и рамка. Без <video>, субтитров и анимации — так стоят ролики сетки
 * «Все ролики» и плитки библиотеки, пока на них не навели: живой Stage — свой декодер на клип.
 */
function StagePoster({ frames, bounds, t, fx, w, h, className = '' }: { frames: Frame[]; bounds: number[]; t: number; fx: StageFx; w: number; h: number; className?: string }) {
  const fxName = useFxName();
  const shots = Math.max(1, bounds.length - 1);
  const k = frameIndex(bounds, t);
  const activeStyles = fx.styles.filter((s) => t >= bounds[s.a] && t < bounds[Math.min(s.b, shots)]).map((s) => s.style);
  const inHook = fx.hookRange && t >= fx.hookRange[0] && t < fx.hookRange[1];
  const cover = inHook && (fx.hookKind === 'motion' || fx.hookKind === 'thought');
  return (
    <div className={`fxt-stage ${className}`} style={{ width: w, height: h }}>
      <div className="fxt-fx" style={{ filter: styleFilter(activeStyles, t) }}>
        {frames[k] ? <FrameView frame={frames[k]} t={t} thumb className="shot on" /> : <ShotPlaceholder i={k} />}
      </div>
      {cover && <div className="mt-cover"><span>{fxName(fx.hookLabel)}</span></div>}
      {fx.frameUrl && <img className="mt-frame" src={fx.frameUrl} alt="" draggable={false} />}
    </div>
  );
}

/** Пример эффекта на своём ролике: окно [at − lead, at − lead + span] крутится по кругу. */
function LoopStage({ frames, bounds, at, dur, fx, w = 169, h = 300, lead = 0.5, span = 1.5, paused = false }: {
  frames: Frame[]; bounds: number[]; at: number; dur: number; fx: StageFx; w?: number; h?: number; lead?: number; span?: number;
  /**
   * плитка не под курсором (или ушла из виду) — цикл стоит, вместо живого Stage неподвижный
   * кадр: иначе каждая видимая плитка держала бы свои <video> и свой цикл одновременно
   */
  paused?: boolean;
}) {
  const [t, setT] = useState(at - 0.4);
  useEffect(() => {
    if (paused) return undefined;
    let raf = 0; const t0 = performance.now();
    const tick = (now: number) => { setT(clamp(at - lead + ((now - t0) / 1000) % span, 0, dur - 0.01)); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [at, dur, lead, span, paused]);
  if (paused) return <StagePoster frames={frames} bounds={bounds} t={t} fx={fx} w={w} h={h} className="mt-loop" />;
  // время идёт вживую — клип играет сам и перематывается только на стыке круга, а не каждый кадр
  return <Stage frames={frames} bounds={bounds} t={t} playing fx={fx} w={w} h={h} className="mt-loop" />;
}

/**
 * Плитка оживает, только когда её видно: десятки автоплеев не грузят страницу. visible — видна
 * сейчас, ever — уже была видна: медиа монтируется один раз и дальше лишь встаёт на паузу,
 * иначе возврат к плитке качал бы пример заново.
 */
function useInView<T extends Element>(): [React.RefObject<T>, boolean, boolean] {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);
  const [ever, setEver] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => { setVisible(entry.isIntersecting); if (entry.isIntersecting) setEver(true); }, { rootMargin: '80px' });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, visible, ever];
}

/** Пример-видео библиотеки: играет, пока виден; на медленной сети сам не стартует (только по наведению). */
function LazyVideo({ src, visible, lowData }: { src: string; visible: boolean; lowData: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    if (visible && !lowData) void video.play().catch(() => undefined); else video.pause();
  }, [visible, lowData]);
  return <video ref={ref} src={src} muted loop playsInline preload={lowData ? 'none' : 'metadata'} draggable={false} />;
}

/** Пример стиля субтитров на плашке — та же экономия, что у плиток: без постера, пустая до показа. */
function PlateMedia({ url }: { url: string }) {
  const [ref, visible, ever] = useInView<HTMLSpanElement>();
  const lowData = useLowData();
  const hover = (play: boolean) => () => {
    const video = ref.current?.querySelector('video');
    if (!lowData || !video) return;
    if (play) void video.play().catch(() => undefined); else video.pause();
  };
  return (
    <span ref={ref} className="mt-plate-media" onPointerEnter={hover(true)} onPointerLeave={hover(false)}>
      {ever && (isVideoUrl(url) ? <LazyVideo src={url} visible={visible} lowData={lowData} /> : <img src={url} alt="" draggable={false} />)}
    </span>
  );
}

/**
 * Лента плиток: в группе их бывает больше, чем влезает, — скролл вбок (тачпад, Shift+колесо),
 * стрелки на краях и фейд там, где за краем ещё есть плитки. Без перетаскивания ленты мышью:
 * плитки сами тянутся на таймлайн.
 */
function TileRow({ children }: { children: ReactNode }) {
  const { t: tr } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: true, right: true });
  const sync = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setEdges({ left: el.scrollLeft < 4, right: el.scrollLeft + el.clientWidth > el.scrollWidth - 4 });
  }, []);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sync]);
  const by = (dir: 1 | -1) => { const el = ref.current; el?.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' }); };
  return (
    <div className="mt-fxrow" data-fade-l={!edges.left || undefined} data-fade-r={!edges.right || undefined}>
      <button type="button" className="mt-fxrow-btn l" data-off={edges.left || undefined} aria-label={tr('wizard.montage.prevTiles')} tabIndex={-1} onClick={() => by(-1)}><Glyph name="back" size={16} sw={1.8} /></button>
      <div ref={ref} className="mt-fxtiles" onScroll={sync}>{children}</div>
      <button type="button" className="mt-fxrow-btn r" data-off={edges.right || undefined} aria-label={tr('wizard.montage.nextTiles')} tabIndex={-1} onClick={() => by(1)}><Glyph name="fwd" size={16} sw={1.8} /></button>
    </div>
  );
}

/** Медиа плитки: настоящий отрендеренный пример из каталога эффектов, иначе эффект на кадрах ролика. */
export interface LibPreview { url?: string | null; sim?: (size: { w: number; h: number }, paused: boolean) => ReactNode; state?: 'loading' | 'error' }
const TILE = { w: 180, h: 320 };
function TileMedia({ preview }: { preview: LibPreview }) {
  const { t: tr } = useTranslation();
  const [ref, visible, seen] = useInView<HTMLSpanElement>();
  // медленная сеть / экономия трафика: пример не стартует сам — играет по наведению или тапу
  const lowData = useLowData();
  // «на твоём ролике» оживает только под курсором: остальные плитки — неподвижный кадр.
  // На тач-экране наведения нет (тап добавляет эффект) — там видимая плитка играет сама, как раньше.
  const [hovered, setHot] = useState(false);
  const hot = hovered || COARSE_POINTER;
  const hover = (play: boolean) => (e: React.SyntheticEvent<HTMLSpanElement>) => {
    setHot(play);
    const video = e.currentTarget.querySelector('video');
    if (!lowData || !video) return;
    if (play) void video.play().catch(() => undefined); else video.pause();
  };
  return (
    <span ref={ref} className="mt-fxtile-media" onPointerEnter={hover(true)} onPointerLeave={hover(false)}>
      {seen && (preview.url
        ? <LazyVideo src={preview.url} visible={visible} lowData={lowData} />
        : preview.sim?.(TILE, !(visible && hot)))}
      {seen && !preview.url && preview.sim && <span className="mt-fxtile-tag">{tr('wizard.montage.onYourVideo')}</span>}
      {preview.state === 'loading' && <span className="mt-fxtile-none"><span className="spinner" aria-hidden="true" /></span>}
      {preview.state === 'error' && <span className="mt-fxtile-none">{tr('wizard.montage.previewsFailed')}</span>}
      {!preview.state && !preview.url && !preview.sim && <span className="mt-fxtile-none">{tr('wizard.montage.previewMissing')}</span>}
    </span>
  );
}

type Sel = { type: 'frame'; i: number } | { type: 'cut'; i: number } | { type: 'hook' } | { type: 'style'; uid: number } | { type: 'sub'; i: number } | null;
type LibKind = 'src' | 'text' | 'hook' | 'trans' | 'style' | 'frame';
interface LibItem { kind: LibKind; label: string; hookKind?: HookCatalogKind; url?: string | null }
type PlaceTarget = { lane: string; a: number; b: number; bad?: boolean; label?: string } | { join: number };
/**
 * Шаг истории стола: прежнее состояние затронутых роликов (null — записи ещё не было) и, если
 * правка общая для батча, — склеек и дропа. Отмена возвращает ровно это и не трогает остальное.
 * seq/prev — номер шага и прежние верхи его ключей в учёте живых шагов (montage/history.ts).
 */
interface Snapshot { videos: Record<number, MontageVideo | null>; timeline?: Pick<TimelineRecipe, 'key' | 'pace' | 'cuts' | 'edited'>; dropTime?: string; seq?: number; prev?: Record<HistKey, number> }
/** ключи учёта, которые меняет шаг: ролики, склейки, дроп */
const keysOf = (snap: Snapshot): HistKey[] => [...Object.keys(snap.videos).map((i) => `v${i}`), ...(snap.timeline ? ['tl'] : []), ...('dropTime' in snap ? ['drop'] : [])];
/** часть шага — только эти ключи */
const pickKeys = (snap: Snapshot, keys: HistKey[]): Snapshot => ({
  videos: Object.fromEntries(Object.entries(snap.videos).filter(([i]) => keys.includes(`v${i}`))),
  ...(snap.timeline && keys.includes('tl') ? { timeline: snap.timeline } : {}),
  ...('dropTime' in snap && keys.includes('drop') ? { dropTime: snap.dropTime } : {})
});
/** Поля самого хука: «Во все N» у хука переносит только их — склейка и стили ролика остаются его. */
const HOOK_FIELDS: readonly (keyof HookConfig)[] = ['object', 'effectHook', 'effectHookExtend', 'motion', 'thought', 'warmupKind', 'sound', 'soundUrl', 'soundPlaybackUrl', 'soundDuration', 'videoUrl', 'videoWidth', 'videoHeight', 'videoDuration', 'videoHasAudio'];

/* ── тултипы ── */
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

/* ── группы библиотеки: переходы и стилизации раскладываются так же, как хуки ── */
/** название группы — wizard.montage.groups.<id> */
interface Group { id: string; icon: string; items: string[] }
const GLUE_GROUPS: Group[] = withRegistryItems([
  { id: 'shake', icon: 'shake', items: [] },
  { id: 'oneshot', icon: 'oneshot', items: [] },
  { id: 'flash', icon: 't_flash', items: ['Вспышка', 'Инверт'] },
  { id: 'overlay', icon: 'overlay', items: [] },
  { id: 'wipe', icon: 't_snap', items: ['Щелчок'] },
  { id: 'zoom', icon: 't_minimax', items: ['Минимакс'] },
  { id: 'push', icon: 't_extract', items: ['Экстракт'] },
  { id: 'glitchcut', icon: 'glitch', items: [] }
], effectsRegistry.glue as RegistryFx[]);
/*
 * Переходы в том же порядке и тех же группах, что во вкладке «Переходы» библиотеки: окно
 * выбора на склейке листает это дерево (стрелки и «2/6» идут по нему), иначе один и тот же
 * набор выглядел двумя разными. Переход, которого нет ни в одной группе, не теряется — он
 * уходит в «Другие» в конце.
 */
const GLUE_OPTIONS: PopOption[] = (() => {
  const known = new Set(GLUES);
  const out: PopOption[] = [{ id: NO_GLUE, label: NO_GLUE, glyph: NO_GLUE }];
  const placed = new Set<string>([NO_GLUE]);
  for (const g of GLUE_GROUPS.filter((x) => x.items.length)) {
    for (const label of g.items) {
      if (!known.has(label) || placed.has(label)) continue;
      out.push({ id: label, label, glyph: label, group: g.id, groupIcon: g.icon });
      placed.add(label);
    }
  }
  for (const label of GLUES) if (!placed.has(label)) out.push({ id: label, label, glyph: label, group: 'other' });
  return out;
})();
const STYLE_GROUPS: Group[] = withRegistryItems([
  { id: 'color', icon: 'bw', items: ['Ч/Б', 'Неон', 'Night Vision'] },
  { id: 'film', icon: 'oldcam', items: ['Старая камера', 'Ксерокс'] },
  { id: 'distort', icon: 'wave', items: ['Глитч', 'Wave'] },
  { id: 'light', icon: 'crystal', items: ['Crystal Glow'] }
], effectsRegistry.style as RegistryFx[]);

/* ── библиотека ── */
const Library = memo(function Library({ tab, setTab, open, setOpen, used, activeHookKind, subStyle, subPreviews, onPickSub, textCfg, onAdd, onDragStart, frames, frameId, onPickFrame, frameNote, frameBase, frameAll, previewOf, tapAdd }: {
  tab: LibKind; setTab: (tab: LibKind) => void; open: Record<string, boolean>; setOpen: (kind: string) => void;
  used: (item: LibItem) => boolean; activeHookKind?: HookKind;

  subStyle?: string; subPreviews: Record<string, string | undefined>; onPickSub: (name: string) => void; textCfg: ReactNode;
  onAdd: (item: LibItem) => void; onDragStart: (item: LibItem, e: ReactPointerEvent) => void;
  /** рамки: каталог, выбранная у ролика, выбор; frameNote — почему ролик рамку не принимает */
  frames: { id: string; label: string; previewUrl: string }[]; frameId?: string | null; onPickFrame: (id: string | null) => void; frameNote?: string; frameBase?: ReactNode; frameAll?: ReactNode;
  /** пример пункта хуков/переходов/стилей — плитка с автоплеем: по одному названию не выбрать */
  previewOf: (item: LibItem) => LibPreview;
  /** телефон: тап по плитке ставит её (CapCut) — перетаскивать на дорожку пальцем неудобно */
  tapAdd?: boolean;
}) {
  const { t: tr } = useTranslation();
  const fxName = useFxName();
  const metaOf = useMetaOf();
  /*
   * Плитка — группа, а не фокусируемый div вокруг кнопки (вложенные интерактивы скринридер
   * читал кашей): с клавиатуры и скринридера ставит одна кнопка «+» (у стоящего — «✓»,
   * aria-pressed, жмётся так же), мышь тянет плитку, на телефоне тап по плитке.
   */
  const actBtn = (item: LibItem, on: boolean) => (
    <button type="button" data-act className={`${on ? 'mt-on' : 'fxt-mini'} act`} aria-pressed={on} aria-label={tr('wizard.montage.add', { name: fxName(item.label) })} onClick={() => onAdd(item)}>
      <Glyph name={on ? 'check' : 'plus'} size={14} sw={on ? 2 : 1.8} />
    </button>
  );
  const tile = (item: LibItem) => {
    const on = used(item);
    return (
      <div key={`${item.kind}:${item.label}`} role="group" aria-label={fxName(item.label)} className={`mt-fxtile${on ? ' on' : ''}`} data-tip={metaOf(item.label, item.kind) || undefined}
        onPointerDown={(e) => { if (!tapAdd && !(e.target as Element).closest('[data-act]')) onDragStart(item, e); }}
        onClick={tapAdd ? (e) => { if (!(e.target as Element).closest('[data-act]')) onAdd(item); } : undefined}>
        <TileMedia preview={previewOf(item)} />
        <span className="nm"><b>{fxName(item.label)}</b></span>
        {actBtn(item, on)}
      </div>
    );
  };
  const items = (list: LibItem[]) => <TileRow>{list.map(tile)}</TileRow>;
  const row = (item: LibItem, lead: ReactNode, meta?: string, disabled = false) => {
    const on = used(item);
    return (
      <div key={`${item.kind}:${item.label}`} role="group" aria-label={fxName(item.label)} aria-disabled={disabled || undefined} className={`fxt-item${on ? ' on' : ''}${disabled ? ' off' : ''}`}
        onPointerDown={(e) => { if (!tapAdd && !disabled && !(e.target as Element).closest('[data-act]')) onDragStart(item, e); }}
        onClick={tapAdd && !disabled ? (e) => { if (!(e.target as Element).closest('[data-act]')) onAdd(item); } : undefined}>
        {lead}
        <span className="nm"><b>{fxName(item.label)}</b><small>{meta ?? metaOf(item.label, item.kind)}</small></span>
        {!disabled && <span className="acts">{actBtn(item, on)}</span>}
      </div>
    );
  };
  const groups = (list: Group[], kind: 'trans' | 'style') => [...list.filter((g) => g.items.length), ...list.filter((g) => !g.items.length)].map((g) => {
    const inUse = g.items.some((label) => used({ kind, label }));
    return (
      <div key={g.id} className="fxt-acc" data-open={Boolean(open[g.id])}>
        <button type="button" className="fxt-acc-h" aria-expanded={Boolean(open[g.id])} onClick={() => setOpen(g.id)}>
          <span className={`fxt-ic k-${kind} sm${inUse ? ' on' : ''}`}><Glyph name={g.icon} size={14} /></span>
          <span className="name">{tr(`wizard.montage.groups.${g.id}`)}{g.items.length > 0 && <span className="c">{g.items.length}</span>}</span>
          {inUse && <span className="was">{tr('wizard.montage.inThisVideo')}</span>}
          {!g.items.length && <span className="mt-soon">{tr('wizard.montage.soon')}</span>}
          <span className="chev"><Glyph name="chev" size={16} /></span>
        </button>
        {open[g.id] && (
          g.items.length
            ? items(g.items.map((label) => ({ kind, label })))
            : <p className="mt-lib-note mt-empty"><span className="tx">{tr(kind === 'trans' ? 'wizard.montage.emptyGroupTrans' : 'wizard.montage.emptyGroupStyle')}</span></p>
        )}
      </div>
    );
  });
  const tabs: [LibKind, string, number][] = [['hook', tr('wizard.montage.tabHooks'), HOOK_CATS.reduce((n, c) => n + c.options.length, 0)], ['style', tr('wizard.montage.tabStyles'), STYLES.length], ['trans', tr('wizard.montage.tabTrans'), GLUES.length], ['text', tr('wizard.montage.tabSubs'), SUB_STYLES.length], ['frame', tr('wizard.montage.tabFrames'), frames.length]];
  return (
    <section className="fxt-panel fxt-lib" aria-label={tr('wizard.montage.library')}>
      <div className="fxt-lib-h">
        <h2><span className="tx">{tr('wizard.montage.library')}</span></h2>
        <div className="fxt-tabs" role="tablist">
          {tabs.map(([id, label, count]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              <span className="tx">{label}<span className="c">{count}</span></span>
            </button>
          ))}
        </div>
      </div>
      <div className="fxt-lib-b">
        {tab === 'text' && (
          <>
            <div className="mt-plates" role="radiogroup" aria-label={tr('wizard.montage.subStyleGroup')}>
              {SUB_STYLES.map((s) => (
                <button key={s.id} type="button" role="radio" aria-checked={subStyle === s.name} aria-label={s.name} className="mt-plate" onClick={() => onPickSub(s.name)} data-tip={s.name}>
                  {/* на проде пример стиля — видео (как на шаге «Текст»), в моке — svg; живёт только в видимой области */}
                  {subPreviews[s.name] && <PlateMedia url={subPreviews[s.name]!} />}
                  {subStyle === s.name && <span className="ck"><Glyph name="check" size={14} sw={2.2} /></span>}
                </button>
              ))}
            </div>
            <div className="w12 mt-textcfg">{textCfg}</div>
          </>
        )}
        {tab === 'hook' && HOOK_CATS.map((cat) => (
          <div key={cat.kind} className="fxt-acc" data-open={Boolean(open[cat.kind])}>
            <button type="button" className="fxt-acc-h" aria-expanded={Boolean(open[cat.kind])} onClick={() => setOpen(cat.kind)}>
              <span className={`fxt-ic k-hook sm${activeHookKind === cat.kind ? ' on' : ''}`}><Glyph name={cat.icon} size={14} /></span>
              <span className="name">{fxName(cat.label)}{cat.options.length > 0 && <span className="c">{cat.options.length}</span>}</span>
              {activeHookKind === cat.kind && <span className="was">{tr('wizard.montage.inThisVideo')}</span>}
              <span className="chev"><Glyph name="chev" size={16} /></span>
            </button>
            {open[cat.kind] && (cat.kind === 'warmup'
              ? <div className="fxt-grid">{row({ kind: 'hook', label: tr('wizard.montage.ownWarmup'), hookKind: 'warmup' }, <Ic kind="hook" label="warmup" />, tr('wizard.montage.ownWarmupMeta'), true)}</div>
              : items(cat.options.map((label) => ({ kind: 'hook' as const, label, hookKind: cat.kind }))))}
          </div>
        ))}
        {tab === 'trans' && (
          <>
            <div className="fxt-acc mt-plain">
              <button type="button" className="fxt-acc-h" aria-pressed={used({ kind: 'trans', label: NO_GLUE })} onClick={() => onAdd({ kind: 'trans', label: NO_GLUE })}>
                <span className={`fxt-ic k-trans sm${used({ kind: 'trans', label: NO_GLUE }) ? ' on' : ''}`}><Glyph name="t_none" size={14} /></span>
                <span className="name">{fxName(NO_GLUE)}<span className="c">{metaOf(NO_GLUE, 'trans')}</span></span>
                {used({ kind: 'trans', label: NO_GLUE }) ? <span className="was">{tr('wizard.montage.inThisVideo')}</span> : <span className="mt-plainact">{tr('wizard.montage.onAllCuts')}</span>}
              </button>
            </div>
            {groups(GLUE_GROUPS, 'trans')}
          </>
        )}
        {tab === 'style' && groups(STYLE_GROUPS, 'style')}
        {tab === 'frame' && (
          <>
            {/* плашки — кадр этого ролика с рамкой поверх: видно, как она ляжет именно на него */}
            <div className="mt-plates mt-frames" role="radiogroup" aria-label={tr('wizard.montage.frameGroup')}>
              {[{ id: '', label: tr('wizard.montage.noFrame'), previewUrl: '' }, ...frames].map((f) => {
                const on = (frameId ?? '') === f.id;
                return (
                  <button key={f.id || 'none'} type="button" role="radio" aria-checked={on} aria-label={f.label} className="mt-plate mt-frame-plate" disabled={Boolean(frameNote) && Boolean(f.id)} onClick={() => onPickFrame(f.id || null)} data-tip={f.label}>
                    <span className="mt-frame-base">{frameBase}</span>
                    {f.previewUrl && <img src={f.previewUrl} alt="" draggable={false} />}
                    <span className="nm">{f.label}</span>
                    {on && <span className="ck"><Glyph name="check" size={14} sw={2.2} /></span>}
                  </button>
                );
              })}
            </div>
            {frameNote ? <p className="mt-lib-note"><span className="tx">{frameNote}</span></p> : frameAll && <div className="mt-frame-all">{frameAll}</div>}
          </>
        )}
      </div>
    </section>
  );
});

/* ── окно на кадре/склейке: мини-превью со стрелками + короткий список. Выбор сразу ложится
      в ролик и не закрывает окно — можно сравнить несколько; на чём остановился, то и встало. ── */
const DWELL_MS = 650;
interface PopOption { id: string; label: string; url?: string | null; glyph?: string; group?: string; groupIcon?: string }
function PickPopover({ title, options, current, left, bottom, preview, onApply, footer, stateLabel }: {
  title: string; options: PopOption[]; current: string; left: number; bottom: number;
  preview: (id: string) => ReactNode; onApply: (id: string) => void; footer?: ReactNode; stateLabel: string;
}) {
  const { t: tr } = useTranslation();
  const fxName = useFxName();
  const [shown, setShown] = useState(current);
  const pending = useRef<string | null>(null);
  const timer = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const flush = () => { window.clearTimeout(timer.current); if (pending.current !== null) { onApply(pending.current); pending.current = null; } };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setShown(current); return flush; }, [title]);
  const pick = (id: string) => { window.clearTimeout(timer.current); pending.current = null; setShown(id); if (id !== current) onApply(id); };
  const browse = (dir: 1 | -1) => {
    const i = options.findIndex((o) => o.id === shown);
    const next = options[(i + dir + options.length) % options.length].id;
    if (listRef.current?.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
    setShown(next);
    window.clearTimeout(timer.current);
    pending.current = next;
    timer.current = window.setTimeout(flush, DWELL_MS);
  };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault(); e.stopImmediatePropagation();
      browse(e.key === 'ArrowRight' ? 1 : -1);
    };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  });
  useEffect(() => {
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(`[data-opt="${CSS.escape(shown)}"]`);
    if (!list || !row) return;
    const top = row.offsetTop - list.offsetTop;
    if (top < list.scrollTop) list.scrollTo({ top, behavior: 'smooth' });
    else if (top + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTo({ top: top + row.offsetHeight - list.clientHeight, behavior: 'smooth' });
  }, [shown]);
  const idx = options.findIndex((o) => o.id === shown);
  const settled = shown === current && pending.current === null;
  const cur = options[idx];
  return (
    <div className="fxt-pop fxt-cutpop" role="dialog" aria-label={title} style={{ left, bottom }} onPointerDown={(e) => e.stopPropagation()}>
      <div className="mstage" style={{ width: 169, height: 300 }}>
        {preview(shown)}
        <div className="mtop"><span className="nm tx">{fxName(cur?.label)}{cur?.group && <i className="grp"> · {tr(`wizard.montage.groups.${cur.group}`)}</i>}</span><span className="c tx num">{idx + 1}/{options.length}</span></div>
        <button type="button" className="mnav l" aria-label={tr('wizard.montage.prev')} data-tip={tr('wizard.montage.prevKey')} onClick={() => browse(-1)}><Glyph name="back" size={16} sw={2} /></button>
        <button type="button" className="mnav r" aria-label={tr('wizard.montage.next')} data-tip={tr('wizard.montage.nextKey')} onClick={() => browse(1)}><Glyph name="fwd" size={16} sw={2} /></button>
        <span className={`mstate${settled ? ' on' : ''}`}>{settled && <Glyph name="check" size={12} sw={2} />}<span className="tx">{settled ? stateLabel : tr('wizard.montage.example')}</span></span>
      </div>
      <h3>{title}</h3>
      <div ref={listRef} className="opts" role="listbox" aria-label={title}>
        {options.map((o, i) => (
          <Fragment key={o.id}>
          {o.group && o.group !== options[i - 1]?.group && (
            <div className="fxt-opt-group" role="presentation">
              {o.groupIcon && <Glyph name={o.groupIcon} size={12} />}<span className="tx">{tr(`wizard.montage.groups.${o.group}`)}</span>
            </div>
          )}
          <button type="button" role="option" data-opt={o.id} className={`fxt-opt${o.id === shown && o.id !== current ? ' browsed' : ''}`} aria-selected={o.id === current} aria-pressed={o.id === current} onClick={() => pick(o.id)}>
            {o.url !== undefined ? <Thumb url={o.url} size={26} /> : <Ic kind="trans" label={o.glyph ?? o.label} on={o.id === current} size={26} />}
            <span className="tx">{fxName(o.label)}</span>{o.id === current && <span className="ck"><Glyph name="check" size={16} sw={2} /></span>}
          </button>
          </Fragment>
        ))}
      </div>
      {footer && <div className="foot">{footer}</div>}
    </div>
  );
}


/**
 * onGenerate возвращает причину, если генерировать ещё нельзя: «Пул» с её подсказкой скрыт под столом.
 * busy — генерация уже уходит: кнопки генерации ждут со спиннером, второй батч не запустить.
 */
export function MontageTable({ index, onIndex, onClose, onGenerate, busy = false }: { index: number; onIndex: (i: number) => void; onClose: () => void; onGenerate: () => string | null; busy?: boolean }) {
  const rootRef = useRef<HTMLDivElement>(null);
  useTooltips(rootRef);
  const { t: tr, i18n } = useTranslation();
  const fxName = useFxName();
  const chip = useChip();
  const secs = (v: number) => tr('wizard.montage.secs', { n: fmtSec(v, i18n.language) });
  const clipTitle = (id: string) => tr(id.startsWith('photo-') ? 'wizard.montage.photoN' : 'wizard.montage.clipN', { n: Number(/(\d+)(\.mp4)?$/.exec(id)?.[1] ?? 0) });
  // модальный стол: фокус — внутрь при открытии и обратно на кнопку, что его открыла, при закрытии
  useEffect(() => {
    const back = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    rootRef.current?.focus({ preventScroll: true });
    return () => { if (back?.isConnected) back.focus({ preventScroll: true }); };
  }, []);
  const track = useWizardStore((s) => s.track);
  const timingFrom = useWizardStore((s) => s.timingFrom);
  const timingTo = useWizardStore((s) => s.timingTo);
  const setWTimeline = useWizardStore((s) => s.setTimeline);
  const asr = useWizardStore((s) => s.asr);
  const storyboard = useWizardStore((s) => s.storyboard);
  const setStoryboardVideo = useWizardStore((s) => s.setStoryboardVideo);
  const recipe = useRecipeCuts();
  const combos = useCombos();
  const total = combos.length;
  const combo = combos[Math.min(index, total - 1)];
  const [view, setView] = useState<'table' | 'grid'>('table');
  const montage = useWizardStore((s) => s.montage);
  const fxAll = montage.videos;
  const setMontage = useWizardStore((s) => s.setMontage);
  const strobe = useWizardStore((s) => Boolean(s.background.strobe));

  /* ── время: всё в секундах от начала отрывка ── */
  const start = recipe.window?.start ?? 0;
  const dur = recipe.window ? recipe.window.end - recipe.window.start : 15;
  const drop = recipe.window?.drop != null ? recipe.window.drop - start : null;
  // Хук тянется мышью — пока тянут, дроп живёт здесь (от начала отрывка); в стор он уходит
  // на отпускании, иначе склейки пересчитывались бы на каждый пиксель.
  const [dragDrop, setDragDrop] = useState<number | null>(null);
  const dropView = dragDrop ?? drop;
  const setWizardHooks = useWizardStore((s) => s.setHooks);
  const bpm = recipe.data?.bpm ?? 0;
  const [dragCuts, setDragCuts] = useState<number[] | null>(null);
  const cuts = useMemo(() => dragCuts ?? (recipe.cuts ?? []).map((c) => c - start), [dragCuts, recipe.cuts, start]);
  const bounds = useMemo(() => [0, ...cuts, dur], [cuts, dur]);
  const shots = bounds.length - 1;
  const beats = useMemo(() => (recipe.data?.beats ?? []).map((b) => b - start), [recipe.data, start]);

  /* ── ролики батча: FX-настройка каждого стартует с его варианта из «Пула» ── */
  const defaultsFor = useCallback((c: Combo): VideoFx => {
    const cfg = { ...(c.variant?.config ?? {}) };
    const picked = cfg.effectStyles?.length ? cfg.effectStyles : cfg.effectStyle ? [cfg.effectStyle] : [];
    const dropFrame = drop !== null ? Math.max(1, bounds.findIndex((b) => Math.abs(b - drop) < 0.02)) : shots;
    return {
      sig: c.sig, kind: c.variant?.kind ?? 'none', config: cfg, transitions: { ...(c.variant?.recipe?.transitions ?? {}) },
      styles: picked.slice(0, 2).map((style, k) => ({ uid: k + 1, style, lane: k as 0 | 1, a: 0, b: cfg.effectStyleFull ? shots : dropFrame })),
      sub: c.sub, edited: false
    };
  }, [bounds, drop, shots]);
  const ready = shots > 1 && Boolean(recipe.cuts);
  // Ролик без правок (или с чужой комбинацией — распределение «Пула» поменялось) стартует
  // заново со своего варианта FX. Правки ролика с той же комбинацией не трогаем.
  useEffect(() => {
    if (!ready) return;
    const stale = combos.filter((c) => !fxAll[c.index] || fxAll[c.index].sig !== c.sig || (!fxAll[c.index].edited && fxAll[c.index].styles.some((st) => st.b > shots)));
    const extra = Object.keys(fxAll).map(Number).filter((i) => i >= combos.length);
    // Правленый ролик после смены темпа: кадров стало меньше — окна стилей подрезаем под
    // новые кадры (правки сохраняются), иначе рендер отказал бы «стиль выходит за кадры».
    const clipped = combos.filter((c) => fxAll[c.index]?.edited && fxAll[c.index].sig === c.sig && fxAll[c.index].styles.some((st) => st.b > shots));
    if (stale.length || extra.length || clipped.length) {
      const videos = Object.fromEntries(Object.entries(fxAll).filter(([i]) => Number(i) < combos.length));
      for (const c of stale) videos[c.index] = defaultsFor(c);
      for (const c of clipped) {
        const v = videos[c.index];
        videos[c.index] = { ...v, styles: v.styles.filter((st) => st.a < shots).map((st) => ({ ...st, b: Math.min(st.b, shots) })) };
      }
      setMontage({ videos });
    }
  }, [ready, combos, fxAll, defaultsFor, setMontage, shots]);
  const fxOf = (i: number) => (fxAll[i]?.sig === combos[i]?.sig ? fxAll[i] : undefined) ?? defaultsFor(combos[i]);
  const vfx = fxOf(combo.index);
  const allIdx = combos.map((c) => c.index);
  /*
   * Правка роликов. Запись ролика стол засевает сам, только когда склейки готовы и кадров
   * больше одного, — правка до этого раньше молча пропадала (под тостом «готово»). Поэтому
   * запись засевается здесь же из варианта ролика; стор читается свежим, а не из рендера.
   */
  const editVideos = (indices: number[], fn: (v: VideoFx) => VideoFx) => {
    const cur = useWizardStore.getState().montage.videos;
    const videos = { ...cur };
    for (const i of indices) {
      const c = combos[i];
      if (!c) continue;
      videos[i] = { ...fn(cur[i]?.sig === c.sig ? cur[i] : defaultsFor(c)), edited: true };
    }
    setMontage({ videos });
  };
  /** Зеркало проверок montage.py: что бэк отвергнет при генерации (422), «Во все N» туда не ставит. */
  const rejects = (c: Combo, v: VideoFx) => {
    if (v.kind !== 'none' && (!c.hookAllowed || drop === null)) return true;
    const labels = cuts.map((_, i) => v.transitions[i] ?? v.config.effectGlue ?? NO_GLUE);
    // своё видео — один переход на весь ролик; статичный цвет склеек не имеет вовсе
    if (c.bgKey?.startsWith('upload:') && new Set(labels).size > 1) return true;
    if (c.bgKey === '__color__' && !strobe && labels.some((l) => l !== NO_GLUE)) return true;
    // рамка нарисована под 9:16 — на 16:9 бэк её отвергает (montage.py)
    if (v.frame && !c.vertical) return true;
    return false;
  };

  const kind = vfx.kind;
  const config = vfx.config;
  const activeHookLabel = hookLabel(kind, config);
  const defaultGlue = config.effectGlue ?? NO_GLUE;
  const transitionAtFor = (v: VideoFx) => (i: number) => v.transitions[i] ?? v.config.effectGlue ?? NO_GLUE;
  const transitionAt = transitionAtFor(vfx);
  const hookRangeFor = (v: VideoFx) => { const l = hookLabel(v.kind, v.config); return v.kind !== 'none' && dropView !== null && l ? hookSpan(v.kind, v.config, dropView, dur, bpm, bounds) : null; };
  /*
   * Тайминг хука = дроп: все хук-эффекты в рендере считаются от него (`user_drop_t`). Поэтому
   * хук на столе двигает сам дроп — общий для батча, как и на шаге FX; склейки под новый дроп
   * пересчитываются. Края — по полсекунды от краёв отрывка: рендер требует дроп внутри него.
   */
  const DROP_EDGE = 0.5;
  const commitDrop = (next: number) => {
    if (drop === null) return;
    const v = Math.round(clamp(next, DROP_EDGE, dur - DROP_EDGE) * 100) / 100;
    if (Math.abs(v - drop) < 0.005) return;
    // дроп общий для батча и двигает склейки — в историю идут все ролики, склейки и сам дроп
    remember(allIdx, { timeline: true, drop: true });
    setWizardHooks({ dropTime: secondsToDropTime(start + v) });
    say(tr('wizard.montage.dropMoved', { from: tc(start + drop), to: tc(start + v) }));
  };
  const hookRange = hookRangeFor(vfx);
  const fxEdit = (fn: (v: VideoFx) => VideoFx) => editVideos([combo.index], fn);
  const setHooks = (patch: { kind?: HookKind; config?: Partial<HookConfig> }) => fxEdit((v) => ({
    ...v, kind: patch.kind ?? v.kind,
    config: patch.kind && patch.kind !== v.kind ? { ...(patch.config ?? {}) } as HookConfig : { ...v.config, ...patch.config }
  }));
  const setTimeline = (patch: Partial<TimelineRecipe>) => {
    const { transitions, styles, ...rest } = patch;
    if (Object.keys(rest).length) setWTimeline(rest);
    if (transitions || styles) fxEdit((v) => ({ ...v, ...(transitions ? { transitions } : {}), ...(styles ? { styles: styles.map((s) => ({ ...s })) } : {}) }));
  };

  /* ── исходники ролика: раскадровка «Пула» (одна на оба экрана) ── */
  const sbVideo = combo.group ? storyboard.videos[combo.slotIndex] : undefined;
  const framesOfCombo = useFramesOf(shots);
  const clipsOf = framesOfCombo;
  const clips = framesOfCombo(combo);
  // статичный цвет (без стробоскопа) склеек в рендере не имеет — переход на нём бэк отвергнет
  // при генерации (montage.py), поэтому склейки-переходы на таком ролике не показываем вовсе
  const staticColor = clips.length > 0 && clips.every((c) => c.color && !c.strobe);
  // Пример пункта библиотеки: настоящий рендер из каталога эффектов (как на шаге FX), а если его
  // нет — тот же эффект на кадрах этого ролика (переход — на первой склейке, хук — у дропа).
  const previewOf = (item: LibItem): LibPreview => {
    const id = item.kind === 'trans' ? previewIdFor('effectGlue', item.label)
      : item.kind === 'style' ? previewIdFor('effectStyle', item.label)
        : item.kind === 'hook' && item.hookKind === 'object' ? previewIdFor('object', item.label)
          : item.kind === 'hook' && item.hookKind === 'motion' ? previewIdFor('motion', item.label)
            : item.kind === 'hook' && item.hookKind === 'effects' ? previewIdFor('effectHook', item.label) : undefined;
    if (id && fxPreviews.isPending) return { state: 'loading' };
    if (id && fxPreviews.isError) return { state: 'error' };
    const url = id ? fxPreviews.data?.previews.find((p) => p.id === id)?.previewUrl : undefined;
    if (url) return { url };
    const none: StageFx = { transitionAt: () => NO_GLUE, styles: [], hookRange: null };
    // симуляция — только там, где стол сам рисует эффект; иначе честно «примера нет»
    if (item.kind === 'trans' && TRANSITION_ANIM[item.label]) {
      const at = cuts[0] ?? Math.min(dur / 2, 1.5);
      return { sim: ({ w, h }, paused) => <LoopStage frames={clips} bounds={bounds} at={at} dur={dur} w={w} h={h} paused={paused} fx={{ ...none, transitionAt: () => item.label }} /> };
    }
    if (item.kind === 'style' && styleFilter([item.label], 0)) {
      const style: TimelineStyleRange = { uid: -1, style: item.label, lane: 0, a: 0, b: Math.max(1, bounds.length - 1) };
      return { sim: ({ w, h }, paused) => <LoopStage frames={clips} bounds={bounds} at={Math.min(1, dur / 2)} lead={1} span={Math.min(2.5, dur)} dur={dur} w={w} h={h} paused={paused} fx={{ ...none, styles: [style] }} /> };
    }
    const cat = HOOK_CATS.find((c) => c.kind === item.hookKind);
    const simHook = cat?.kind === 'motion' || cat?.kind === 'thought' || (cat?.kind === 'effects' && item.label === 'Молния');
    if (item.kind === 'hook' && cat?.key && drop !== null && simHook) {
      const config = { [cat.key]: item.label } as HookConfig;
      const range = hookSpan(cat.kind, config, drop, dur, bpm, bounds);
      return { sim: ({ w, h }, paused) => <LoopStage frames={clips} bounds={bounds} at={drop} lead={0.8} span={2} dur={dur} w={w} h={h} paused={paused} fx={{ ...none, hookKind: cat.kind, hookLabel: item.label, hookRange: range }} /> };
    }
    return {};
  };
  const unpin = (k: number) => { if (!sbVideo) return; const { [k]: _gone, ...pins } = sbVideo.pins; setStoryboardVideo({ ...sbVideo, pins }); };
  const markEdited = () => editVideos([combo.index], (v) => v);

  /* ── субтитры: слова примерки ── */
  const subs = useMemo(() => {
    const words = asr.status === 'COMPLETED' ? asr.words : [];
    return words.map((w, idx) => ({ a: w.tStart - start, b: w.tEnd - start, text: w.text, focus: Boolean(w.focus), idx })).filter((s) => s.b > 0 && s.a < dur);
  }, [asr, start, dur]);
  const subStyleId = (name?: string) => SUB_STYLES.find((s) => s.name === name)?.id;
  const subCatalog = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles, staleTime: 5 * 60_000 });
  const subPreviews = useMemo(() => Object.fromEntries((subCatalog.data?.styles ?? []).map((st) => [st.name, st.previewUrl])), [subCatalog.data]);
  const setAsrWord = useWizardStore((s) => s.setAsrWord);
  const toggleAsrFocus = useWizardStore((s) => s.toggleAsrFocus);
  const subtitles = useWizardStore((s) => s.subtitles);
  const setSubtitles = useWizardStore((s) => s.setSubtitles);
  const lyrics = useWizardStore((s) => s.lyrics);
  const fragmentLyrics = useWizardStore((s) => s.fragmentLyrics);
  const timedSubs = useMemo(() => subs.map((w) => ({ text: w.text, start: w.a, end: w.b, focus: w.focus })), [subs]);
  const subProps = (name: string | undefined, wide: boolean): SubProps | undefined => {
    const style = styleIdOf(name ?? '');
    if (!subStyleId(name) || !name || !style) return undefined;
    return { style, settings: textSettingsFor(subtitles, name), color: subtitles.color, words: timedSubs, lyrics: fragmentLyrics.trim() || lyrics.trim() || undefined, wide };
  };
  // Конфигуратор текста правит стиль этого ролика: вкладка шага «Текст» = стиль ролика
  // примеры эффектов для библиотеки (тот же каталог отрендеренных образцов, что на шаге FX)
  const fxPreviews = useQuery({ queryKey: ['fx-previews'], queryFn: api.fxPreviews, staleTime: 30 * 60_000 });
  const framesQuery = useQuery({ queryKey: ['wizard-frames'], queryFn: api.frames, staleTime: 30 * 60_000 });
  // подпись рамки — на языке интерфейса: бэк отдаёт обе (label — RU, labelEn — EN)
  const ruUi = i18n.language.startsWith('ru');
  const frameCatalog = useMemo(() => (framesQuery.data?.frames ?? []).map((f) => ({ ...f, label: ruUi ? f.label : f.labelEn })), [framesQuery.data, ruUi]);
  const frameUrlOf = (id?: string | null) => (id ? frameCatalog.find((f) => f.id === id)?.previewUrl ?? null : null);
  const pickSub = (name: string) => {
    remember();
    fxEdit((v) => ({ ...v, sub: name }));
    setSubtitles({ pool: subtitles.pool.includes(name) ? subtitles.pool : [...subtitles.pool, name], textTab: name });
  };
  useEffect(() => { if (vfx.sub && subtitles.textTab !== vfx.sub && subtitles.pool.includes(vfx.sub)) setSubtitles({ textTab: vfx.sub }); }, [vfx.sub, subtitles.textTab, subtitles.pool, setSubtitles]);

  /* ── состояние экрана ── */
  const [tab, setTab] = useState<LibKind>('hook');
  // Группы библиотеки стартуют свёрнутыми: стол открывается обзором, а не одной группой
  // хуков; группа, что уже стоит в ролике, видна по метке «в этом ролике».
  const [open, setOpenState] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useState<Sel>(null);
  const [t, setT] = useState(0);
  const tRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [snap, setSnap] = useState(true);
  const [pop, setPop] = useState<{ type: 'cut' | 'frame'; i: number; x: number; y: number } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [keysOpen, setKeysOpen] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);
  const [lane2Open, setLane2Open] = useState(false);
  /*
   * Телефон — раскладка по CapCut: превью сверху, под ним время и плей, таймлайн с неподвижной
   * линией по центру (листаешь дорожки — двигается время), внизу панель инструментов; у
   * выбранного элемента — свои действия, библиотека открывается шторкой. Логика правок та же.
   */
  const phone = usePhone();
  const [sheet, setSheet] = useState<null | 'hook' | 'trans' | 'style' | 'text' | 'frame' | 'pace'>(null);
  const [dockReq, setDockReq] = useState<{ kind: 'edit' | 'shuffle'; n: number } | null>(null);
  const askDock = (kind: 'edit' | 'shuffle') => setDockReq((r) => ({ kind, n: (r?.n ?? 0) + 1 }));
  /** телефон: панель замены кадра встаёт на место нижней панели инструментов */
  const [replaceSlot, setReplaceSlot] = useState<HTMLElement | null>(null);
  // лента инструментов шире экрана — затухание у края показывает, что её можно листать
  const barRef = useRef<HTMLElement | null>(null);
  const [barFade, setBarFade] = useState({ l: false, r: false });
  const syncBar = useCallback(() => {
    const el = barRef.current;
    if (!el) return;
    const l = el.scrollLeft > 4; const r = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setBarFade((cur) => (cur.l === l && cur.r === r ? cur : { l, r }));
  }, []);
  // Стабильный ref: колбэк, объявленный прямо в разметке, React вызывает на КАЖДОМ рендере, а он
  // ставил состояние — на телефоне это давало «Maximum update depth exceeded» (Стиль → Esc → Хук).
  const barRefCb = useCallback((el: HTMLElement | null) => { barRef.current = el; }, []);
  // разовая подсказка, как работать с таймлайном на телефоне
  const [mobIntroDismissed, setMobIntroDismissed] = useGuideDismiss('table-mobile-intro', phone);
  // шторка открывается сразу с плитками: если в ней ничего не раскрыто — раскрываем первую
  // непустую группу (на телефоне лишний тап по заголовку группы — это лишний шаг)
  useEffect(() => {
    if (!sheet || sheet === 'pace' || sheet === 'text' || sheet === 'frame') return;
    const ids = sheet === 'hook' ? HOOK_CATS.filter((c) => c.options.length).map((c) => c.kind as string)
      : (sheet === 'trans' ? GLUE_GROUPS : STYLE_GROUPS).filter((g) => g.items.length).map((g) => g.id);
    setOpenState((cur) => (ids.some((id) => cur[id]) || !ids.length ? cur : { ...cur, [ids[0]]: true }));
  }, [sheet]);
  // Исходники в два шага: вайб в библиотеке → кадры ролика справа (механика раскадровки «Пула»)
  const [editK, setEditK] = useState<number | null>(null);
  // края пересчитываем, когда лента появилась (её подменяет панель замены кадра) и когда меняется
  // её размер; прокрутка — через onScroll. Наблюдатель срабатывает вне рендера — цикла не будет.
  useEffect(() => {
    const el = barRef.current;
    if (!el) return undefined;
    syncBar();
    const ro = new ResizeObserver(syncBar);
    ro.observe(el);
    return () => ro.disconnect();
  }, [editK, phone, syncBar]);
  const dockRef = useRef<HTMLDivElement>(null);
  // Тур стола (общая память подсказок): кадры в превью → дорожки ролика.
  const [framesGuideDismissed, setFramesGuideDismissed] = useGuideDismiss('table-frames', false);
  const [lanesGuideDismissed, setLanesGuideDismissed] = useGuideDismiss('table-lanes', false);
  useEffect(() => {
    if (editK === null) return undefined;
    setPlaying(false);
    let raf = 0; const t0 = performance.now(); const a = bounds[editK] ?? 0; const len = Math.max(0.3, (bounds[editK + 1] ?? a + 1) - a);
    const tick = (now: number) => { const x = a + ((now - t0) / 1000) % len; tRef.current = x; setT(x); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [editK, bounds]);
  const cfgRef = useRef<HTMLDivElement>(null);
  const toastTimer = useRef(0);
  const say = useCallback((msg: string) => { setToast(msg); window.clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(null), 2400); }, []);
  const seek = useCallback((v: number) => {
    const x = clamp(v, 0, dur - 1 / FPS); tRef.current = x; setT(x);
    const audio = audioRef.current;
    if (audio) { try { audio.currentTime = start + x; } catch { /* ещё не загрузился */ } }
  }, [dur, start]);
  // Другой ролик — выбор и окна относятся к прошлому; время и игра сохраняются.
  useEffect(() => { setSel(null); setPop(null); }, [index]);
  // Темп или склейки сменились — окно и выбор склейки указывали на старый индекс (за последней
  // склейкой превью было NaN, а переход ложился на несуществующий стык).
  const cutCount = cuts.length;
  useEffect(() => { setPop(null); }, [recipe.pace, cutCount]);
  useEffect(() => {
    if ((sel?.type === 'cut' && sel.i >= cutCount) || (sel?.type === 'frame' && sel.i >= shots)) setSel(null);
  }, [sel, cutCount, shots]);

  /*
   * ── история: своя у каждого ролика ──
   * Раньше одна история на весь батч: правка ролика 1, «]», Ctrl+Z — и переходы ролика 1
   * записывались в ролик 2. Теперь шаг помнит, какие ролики он менял, и возвращает только их;
   * общие правки (дроп, темп, «Во все N») лежат в истории ролика, где их сделали.
   * Такой шаг откатывает другой ролик (склейки, дроп), только если его не правили позже, —
   * иначе снимок «до» стёр бы новые правки; пропущенное стол называет в тосте (history.ts).
   * Отдельная история батча этого не решила бы: откат общего шага всё равно ложился бы
   * поверх более поздних правок роликов.
   */
  const hist = useRef<Record<number, { past: Snapshot[]; future: Snapshot[] }>>({});
  const ledger = useRef(createLedger());
  const [, bump] = useState(0);
  const stackOf = (i: number) => (hist.current[i] ??= { past: [], future: [] });
  const capture = (indices: number[], opts: { timeline?: boolean; drop?: boolean } = {}): Snapshot => {
    const st = useWizardStore.getState();
    const { key, pace, cuts: tlCuts, edited } = st.timeline;
    return {
      videos: Object.fromEntries(indices.map((i) => [i, st.montage.videos[i] ?? null])),
      ...(opts.timeline ? { timeline: { key, pace, cuts: tlCuts, edited } } : {}),
      ...(opts.drop ? { dropTime: st.hooks.dropTime } : {})
    };
  };
  const pushHistory = (snap: Snapshot) => {
    const h = stackOf(combo.index);
    h.past.push({ ...snap, ...ledgerPush(ledger.current, keysOf(snap)) });
    if (h.past.length > 80) h.past.shift();
    h.future = [];
    bump((n) => n + 1);
  };
  const remember = (indices: number[] = [combo.index], opts: { timeline?: boolean; drop?: boolean } = {}) => pushHistory(capture(indices, opts));
  const restore = (snap: Snapshot) => {
    const videos = { ...useWizardStore.getState().montage.videos };
    for (const [k, v] of Object.entries(snap.videos)) { if (v) videos[Number(k)] = v; else delete videos[Number(k)]; }
    setMontage({ videos });
    // дроп раньше склеек: склейки сверяются с ключом рецепта, а он считается от дропа
    if ('dropTime' in snap) setWizardHooks({ dropTime: snap.dropTime });
    if (snap.timeline) setWTimeline(snap.timeline);
  };
  /** Что шаг не тронул: ролики по номерам, склейки и дроп — одной строкой. */
  const sayHistorySkipped = (skipped: HistKey[], nothing: boolean) => {
    if (!skipped.length) return;
    const names = skipped.filter((k) => k.startsWith('v')).map((k) => tr('wizard.montage.historySkipVideo', { n: Number(k.slice(1)) + 1 }));
    if (skipped.some((k) => !k.startsWith('v'))) names.push(tr('wizard.montage.historySkipCuts'));
    say(tr(nothing ? 'wizard.montage.historySkippedAll' : 'wizard.montage.historySkipped', { list: names.join(', ') }));
  };
  // отменённое ложится в «вернуть» только по тем ключам, что реально откатились
  const undo = () => {
    const h = stackOf(combo.index); const snap = h.past.pop();
    if (!snap || snap.seq === undefined) return;
    const { ok, skipped } = ledgerUndo(ledger.current, snap.seq, keysOf(snap));
    if (ok.length) {
      const now = capture(Object.keys(snap.videos).map(Number).filter((i) => ok.includes(`v${i}`)), { timeline: ok.includes('tl'), drop: ok.includes('drop') });
      h.future.push({ ...now, seq: snap.seq, prev: snap.prev });
      restore(pickKeys(snap, ok));
    }
    sayHistorySkipped(skipped, !ok.length);
    setSel(null); setPop(null); bump((n) => n + 1);
  };
  const redo = () => {
    const h = stackOf(combo.index); const snap = h.future.pop();
    if (!snap || snap.seq === undefined) return;
    const { ok, skipped } = ledgerRedo(ledger.current, snap.seq, snap.prev ?? {}, keysOf(snap));
    if (ok.length) {
      const now = capture(Object.keys(snap.videos).map(Number).filter((i) => ok.includes(`v${i}`)), { timeline: ok.includes('tl'), drop: ok.includes('drop') });
      h.past.push({ ...now, seq: snap.seq, prev: snap.prev });
      restore(pickKeys(snap, ok));
    }
    sayHistorySkipped(skipped, !ok.length);
    setSel(null); setPop(null); bump((n) => n + 1);
  };
  const canUndo = (hist.current[combo.index]?.past.length ?? 0) > 0;
  const canRedo = (hist.current[combo.index]?.future.length ?? 0) > 0;

  /* ── звук отрывка ── */
  const audioUrl = usePlaybackUrl(track);
  // волна отрывка на дорожке «Биты»: громкость с сервера (трек не качается ради неё отдельно),
  // ~20 столбиков в секунду, при зуме они растягиваются
  const trackPeaks = useTrackPeaks(track);
  const wavePeaksSeg = useMemo(() => (trackPeaks ? peakLevels(trackPeaks, start, start + dur, Math.max(32, Math.round(dur * 20))) : null), [trackPeaks, start, dur]);
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

  // звук ещё качается: время стоит, на кнопке плея — загрузка (а не «мёртвая» кнопка)
  const [buffering, setBuffering] = useState(false);
  const bufRef = useRef(false);

  /* ── транспорт: в конце отрывка — снова с начала ── */
  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0; let last = 0;
    const tick = (ts: number) => {
      const audio = audioRef.current;
      const waiting = Boolean(audio && !audio.paused && audio.readyState < 3);
      if (waiting !== bufRef.current) { bufRef.current = waiting; setBuffering(waiting); }
      if (waiting) { last = ts; raf = requestAnimationFrame(tick); return; }
      let next: number;
      if (audio && !audio.paused && audio.readyState >= 2) next = audio.currentTime - start;
      else { const dt = last ? (ts - last) / 1000 : 0; next = tRef.current + dt; }
      last = ts;
      if (next >= dur) {
        next = 0;
        if (audio) audio.currentTime = start;
      }
      tRef.current = next;
      setT(next);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); if (bufRef.current) { bufRef.current = false; setBuffering(false); } };
  }, [playing, dur, start]);

  /* ── геометрия ── */
  const mainRef = useRef<HTMLElement>(null);
  const tlRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ w: 360, h: 640 });
  const [mainSize, setMainSize] = useState({ w: 1200, h: 800 });
  const [scrollW, setScrollW] = useState(800);
  const pvRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return undefined;
    const fit = () => {
      const pv = pvRef.current;
      if (phone && pv) {
        const w = Math.min(pv.clientWidth, Math.floor(pv.clientHeight * 9 / 16));
        setStageSize({ w, h: Math.round(w * 16 / 9) });
      } else {
        const H = main.clientHeight - 28;
        setStageSize({ w: Math.round(H * 9 / 16), h: H });
      }
      setMainSize({ w: main.clientWidth, h: main.clientHeight });
      if (scrollRef.current) setScrollW(scrollRef.current.clientWidth);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(main); if (tlRef.current) ro.observe(tlRef.current); if (pvRef.current) ro.observe(pvRef.current);
    return () => ro.disconnect();
  }, [view, phone]);
  // телефон: ноль времени стоит под центральной линией, на экране ~6 секунд (масштаб — щипком)
  const x0 = phone ? Math.round(scrollW / 2) : X0;
  const pps = phone ? Math.max(8, scrollW / 6) * zoom : Math.max(10, (scrollW - X0 * 2) / dur) * zoom;
  const tx = (s: number) => x0 + s * pps;
  const xt = (x: number) => (x - x0) / pps;
  const canvasW = tx(dur) + x0;
  const frameAt = (v: number) => frameIndex(bounds, v);
  const fNow = frameAt(t);

  // Прокрутка дорожек и время связаны в обе стороны: играет ролик — дорожки едут под линией;
  // палец листает дорожки — время идёт за ними (ролик при этом встаёт на паузу, как в CapCut).
  const ownScroll = useRef(0);
  useLayoutEffect(() => {
    if (!phone) return;
    const el = scrollRef.current;
    if (!el) return;
    const want = t * pps;
    if (Math.abs(el.scrollLeft - want) > 1) { ownScroll.current = performance.now(); el.scrollLeft = want; }
  }, [phone, t, pps, canvasW, view]);
  const onPhoneScroll = () => {
    const el = scrollRef.current;
    if (!el || performance.now() - ownScroll.current < 80) return;
    if (playing) setPlaying(false);
    const v = clamp(el.scrollLeft / pps, 0, dur - 1 / FPS);
    if (Math.abs(v - tRef.current) > 1e-3) seek(v);
  };
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  useEffect(() => {
    const el = scrollRef.current;
    if (!phone || !el) return undefined;
    let d0 = 0; let z0 = 1;
    const dist = (e: TouchEvent) => Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    const startT = (e: TouchEvent) => { if (e.touches.length === 2) { d0 = dist(e); z0 = zoomRef.current; } };
    const moveT = (e: TouchEvent) => { if (e.touches.length === 2 && d0) { e.preventDefault(); setZoom(clamp(z0 * dist(e) / d0, 0.4, 5)); } };
    const endT = (e: TouchEvent) => { if (e.touches.length < 2) d0 = 0; };
    el.addEventListener('touchstart', startT, { passive: true });
    el.addEventListener('touchmove', moveT, { passive: false });
    el.addEventListener('touchend', endT);
    return () => { el.removeEventListener('touchstart', startT); el.removeEventListener('touchmove', moveT); el.removeEventListener('touchend', endT); };
  }, [phone, view]);

  /* ── стили на дорожках ── */
  const styles = vfx.styles;
  const styleFree = (lane: 0 | 1, a: number, b: number, except?: number) => !styles.some((o) => o.uid !== except && o.lane === lane && a < o.b && b > o.a);
  const setStyles = (next: TimelineStyleRange[]) => setTimeline({ styles: next });
  const addStyle = (label: string, frame: number, lanePref: 0 | 1 = 0) => {
    for (const lane of [lanePref, (1 - lanePref) as 0 | 1]) {
      if (styleFree(lane, frame, frame + 1)) {
        remember();
        const uid = Math.max(0, ...styles.map((s) => s.uid)) + 1;
        setStyles([...styles, { uid, style: label, lane, a: frame, b: frame + 1 }]);
        setSel({ type: 'style', uid });
        say(tr('wizard.montage.styleAdded', { name: fxName(label), n: frame + 1 }));
        return;
      }
    }
    say(tr('wizard.montage.twoStylesMax'));
  };
  const noCutsNote = () => say(tr('wizard.montage.staticNoCuts'));
  const setTransition = (i: number, label: string) => { if (staticColor && label !== NO_GLUE) { noCutsNote(); return; } remember(); setTimeline({ transitions: { ...vfx.transitions, [i]: label } }); };
  const popEdited = useRef<number | null>(null);
  const setCutTransition = (i: number, label: string) => {
    if (popEdited.current !== i) { remember(); popEdited.current = i; }
    const cur = useWizardStore.getState().montage.videos[combo.index]?.transitions ?? {};
    setTimeline({ transitions: { ...cur, [i]: label } });
  };
  const setTransitionAll = (label: string) => {
    if (staticColor && label !== NO_GLUE) { noCutsNote(); return; }
    remember();
    setTimeline({ transitions: Object.fromEntries(cuts.map((_, i) => [i, label])) });
    setHooks({ config: { effectGlue: label } });
    say(tr('wizard.montage.onAllCutsToast', { name: fxName(label) }));
  };
  const setSub = (name: string) => { pickSub(name); say(tr('wizard.montage.subPicked', { name })); };
  const frameLabel = (id: string | null) => (id ? frameCatalog.find((f) => f.id === id)?.label ?? id : tr('wizard.montage.noFrameLower'));
  const pickFrame = (id: string | null) => {
    if (id && !combo.vertical) { say(tr('wizard.montage.frameVerticalOnly')); return; }
    remember();
    fxEdit((v) => ({ ...v, frame: id }));
    say(tr('wizard.montage.framePicked', { name: frameLabel(id) }));
  };
  // «Во все N»: только туда, где бэк правку примет, — и честно сколько из батча её получили
  const toAll = (fn: (v: VideoFx) => VideoFx, msg: string, only?: number[]) => {
    const cur = useWizardStore.getState().montage.videos;
    const ok = (only ?? allIdx).filter((i) => {
      const c = combos[i];
      return c && !rejects(c, fn(cur[i]?.sig === c.sig ? cur[i] : defaultsFor(c)));
    });
    if (!ok.length) { say(tr('wizard.montage.noneEligible')); return; }
    remember(ok);
    editVideos(ok, fn);
    say(ok.length === total ? tr('wizard.montage.appliedAll', { msg, n: total }) : tr('wizard.montage.appliedSome', { msg, k: ok.length, n: total }));
  };
  /** Хук ролика целиком (тип и его поля) поверх ролика v — склейка и стили v остаются его. */
  const withHookOf = (src: VideoFx) => (v: VideoFx): VideoFx => {
    const own = Object.fromEntries(Object.entries(v.config).filter(([k]) => !HOOK_FIELDS.includes(k as keyof HookConfig)));
    const hook = Object.fromEntries(HOOK_FIELDS.filter((k) => src.config[k] !== undefined).map((k) => [k, src.config[k]]));
    return { ...v, kind: src.kind, config: { ...own, ...hook } as HookConfig };
  };
  // Хук рендер собирает только на вертикальном видео: фото, цвет и 16:9 его не принимают
  const hookIdx = combos.filter((c) => c.hookAllowed).map((c) => c.index);
  // рамка нарисована под 9:16 — «Во все» ставит её только вертикальным роликам
  const verticalIdx = combos.filter((c) => c.vertical).map((c) => c.index);
  const allPill = (onClick: () => void, count = total) => count > 1 ? <button type="button" className="fxt-pill mt-all" data-tip={tr('wizard.montage.allTip', { n: count })} onClick={onClick}><span className="tx">{tr('wizard.montage.allN', { n: count })}</span></button> : null;
  const addHook = (item: LibItem) => {
    const cat = HOOK_CATS.find((c) => c.kind === item.hookKind);
    if (!cat?.key) return;
    if (drop === null) { say(tr('wizard.montage.needDrop')); return; }
    if (!combo.hookAllowed) { say(tr('wizard.montage.hookVerticalOnly')); return; }
    const prev = activeHookLabel && activeHookLabel !== item.label ? activeHookLabel : null;
    const carry: Partial<HookConfig> = kind === cat.kind ? {} : { effectGlue: config.effectGlue, effectStyle: config.effectStyle, effectStyles: config.effectStyles };
    remember();
    setHooks({ kind: cat.kind, config: { ...carry, [cat.key]: item.label } as Partial<HookConfig> });
    setSel({ type: 'hook' });
    say(prev ? tr('wizard.montage.hookReplaced', { from: fxName(prev), to: fxName(item.label) }) : tr('wizard.montage.hookPlaced', { name: fxName(item.label), at: tc(drop) }));
  };
  const targetFrame = () => (sel?.type === 'frame' ? sel.i : frameAt(tRef.current));
  const addFromLib = (item: LibItem) => {
    if (item.kind === 'text') return setSub(item.label);
    if (item.kind === 'hook') return addHook(item);
    if (item.kind === 'style') return addStyle(item.label, targetFrame());
    if (sel?.type === 'cut') { setTransition(sel.i, item.label); say(tr('wizard.montage.cutSet', { n: sel.i + 1, name: fxName(item.label) })); } else setTransitionAll(item.label);
  };
  const del = () => {
    if (!sel) return;
    if (sel.type === 'hook' && kind !== 'none') { remember(); setHooks({ kind: 'none', config: { effectGlue: config.effectGlue, effectStyles: config.effectStyles, effectStyle: config.effectStyle } }); say(tr('wizard.montage.hookRemoved')); }
    else if (sel.type === 'style') { remember(); setStyles(styles.filter((s) => s.uid !== sel.uid)); }
    else if (sel.type === 'cut') setTransition(sel.i, NO_GLUE);
    else if (sel.type === 'frame' && sbVideo?.pins[sel.i]) { unpin(sel.i); say(tr('wizard.montage.shotUnpinned', { n: sel.i + 1 })); return; }
    else return;
    setSel(null);
  };

  /* ── drag: склейки, стили, библиотека ── */
  const cvRef = useRef<HTMLDivElement>(null);
  const laneRefs = useRef<Record<string, HTMLDivElement | null>>({});
  // snap — состояние до перетаскивания: в историю оно ложится на первом реальном сдвиге,
  // а простой клик-выбор историю не трогает и «Вернуть» не сбрасывает
  type Undoable = { snap: Snapshot; saved: boolean };
  const drag = useRef<null | { kind: 'scrub' } | ({ kind: 'cut'; i: number; cuts: number[] } & Undoable) | ({ kind: 'sedge'; uid: number; side: 'l' | 'r' } & Undoable) | ({ kind: 'smove'; uid: number; grab: number } & Undoable) | { kind: 'hookbody'; x0: number; moved: boolean } | ({ kind: 'hookedge' } & Undoable) | { kind: 'lib'; item: LibItem; x0: number; y0: number; live: boolean } | { kind: 'word'; i: number; mode: 'move' | 'l' | 'r'; grab: number; a0: number; b0: number }>(null);
  const saveOnce = (d: Undoable) => { if (!d.saved) { d.saved = true; pushHistory(d.snap); } };
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
    if (item.kind === 'src') { const f = frameAt(v); return { lane: 'frames', a: bounds[f], b: bounds[f + 1], label: tr('wizard.montage.placeShot', { n: f + 1 }) }; }
    if (item.kind === 'text') return { lane: 'subs', a: 0, b: dur, label: tr('wizard.montage.placeSubs') };
    if (item.kind === 'hook') { if (drop === null) return null; const cat = HOOK_CATS.find((c) => c.kind === item.hookKind); if (!cat?.key) return null; const span = hookSpan(cat.kind, { ...config, [cat.key]: item.label }, drop, dur, bpm, bounds); return span ? { lane: 'hook', a: span[0], b: span[1], label: tr('wizard.montage.placeDrop') } : null; }
    if (item.kind === 'style') { const f = frameAt(v); const pref: 0 | 1 = ln === 's1' ? 1 : 0; for (const L of [pref, (1 - pref) as 0 | 1]) if (styleFree(L, f, f + 1)) return { lane: `s${L}`, a: bounds[f], b: bounds[f + 1] }; return { lane: `s${pref}`, a: bounds[f], b: bounds[f + 1], bad: true, label: tr('wizard.montage.placeTwoStyles') }; }
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
      if (d.kind === 'word') {
        // слово не наезжает на соседей и не уходит за отрывок; короче 0,12 с не бывает
        const w = subs[d.i]; const lo = (subs[d.i - 1]?.b ?? 0) + 0.02; const hi = (subs[d.i + 1]?.a ?? dur) - 0.02;
        const v = xt(cvX(e.clientX)); let a = d.a0; let b = d.b0;
        if (d.mode === 'move') { const len = d.b0 - d.a0; a = clamp(v - d.grab, lo, hi - len); b = a + len; }
        else if (d.mode === 'l') a = clamp(v, lo, d.b0 - 0.12);
        else b = clamp(v, d.a0 + 0.12, hi);
        const r3 = (x: number) => Math.round((start + x) * 1000) / 1000;
        if (Math.abs(a - w.a) > 1e-3 || Math.abs(b - w.b) > 1e-3) setAsrWord(w.idx, { tStart: r3(a), tEnd: r3(b) });
        return;
      }
      if (d.kind === 'cut') {
        const b = [0, ...d.cuts, dur]; const lo = b[d.i] + 0.4; const hi = b[d.i + 2] - 0.4;
        const r = snapT(xt(cvX(e.clientX))); const v = clamp(r.v, lo, hi);
        const next = [...d.cuts]; next[d.i] = Math.round(v * 1000) / 1000;
        if (next[d.i] !== d.cuts[d.i]) saveOnce(d);
        d.cuts = next; setDragCuts(next);
        setSnapLine(r.snapped && v > lo && v < hi ? v : null); return;
      }
      if (d.kind === 'hookedge') {
        if (drop === null) return;
        const v = xt(cvX(e.clientX));
        let best: SlowExtend = '';
        for (const [opt] of SLOW_EXTENDS) if (Math.abs(slowShutterEnd(opt, drop, dur, bounds) - v) < Math.abs(slowShutterEnd(best, drop, dur, bounds) - v)) best = opt;
        if (best !== (config.effectHookExtend ?? '')) { saveOnce(d); setHooks({ config: { effectHookExtend: best } }); }
        return;
      }
      if (d.kind === 'hookbody') {
        if (drop === null) return;
        if (!d.moved && Math.abs(e.clientX - d.x0) < 4) return;
        d.moved = true; document.body.style.cursor = 'grabbing';
        const r = snapT(drop + (cvX(e.clientX) - cvX(d.x0)) / pps);
        const v = clamp(r.v, DROP_EDGE, dur - DROP_EDGE);
        setDragDrop(v); setSnapLine(r.snapped && v === r.v ? v : null);
        return;
      }
      if (d.kind === 'sedge') {
        const s = styles.find((x) => x.uid === d.uid); if (!s) return;
        const v = xt(cvX(e.clientX)); let k = 0; bounds.forEach((x, i) => { if (Math.abs(x - v) < Math.abs(bounds[k] - v)) k = i; });
        if (d.side === 'l') { const a = clamp(k, 0, s.b - 1); if (a !== s.a && styleFree(s.lane, a, s.b, s.uid)) { saveOnce(d); setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a } : x)); } }
        else { const bb = clamp(k, s.a + 1, shots); if (bb !== s.b && styleFree(s.lane, s.a, bb, s.uid)) { saveOnce(d); setStyles(styles.map((x) => x.uid === s.uid ? { ...x, b: bb } : x)); } }
        return;
      }
      if (d.kind === 'smove') {
        const s = styles.find((x) => x.uid === d.uid); if (!s) return;
        const span = s.b - s.a; const a = clamp(frameAt(xt(cvX(e.clientX))) - d.grab, 0, shots - span);
        const ln = laneAt(e.clientX, e.clientY); const lane: 0 | 1 = ln === 's1' ? 1 : ln === 's0' ? 0 : s.lane;
        if ((a !== s.a || lane !== s.lane) && styleFree(lane, a, a + span, s.uid)) { saveOnce(d); setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a, b: a + span, lane } : x)); }
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
      // склейку не сдвинули (просто клик по краю) — рецепт не трогаем и «ручными» не помечаем
      if (d.kind === 'cut') { setDragCuts(null); if (d.saved) setTimeline({ cuts: d.cuts.map((c) => c + start), edited: true }); return; }
      if (d.kind === 'hookedge') { document.body.style.cursor = ''; return; }
      if (d.kind === 'hookbody') { document.body.style.cursor = ''; if (d.moved && dragDrop !== null) commitDrop(dragDrop); setDragDrop(null); return; }
      if (d.kind === 'lib') {
        document.body.style.cursor = ''; setGhost(null); setPlace(null);
        if (!d.live) return;
        const target = libTarget(d.item, e.clientX, e.clientY); if (!target) return;
        if ('join' in target) { setTransition(target.join, d.item.label); setSel({ type: 'cut', i: target.join }); say(tr('wizard.montage.cutSet', { n: target.join + 1, name: fxName(d.item.label) })); return; }
        if (target.bad) return;
        if (d.item.kind === 'text') setSub(d.item.label);
        else if (d.item.kind === 'hook') addHook(d.item);
        else addStyle(d.item.label, frameAt(target.a + 1e-3), target.lane === 's1' ? 1 : 0);
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
  });

  const srcPins: Record<number, string> = sbVideo?.pins ?? {};
  const tapStart = useRef<{ x: number; y: number } | null>(null);
  const onCanvasDownPhone = (e: ReactPointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const selected = target.closest('.fxt-clip.sel');
    // у выбранного кадра тянутся только края (двигают склейку), у остального — и тело
    if (selected && (!selected.classList.contains('fr') || target.closest('.fxt-edge'))) { tapStart.current = null; onCanvasDown(e); return; }
    tapStart.current = { x: e.clientX, y: e.clientY };
  };
  const onCanvasTap = (e: React.MouseEvent<HTMLDivElement>) => {
    const from = tapStart.current; tapStart.current = null;
    if (!from || Math.hypot(e.clientX - from.x, e.clientY - from.y) > 8) return;
    const target = e.target as HTMLElement;
    if (target.closest('.fxt-join')) return;
    const st = target.closest<HTMLElement>('.fxt-clip.st');
    if (st) { setSel({ type: 'style', uid: Number(st.dataset.uid) }); return; }
    if (target.closest('.fxt-clip.hk')) { setSel({ type: 'hook' }); return; }
    const sb = target.closest<HTMLElement>('.fxt-clip.sb');
    if (sb) { setSel({ type: 'sub', i: Number(sb.dataset.sub) }); return; }
    const fr = target.closest<HTMLElement>('.fxt-clip.fr');
    if (fr) { setSel({ type: 'frame', i: Number(fr.dataset.frame) }); return; }
    setSel(null);
  };
  const onCanvasDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('.fxt-join') || target.closest('.mt-swap')) return;
    setPop(null);
    if (target.closest('.fxt-ruler')) { drag.current = { kind: 'scrub' }; seek(xt(cvX(e.clientX))); return; }
    const edge = target.closest<HTMLElement>('.fxt-edge');
    const st = target.closest<HTMLElement>('.fxt-clip.st');
    if (edge?.dataset.cut) { drag.current = { kind: 'cut', i: Number(edge.dataset.cut), cuts: [...cuts], snap: capture([combo.index], { timeline: true }), saved: false }; e.preventDefault(); return; }
    if (st) {
      const uid = Number(st.dataset.uid); setSel({ type: 'style', uid });
      const s = styles.find((x) => x.uid === uid);
      const undoable = { snap: capture([combo.index]), saved: false };
      drag.current = edge ? { kind: 'sedge', uid, side: edge.dataset.side === 'l' ? 'l' : 'r', ...undoable } : { kind: 'smove', uid, grab: frameAt(xt(cvX(e.clientX))) - (s?.a ?? 0), ...undoable };
      e.preventDefault(); return;
    }
    if (target.closest('[data-hookedge]')) { setSel({ type: 'hook' }); drag.current = { kind: 'hookedge', snap: capture([combo.index]), saved: false }; document.body.style.cursor = 'ew-resize'; e.preventDefault(); return; }
    if (target.closest('.fxt-clip.hk')) { setSel({ type: 'hook' }); drag.current = { kind: 'hookbody', x0: e.clientX, moved: false }; return; }
    const sb = target.closest<HTMLElement>('.fxt-clip.sb');
    if (sb) {
      const i = Number(sb.dataset.sub); const w = subs[i];
      setSel({ type: 'sub', i }); seek(w.a + 0.02);
      const side = target.closest<HTMLElement>('[data-wedge]')?.dataset.wedge as 'l' | 'r' | undefined;
      drag.current = { kind: 'word', i, mode: side ?? 'move', grab: xt(cvX(e.clientX)) - w.a, a0: w.a, b0: w.b };
      e.preventDefault(); return;
    }
    const fr = target.closest<HTMLElement>('.fxt-clip.fr');
    if (fr) { const i = Number(fr.dataset.frame); setSel({ type: 'frame', i }); if (tRef.current < bounds[i] || tRef.current >= bounds[i + 1]) seek(bounds[i] + 0.001); return; }
    setSel(null);
  };

  /* ── клавиатура ── */
  const goVideo = (d: number) => { onIndex((index + d + total) % total); };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.matches?.('input, textarea, select, [contenteditable="true"]')) return;
      // Esc закрывает сначала то, что открыто поверх: подсказку клавиш, меню роликов, шторку,
      // окно склейки, выбор — и только когда открытого ничего нет, сам стол
      if (e.key === 'Escape') {
        if (editK !== null) return; // замену кадра отменяет её док
        if (keysOpen) setKeysOpen(false);
        else if (switchOpen) setSwitchOpen(false);
        else if (sheet) setSheet(null);
        else if (pop) setPop(null);
        else if (sel) setSel(null);
        else onClose();
        return;
      }
      if ((e.key === 'f' || e.key === 'F' || e.key === 'а' || e.key === 'А') && sel?.type === 'sub') { e.preventDefault(); toggleAsrFocus(subs[sel.i].idx); return; }
      // пробел на кнопке/вкладке нажимает её саму — играть/пауза только вне контролов
      if (e.code === 'Space') { if (target.closest?.('button, a, [role="button"], [role="tab"], [role="radio"], [role="option"], [role="switch"]')) return; e.preventDefault(); setPlaying((p) => !p); return; }
      if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (e.key === '[' || e.key === ']') { e.preventDefault(); goVideo(e.key === ']' ? 1 : -1); return; }
      // в «Все ролики» выделение спрятано — удалять по нему нельзя
      if (view === 'grid') return;
      if (e.key === 'Delete' || e.key === 'Backspace') { del(); return; }
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && sel?.type === 'hook' && drop !== null) {
        e.preventDefault();
        const right = e.key === 'ArrowRight';
        const beat = right ? beats.find((b) => b > drop + 0.01) : [...beats].reverse().find((b) => b < drop - 0.01);
        commitDrop(beat ?? drop + (right ? 1 : -1) / FPS);
        return;
      }
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); seek(tRef.current + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 1 : 1 / FPS)); return; }
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); const next = e.key === 'ArrowDown' ? bounds.find((x) => x > tRef.current + 0.01) : [...bounds].reverse().find((x) => x < tRef.current - 0.01); if (next !== undefined) seek(next); }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });

  /* ── подписи выделения ── */
  const selInfo = () => {
    if (!sel) return <span className="muted">{tr('wizard.montage.selNone')}</span>;
    if (sel.type === 'frame') {
      const clip = clips[sel.i]; const pinned = Boolean(srcPins[sel.i]);
      return <><span className="mt-thumb mt-selthumb"><FrameView frame={clip} thumb /></span><span className="nm">{tr('wizard.montage.shotTitle', { n: pad(sel.i + 1) })}</span><span className="meta num">{clip?.color ? tr(clip.strobe ? 'wizard.montage.strobe' : 'wizard.montage.colour') : clip ? clipTitle(clip.id) : '—'} · {secs(bounds[sel.i + 1] - bounds[sel.i])}</span>
        
        {pinned && <button type="button" className="fxt-pill" data-tip={tr('wizard.montage.pinnedTip')} onClick={() => unpin(sel.i)}><Glyph name="lock" size={13} /><span className="tx">{tr('wizard.montage.pinned')}</span></button>}</>;
    }
    if (sel.type === 'cut') { const label = transitionAt(sel.i); return <><Ic kind="trans" label={label} size={24} /><span className="nm">{tr('wizard.montage.cutTitle', { a: sel.i + 1, b: sel.i + 2 })}</span><span className="meta">{fxName(label)}</span><button type="button" className="fxt-pill" onClick={() => setTransitionAll(label)}><span className="tx">{tr('wizard.montage.toAllCuts')}</span></button>{allPill(() => cutToAll(sel.i))}</>; }
    if (sel.type === 'hook' && hookRange && activeHookLabel) return <><Ic kind="hook" label={activeHookLabel} size={24} /><span className="nm">{fxName(activeHookLabel)}</span><span className="meta num">{tr(hookRange[1] <= (dropView ?? 0) + 1e-3 ? 'wizard.montage.hookEndsAtDrop' : 'wizard.montage.hookStartsAtDrop')} {tc(start + (dropView ?? 0))} · {secs(hookRange[1] - hookRange[0])} · {tr('wizard.montage.hookDragHint')}</span>{kind === 'effects' && config.effectHook === 'Слоу-шаттер' && <div className="fxt-seg" role="group" aria-label={tr('wizard.montage.slowLength')}>{SLOW_EXTENDS.map(([opt, lab]) => <button key={opt || 'std'} type="button" aria-pressed={(config.effectHookExtend ?? '') === opt} onClick={() => setSlowExtend(opt)}><span className="tx">{tr(`wizard.montage.${lab}`)}</span></button>)}</div>}{allPill(hookToAll, hookIdx.length)}<button type="button" className="fxt-icon" aria-label={tr('wizard.montage.removeHook')} data-tip={tr('wizard.montage.removeHookKey')} onClick={del}><Glyph name="trash" size={17} /></button></>;
    if (sel.type === 'style') { const s = styles.find((x) => x.uid === sel.uid); if (!s) return null; return <><Ic kind="style" label={s.style} size={24} /><span className="nm">{fxName(s.style)}</span><span className="meta num">{s.b - s.a > 1 ? tr('wizard.montage.shotsRange', { a: s.a + 1, b: s.b }) : tr('wizard.montage.placeShot', { n: s.a + 1 })} · {secs(bounds[s.b] - bounds[s.a])}</span><button type="button" className="fxt-pill" onClick={() => styleWhole(s)}><span className="tx">{tr('wizard.montage.wholeClip')}</span></button>{allPill(() => styleToAll(s))}<button type="button" className="fxt-icon" aria-label={tr('wizard.montage.delete')} data-tip={tr('wizard.montage.deleteKey')} onClick={del}><Glyph name="trash" size={17} /></button></>; }
    if (sel.type === 'sub') { const s = subs[sel.i]; return s ? <><Ic kind="trans" label="text" size={24} /><span className="nm">«{s.text}»</span><span className="meta num">{tc(s.a)} → {tc(s.b)} · {secs(s.b - s.a)}{s.focus ? ` · ${tr('wizard.montage.focusWord')}` : ''}</span><button type="button" className="fxt-pill" data-tip={tr('wizard.montage.focusTip')} onClick={() => toggleAsrFocus(s.idx)}><span className="tx">{tr(s.focus ? 'wizard.montage.unfocus' : 'wizard.montage.makeFocus')}</span></button></> : null; }
    return null;
  };

  /* общие действия выделения — десктопная строка и телефонная панель зовут одни и те же */
  const setSlowExtend = (opt: SlowExtend) => { if ((config.effectHookExtend ?? '') === opt) return; remember(); setHooks({ config: { effectHookExtend: opt } }); };
  const hookToAll = () => { if (activeHookLabel) toAll(withHookOf(vfx), fxName(activeHookLabel), hookIdx); };
  const cutToAll = (i: number) => { const label = transitionAt(i); toAll((v) => ({ ...v, transitions: { ...v.transitions, [i]: label } }), tr('wizard.montage.cutSet', { n: i + 1, name: fxName(label) })); };
  const styleWhole = (s: TimelineStyleRange) => {
    if (!styleFree(s.lane, 0, shots, s.uid)) { say(tr('wizard.montage.laneBlocked')); return; }
    remember(); setStyles(styles.map((x) => x.uid === s.uid ? { ...x, a: 0, b: shots } : x));
  };
  const styleToAll = (s: TimelineStyleRange) => toAll((v) => ({ ...v, styles: [...v.styles.filter((o) => o.lane !== s.lane || o.b <= s.a || o.a >= s.b), { ...s, uid: Math.max(0, ...v.styles.map((o) => o.uid)) + 1 }] }), fxName(s.style));
  // темп общий для батча и пересобирает кадры всех роликов — в историю идут все и склейки
  const changePace = (pace: TimelinePace) => {
    if (recipe.pace === pace) return;
    remember(allIdx, { timeline: true });
    recipe.setPace(pace); setSel(null); setPop(null);
  };
  const paceGlyph = (k: number) => { const n = [3, 5, 7][k]; let d = ''; for (let j = 1; j < n; j++) d += `M${(2 + j * 20 / n).toFixed(1)} 8v8`; return `<rect x="2" y="7" width="20" height="10" rx="2.5"/><path d="${d}"/>`; };
  const paceLabel = (pace: TimelinePace) => tr({ sparse: 'wizard.montage.paceSparse', auto: 'wizard.montage.paceAuto', dense: 'wizard.montage.paceDense' }[pace]);
  const paceTip = (pace: TimelinePace) => { const n = recipe.data?.cuts[pace].length; const label = pace === 'auto' ? tr('wizard.montage.paceAutoTip') : paceLabel(pace); return n !== undefined ? tr('wizard.montage.paceTip', { label, shots: tr('wizard.montage.shots', { count: n + 1 }) }) : label; };
  const used = useCallback((item: LibItem) => item.kind === 'src' ? clips.some((c) => c.id === item.label)
    : item.kind === 'text' ? vfx.sub === item.label
      : item.kind === 'hook' ? activeHookLabel === item.label
        : item.kind === 'style' ? styles.some((s) => s.style === item.label)
          : Object.values(vfx.transitions).includes(item.label) || (defaultGlue === item.label && item.label !== NO_GLUE), [clips, vfx, activeHookLabel, styles, defaultGlue]);
  const toggleOpen = useCallback((k: string) => setOpenState((o) => ({ ...o, [k]: !o[k] })), []);

  /* ── линейка ── */
  const step = pps > 90 ? 0.25 : pps > 40 ? 0.5 : 1;
  const lab = pps > 70 ? 1 : pps > 30 ? 2 : 5;
  const ticks: { v: number; maj: boolean }[] = [];
  for (let v = 0; v <= dur + 1e-6; v += step) ticks.push({ v, maj: Math.abs(v / lab - Math.round(v / lab)) < 1e-6 });
  // второй стиль — по запросу: дорожка появляется по «+» у «Стиля 1» или сама, когда стиль лёг на неё
  const lane2 = lane2Open || styles.some((x) => x.lane === 1);
  const laneHead = (cls: string, icon: string, k: 'frame' | 'hook' | 'style' | 'trans', name: string, extra?: ReactNode) => (
    <div className={`fxt-lh ${cls}`}>
      <span className={`fxt-ic k-${k} sm`}><Glyph name={icon} size={13} /></span>
      <span className="nm">{name}</span>{extra}
    </div>
  );

  /* ── подписи роликов ── */
  const videoLabel = (c: Combo) => {
    const v = fxOf(c.index);
    const hook = hookLabel(v.kind, v.config);
    return [chip(c.bgLabel), v.sub ?? c.sub ?? '—', hook ? fxName(hook) : tr('wizard.montage.noHook')].join(' · ');
  };
  const stageFxFor = (c: Combo): StageFx => { const v = fxOf(c.index); return { transitionAt: transitionAtFor(v), styles: v.styles, hookKind: v.kind, hookLabel: hookLabel(v.kind, v.config), hookRange: hookRangeFor(v), frameUrl: frameUrlOf(v.frame) }; };
  const subFor = (c: Combo) => subProps(fxOf(c.index).sub ?? c.sub, !c.vertical);
  const showFramesGuide = !phone && view === 'table' && Boolean(sbVideo) && !framesGuideDismissed && editK === null;
  const showLanesGuide = !phone && view === 'table' && (framesGuideDismissed || !sbVideo) && !lanesGuideDismissed && editK === null && !pop && !keysOpen;
  useMarkGuideSeen('table-frames', showFramesGuide);
  useMarkGuideSeen('table-lanes', showLanesGuide);
  const isEdited = (c: Combo) => fxOf(c.index).edited || Object.keys(storyboard.videos[c.slotIndex]?.pins ?? {}).length > 0;
  const editedCount = combos.filter(isEdited).length;

  /*
   * «Все ролики»: живой (видео, субтитры) только один ролик — под курсором/фокусом, иначе
   * выбранный; остальные — неподвижные кадры под тем же временем. Раньше каждый ролик батча
   * был полным Stage с <video preload="auto"> и canvas субтитров — самый тяжёлый вид стола.
   * На медленной сети выбранный сам не оживает — только по наведению.
   */
  const lowData = useLowData();
  const [gridHot, setGridHot] = useState<number | null>(null);
  const gridLive = gridHot ?? (lowData ? null : index);
  // «Все ролики»: сетка под размер области — все 9:16 целиком, синхронно по времени.
  const gridCell = useMemo(() => {
    const gap = 16; const labelH = 44;
    let best = { w: 0, h: 0, cols: 1 };
    for (let cols = 1; cols <= total; cols++) {
      const rows = Math.ceil(total / cols);
      const w = Math.min((mainSize.w - 32 - gap * (cols - 1)) / cols, ((mainSize.h - 32 - gap * (rows - 1)) / rows - labelH) * 9 / 16);
      if (w > best.w) best = { w: Math.floor(w), h: Math.floor(w * 16 / 9), cols };
    }
    return best;
  }, [mainSize, total]);

  const canvas = (
                <div ref={cvRef} className="fxt-cv" style={{ width: canvasW }} onPointerDown={phone ? onCanvasDownPhone : onCanvasDown} onClick={phone ? onCanvasTap : undefined}>
                {/* телефон: слева от начала ролика пусто (ноль — под линией по центру), там названия
                    дорожек. Они часть полотна — уезжают вместе с дорожками, к ним можно отлистать */}
                {phone && (
        <div className="mm-heads">
          <div className="mm-h r" />
          <div className="mm-h f"><span className="fxt-ic k-frame sm"><Glyph name="film" size={13} /></span><span className="tx">{tr('wizard.montage.laneShots')}</span></div>
          <div className="mm-h hk"><span className="fxt-ic k-hook sm"><Glyph name="effects" size={13} /></span><span className="tx">{tr('wizard.montage.laneHook')}</span></div>
          <div className="mm-h st">
            <span className="fxt-ic k-style sm"><Glyph name="crystal" size={13} /></span><span className="tx">{tr('wizard.montage.laneStyle')}</span>
            {!lane2 && <button type="button" className="mm-hadd" aria-label={tr('wizard.montage.addLane2')} onClick={() => setLane2Open(true)}><Glyph name="plus" size={14} sw={2} /></button>}
          </div>
          {lane2 && (
            <div className="mm-h st">
              <span className="fxt-ic k-style sm"><Glyph name="crystal" size={13} /></span><span className="tx">{tr('wizard.montage.laneStyle2')}</span>
              {!styles.some((x) => x.lane === 1) && <button type="button" className="mm-hadd" aria-label={tr('wizard.montage.removeLane2')} onClick={() => setLane2Open(false)}><Glyph name="close" size={11} sw={2} /></button>}
            </div>
          )}
          <div className="mm-h sb"><span className="fxt-ic k-trans sm"><Glyph name="text" size={13} /></span><span className="tx">{tr('wizard.montage.laneText')}</span></div>
          <div className="mm-h au"><span className="fxt-ic k-trans sm"><Glyph name="audio" size={13} /></span><span className="tx">{tr('wizard.montage.laneBeats')}</span></div>
        </div>
                )}
                  <div className="fxt-ruler">
                    {ticks.map(({ v, maj }) => <span key={v}><i className={`fxt-tick${maj ? ' maj' : ''}`} style={{ left: tx(v) }} />{maj && <span className="fxt-tlab num" style={{ left: tx(v) }}>{pad(Math.floor((start + v) / 60))}:{pad(Math.round(start + v) % 60)}</span>}</span>)}
                    {dropView !== null && <div className="fxt-dropflag num" style={{ left: tx(dropView) }}><span>{tr('wizard.montage.dropFlag', { at: tc(start + dropView) })}</span></div>}
                  </div>
                  <div ref={(el) => { laneRefs.current.frames = el; }} className={`fxt-lane l-frames mt-l-src${place && 'lane' in place && place.lane === 'frames' ? ' over' : ''}`}>
                    {!phone && recipe.loading && <span className="fxt-hint" style={{ left: x0 + 8 }}>{tr('wizard.montage.cutsLoading')}</span>}
                    {!phone && recipe.error && !recipe.loading && <span className="mt-lanemsg" role="alert" style={{ left: x0 + 8 }}><span className="tx">{tr('wizard.montage.cutsFailed')}</span><button type="button" className="fxt-pill" onClick={recipe.retry}><span className="tx">{tr('wizard.montage.retry')}</span></button></span>}
                    {!recipe.loading && Array.from({ length: shots }, (_, i) => {
                      const x = tx(bounds[i]); const w = tx(bounds[i + 1]) - x; const clip = clips[i]; const pinned = Boolean(srcPins[i]);
                      return (
                        <div key={`f${i}`} className={`fxt-clip fr mt-fr${sel?.type === 'frame' && sel.i === i ? ' sel' : ''}${i === fNow ? ' cur' : ''}`} data-frame={i} style={{ left: x + 1, width: w - 2 }}
                          >
                          {clip?.url && <span className={`mt-film${clip.fit === 'contain' ? ' wide' : ''}`} style={{ backgroundImage: `url("${clip.url}")` }} />}
                          {clip?.color && <span className={`mt-film mt-filmc${clip.strobe ? ' strobe' : ''}`} style={{ background: clip.color }} />}
                          {i > 0 && <i className="fxt-edge l" data-cut={i - 1} />}
                          <span className="n num">{pad(i + 1)}</span>
                          {pinned && <span className="mt-pin" data-tip={tr('wizard.montage.pinnedByHand')}><Glyph name="lock" size={10} sw={2.2} /></span>}
                          {w > 86 && <span className="d num">{secs(bounds[i + 1] - bounds[i])}</span>}
                          {i < shots - 1 && <i className="fxt-edge r" data-cut={i} />}
                        </div>
                      );
                    })}
                    {!recipe.loading && !staticColor && cuts.map((c, i) => {
                      const label = transitionAt(i);
                      return (
                        <button key={`j${i}`} type="button" className={`fxt-join${label === NO_GLUE ? ' none' : ''}${sel?.type === 'frame' && (sel.i === i || sel.i === i + 1) ? ' under' : ''}${sel?.type === 'cut' && sel.i === i ? ' sel' : ''}${place && 'join' in place && place.join === i ? ' target' : ''}`} style={{ left: tx(c) }} aria-label={tr('wizard.montage.cutSet', { n: i + 1, name: fxName(label) })}
                          onClick={(e) => { setSel({ type: 'cut', i }); popEdited.current = null; if (phone) { setSheet('trans'); return; } seek(Math.max(0, c - 0.5)); const z = zoomScale(); const r = e.currentTarget.getBoundingClientRect(); setPop({ type: 'cut', i, x: (r.left + r.width / 2) / z, y: r.top / z }); }}>
                          {label === NO_GLUE ? <Glyph name="plus" size={12} sw={2} /> : <Ic kind="trans" label={label} on size={24} />}
                        </button>
                      );
                    })}
                  </div>
                  <div ref={(el) => { laneRefs.current.hook = el; }} className={`fxt-lane l-hook${place && 'lane' in place && place.lane === 'hook' ? ' over' : ''}`}>
                    {hookRange && activeHookLabel
                      ? (() => { const x = tx(hookRange[0]); const w = Math.max(18, tx(hookRange[1]) - x); return <><div className={`fxt-clip hk${sel?.type === 'hook' ? ' sel' : ''}${dragDrop !== null ? ' drag' : ''}`} style={{ left: x, width: w }} data-tip={tr('wizard.montage.hookBodyTip')}><Glyph name={GLYPH[activeHookLabel]} size={13} />{w >= 80 && <span className="lab">{fxName(activeHookLabel)}</span>}{config.effectHook === 'Слоу-шаттер' && kind === 'effects' && <i className="fxt-edge r" data-hookedge="r" data-tip={tr('wizard.montage.slowEdgeTip')} />}</div>{w < 80 && <span className="fxt-outlab" style={{ left: x + w + 8 }}>{fxName(activeHookLabel)}</span>}</>; })()
                      : <span className="fxt-hint" style={{ left: tx(drop ?? 0) + 10 }}>{tr(drop === null ? 'wizard.montage.hookNeedsDrop' : 'wizard.montage.hookEmpty')}</span>}
                  </div>
                  {(lane2 ? [0, 1] as const : [0] as const).map((L) => (
                    <div key={L} ref={(el) => { laneRefs.current[`s${L}`] = el; }} className={`fxt-lane l-s${L}${place && 'lane' in place && place.lane === `s${L}` ? ' over' : ''}`}>
                      {!styles.some((s) => s.lane === L) && <span className="fxt-hint" style={{ left: x0 + 8 }}>{tr(L ? 'wizard.montage.lane2Tip' : 'wizard.montage.style1Empty')}</span>}
                      {styles.filter((s) => s.lane === L && s.b <= shots).map((s) => {
                        const x = tx(bounds[s.a]); const w = tx(bounds[s.b]) - x;
                        return (
                          <div key={s.uid} className={`fxt-clip st${sel?.type === 'style' && sel.uid === s.uid ? ' sel' : ''}`} data-uid={s.uid} style={{ left: x + 1, width: w - 2 }}>
                            <i className="fxt-edge l" data-side="l" /><Glyph name={GLYPH[s.style]} size={13} /><span className="lab">{fxName(s.style)}</span><i className="fxt-edge r" data-side="r" />
                          </div>
                        );
                      })}
                    </div>
                  ))}
                  <div ref={(el) => { laneRefs.current.subs = el; }} className={`fxt-lane l-subs${place && 'lane' in place && place.lane === 'subs' ? ' over' : ''}`}>
                    {!subs.length && <span className="fxt-hint" style={{ left: x0 + 8 }}>{tr('wizard.montage.subsEmpty')}</span>}
                    {subs.map((s, i) => { const x = tx(Math.max(0, s.a)); const w = tx(Math.min(dur, s.b)) - x; return <div key={`s${i}`} className={`fxt-clip sb mt-word${s.focus ? ' focus' : ''}${sel?.type === 'sub' && sel.i === i ? ' sel' : ''}${t >= s.a && t < s.b ? ' cur' : ''}`} data-sub={i} style={{ left: x + 1, width: Math.max(2, w - 2) }} title={tr('wizard.montage.wordTip', { text: s.focus ? `${s.text} · ${tr('wizard.montage.focusWord')}` : s.text })} onDoubleClick={() => toggleAsrFocus(s.idx)}>{w > 22 && <i className="fxt-edge l" data-wedge="l" />}<span className="lab">{s.focus ? '★ ' : ''}{s.text}</span>{w > 22 && <i className="fxt-edge r" data-wedge="r" />}</div>; })}
                  </div>
                  <div className="fxt-lane l-audio">
                    {wavePeaksSeg && (
                      <svg className="mt-wave" style={{ left: x0, width: tx(dur) - x0 }} viewBox={`0 0 ${wavePeaksSeg.length} 100`} preserveAspectRatio="none" aria-hidden="true">
                        {wavePeaksSeg.map((p, k) => { const h = Math.max(4, p * 100); return <rect key={k} x={k + 0.15} y={50 - h / 2} width="0.7" height={h} />; })}
                      </svg>
                    )}
                    {beats.map((b, k) => <i key={k} className={`fxt-beat${drop !== null && Math.round((b - drop) * (bpm || 120) / 60) % 4 === 0 ? ' down' : ''}`} style={{ left: tx(b) }} />)}
                  </div>
                  {dropView !== null && <div className="fxt-dropline" style={{ left: tx(dropView) }} />}
                  <div className="fxt-phl" style={{ left: tx(t) }} />
                  {snapLine !== null && <div className="fxt-snapl" style={{ left: tx(snapLine) }} />}
                  {place && 'lane' in place && (() => {
                    const el = laneRefs.current[place.lane]; if (!el) return null;
                    return <div className={`fxt-place ${place.bad ? 'bad' : place.lane === 'hook' ? 'hk' : place.lane === 'frames' || place.lane === 'subs' ? 'src' : 'st'}`} style={{ left: tx(place.a), width: Math.max(18, tx(place.b) - tx(place.a)), top: el.offsetTop + 6, height: el.offsetHeight - 12 }}>{place.label && <span className="why">{place.label}</span>}</div>;
                  })()}
                </div>
  );

  /* ── телефон (CapCut): шапка, превью, время, таймлайн с линией по центру, панель, шторка ── */
  const nearestCut = () => { if (!cuts.length) return -1; let k = 0; cuts.forEach((c, i) => { if (Math.abs(c - t) < Math.abs(cuts[k] - t)) k = i; }); return k; };
  const replaceStyle = (uid: number, label: string) => {
    remember(); setStyles(styles.map((x) => (x.uid === uid ? { ...x, style: label } : x)));
    say(tr('wizard.montage.styleReplaced', { name: fxName(label) }));
  };
  const sheetAdd = (item: LibItem) => {
    if (item.kind === 'style' && sel?.type === 'style') return replaceStyle(sel.uid, item.label);
    if (item.kind === 'trans' && sel?.type === 'cut') { setTransition(sel.i, item.label); say(tr('wizard.montage.cutSet', { n: sel.i + 1, name: fxName(item.label) })); return; }
    addFromLib(item);
  };
  const nudgeWord = (i: number, d: number) => {
    const w = subs[i]; if (!w) return;
    const words = asr.words; const cur = words[w.idx]; if (!cur) return;
    const len = cur.tEnd - cur.tStart;
    const lo = words[w.idx - 1] ? words[w.idx - 1].tEnd + 0.02 : start;
    const hi = (words[w.idx + 1] ? words[w.idx + 1].tStart - 0.02 : start + dur) - len;
    const a = clamp(cur.tStart + d, lo, Math.max(lo, hi));
    setAsrWord(w.idx, { tStart: Math.round(a * 1000) / 1000, tEnd: Math.round((a + len) * 1000) / 1000 });
  };
  const hookToBeat = (right: boolean) => {
    if (drop === null) return;
    const beat = right ? beats.find((b) => b > drop + 0.01) : [...beats].reverse().find((b) => b < drop - 0.01);
    commitDrop(beat ?? drop + (right ? 1 : -1) / FPS);
  };
  /*
   * Недоступный инструмент не молчит: вместо мёртвой серой кнопки — тап показывает причину.
   * off — эта причина (aria-disabled, кнопка остаётся в фокусе и читается скринридером).
   */
  const tool = (key: string, icon: string, label: string, run: () => void, opts: { off?: string | null; danger?: boolean } = {}) => (
    <button key={key} type="button" className={`mm-tool${opts.danger ? ' danger' : ''}`} aria-disabled={opts.off ? true : undefined} onClick={opts.off ? () => say(opts.off!) : run}>
      <Glyph name={icon} size={22} /><span className="tx">{label}</span>
    </button>
  );
  // замена и перемешивание кадров идут через док ролика: без него (нет раскадровки или она ещё
  // собирается под новые склейки) тап раньше ничего не делал
  const dockReady = Boolean(sbVideo) && clips.length === shots && !recipe.loading;
  const srcOff = dockReady ? null
    : tr(sbVideo ? 'wizard.montage.srcPending'
      : combo.bgKey?.startsWith('photo:') ? 'wizard.montage.srcPhoto'
        : combo.bgKey?.startsWith('upload:') ? 'wizard.montage.srcUpload'
          : combo.bgKey === '__color__' ? 'wizard.montage.srcColor'
            : 'wizard.montage.srcLater');
  const transOff = staticColor ? tr('wizard.montage.staticNoCuts')
    : recipe.loading ? tr('wizard.montage.cutsPending')
      : !cuts.length ? tr('wizard.montage.noCuts') : null;
  const hookOff = combo.hookAllowed ? null : tr('wizard.montage.hookVerticalOnly');
  const frameOff = combo.vertical ? null : tr('wizard.montage.frameVerticalOnly');
  const allTool = (run: () => void, count = total) => (count > 1 ? [tool('every', 'grid', tr('wizard.montage.allN', { n: count }), run)] : []);
  const tools = (() => {
    if (!sel) {
      return [
        tool('src', 'reroll', tr('wizard.montage.toolShot'), () => { setSel({ type: 'frame', i: fNow }); askDock('edit'); }, { off: srcOff }),
        tool('trans', 't_snap', tr('wizard.montage.toolTrans'), () => { setSel({ type: 'cut', i: Math.max(0, nearestCut()) }); setSheet('trans'); }, { off: transOff }),
        tool('style', 'crystal', tr('wizard.montage.toolStyle'), () => setSheet('style')),
        tool('hook', 'effects', tr('wizard.montage.toolHook'), () => setSheet('hook'), { off: hookOff }),
        tool('text', 'text', tr('wizard.montage.toolText'), () => setSheet('text')),
        tool('frame', 'frames', tr('wizard.montage.toolFrame'), () => setSheet('frame'), { off: frameOff }),
        tool('pace', 'audio', tr('wizard.montage.toolPace'), () => setSheet('pace')),
        tool('grid', 'grid', tr('wizard.montage.toolGrid'), () => { setView('grid'); setSel(null); })
      ];
    }
    const back = <button key="back" type="button" className="mm-tool mm-back" aria-label={tr('wizard.montage.deselect')} onClick={() => setSel(null)}><Glyph name="back" size={22} /></button>;
    if (sel.type === 'frame') {
      const i = sel.i;
      return [back,
        tool('re', 'reroll', tr('wizard.montage.toolReplace'), () => askDock('edit'), { off: srcOff }),
        tool('mix', 'chain', tr('wizard.montage.toolShuffle'), () => askDock('shuffle'), { off: srcOff }),
        ...(srcPins[i] ? [tool('unpin', 'lock', tr('wizard.montage.toolUnpin'), () => { unpin(i); say(tr('wizard.montage.shotUnpinned', { n: i + 1 })); })] : []),
        ...(i < shots - 1 && !staticColor ? [tool('tr', 't_snap', tr('wizard.montage.toolTrans'), () => { setSel({ type: 'cut', i }); setSheet('trans'); })] : []),
        tool('st', 'crystal', tr('wizard.montage.toolStyle'), () => setSheet('style'))];
    }
    if (sel.type === 'cut') {
      const i = sel.i;
      return [back,
        tool('tr', 't_snap', tr('wizard.montage.toolTrans'), () => setSheet('trans')),
        tool('all', 'chain', tr('wizard.montage.toolToAll'), () => setTransitionAll(transitionAt(i))),
        ...allTool(() => cutToAll(i)),
        tool('off', 't_none', tr('wizard.montage.toolRemove'), () => { setTransition(i, NO_GLUE); say(tr('wizard.montage.cutNoTrans', { n: i + 1 })); }, { off: transitionAt(i) === NO_GLUE ? tr('wizard.montage.alreadyNoTrans') : null })];
    }
    if (sel.type === 'style') {
      const s = styles.find((x) => x.uid === sel.uid);
      return [back, tool('re', 'crystal', tr('wizard.montage.toolReplace'), () => setSheet('style')),
        ...(s ? [tool('whole', 'film', tr('wizard.montage.toolWhole'), () => styleWhole(s)), ...allTool(() => styleToAll(s))] : []),
        tool('del', 'trash', tr('wizard.montage.delete'), del, { danger: true })];
    }
    if (sel.type === 'hook') {
      const slow = kind === 'effects' && config.effectHook === 'Слоу-шаттер';
      const ext = (config.effectHookExtend ?? '') as SlowExtend;
      const nextExt = SLOW_EXTENDS[(SLOW_EXTENDS.findIndex(([o]) => o === ext) + 1) % SLOW_EXTENDS.length];
      return [back,
        tool('l', 'back', tr('wizard.montage.toolBeatBack'), () => hookToBeat(false), { off: drop === null ? tr('wizard.montage.pickDropFirst') : null }),
        tool('r', 'fwd', tr('wizard.montage.toolBeatFwd'), () => hookToBeat(true), { off: drop === null ? tr('wizard.montage.pickDropFirst') : null }),
        tool('re', 'effects', tr('wizard.montage.toolChange'), () => setSheet('hook')),
        ...(slow ? [tool('ext', 'slowshutter', tr(`wizard.montage.${nextExt[1]}`), () => setSlowExtend(nextExt[0]))] : []),
        ...(activeHookLabel ? allTool(hookToAll, hookIdx.length) : []),
        tool('del', 'trash', tr('wizard.montage.delete'), del, { danger: true, off: kind === 'none' ? tr('wizard.montage.noHookToRemove') : null })];
    }
    const i = sel.i; const w = subs[i];
    return [back,
      tool('focus', 'check', tr(w?.focus ? 'wizard.montage.unfocus' : 'wizard.montage.toolFocus'), () => { if (w) toggleAsrFocus(w.idx); }),
      tool('l', 'back', tr('wizard.montage.nudgeBack'), () => nudgeWord(i, -0.05)),
      tool('r', 'fwd', tr('wizard.montage.nudgeFwd'), () => nudgeWord(i, 0.05)),
      tool('text', 'text', tr('wizard.montage.toolTextStyle'), () => setSheet('text'))];
  })();
  const sheetTitle = sheet === 'trans' ? (sel?.type === 'cut' ? tr('wizard.montage.sheetTransCut', { n: sel.i + 1 }) : tr('wizard.montage.toolTrans'))
    : sheet === 'style' ? (sel?.type === 'style' ? tr('wizard.montage.sheetReplaceStyle') : tr('wizard.montage.sheetStyleOn', { n: (sel?.type === 'frame' ? sel.i : fNow) + 1 }))
      : sheet === 'hook' ? tr('wizard.montage.sheetHook') : sheet === 'text' ? tr('wizard.montage.laneText') : sheet === 'frame' ? tr('wizard.montage.toolFrame') : tr('wizard.montage.sheetPace');
  const mobileHeader = (
    <header className="mm-top">
      <button type="button" className="mm-ic" aria-label={tr(view === 'grid' ? 'wizard.montage.backToVideo' : 'wizard.montage.toPool')} onClick={view === 'grid' ? () => setView('table') : onClose}>
        <Glyph name={view === 'grid' ? 'back' : 'close'} size={20} />
      </button>
      <button type="button" className="mm-vid" aria-haspopup="listbox" aria-expanded={switchOpen} onClick={() => setSwitchOpen((o) => !o)}>
        <span className="num tx">{index + 1}<span className="of">/{total}</span></span>
        <span className="lb tx">{videoLabel(combo)}</span>
        {isEdited(combo) && <i className="mt-dot" />}
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <button type="button" className="mm-gen" disabled={busy} aria-busy={busy || undefined} onClick={() => { const blocked = onGenerate(); if (blocked) say(blocked); }}>{busy && <span className="spinner" aria-hidden="true" />}<span className="tx">{tr('wizard.montage.done', { n: total })}</span></button>
      {switchOpen && (
        <div className="fxt-vsw-menu mt-vmenu mm-vmenu" role="listbox" aria-label={tr('wizard.montage.batchVideos')}>
          {combos.map((c) => (
            <button key={c.index} type="button" role="option" aria-selected={c.index === index} className="fxt-vsw-item" onClick={() => { onIndex(c.index); setSwitchOpen(false); setView('table'); setSel(null); }}>
              <span className="mt-vthumb"><FrameView frame={clipsOf(c)[0]} thumb /></span>
              <span className="mt-vn num tx">{c.index + 1}</span>
              <span className="lb tx">{videoLabel(c)}</span>
              {isEdited(c) && <span className="mt-edited tx">{tr('wizard.montage.edited')}</span>}
            </button>
          ))}
        </div>
      )}
    </header>
  );
  const mobileMain = (
    <main ref={mainRef} className="mm-main">
      <section ref={pvRef} className="mm-pv" aria-label={tr('wizard.montage.preview')}>
        <Stage frames={clips} bounds={bounds} t={t} playing={playing} fx={{ transitionAt, styles, hookKind: kind, hookLabel: activeHookLabel, hookRange, frameUrl: frameUrlOf(vfx.frame) }} sub={subFor(combo)} w={stageSize.w} h={stageSize.h}>
          {sbVideo && clips.length === shots && (
            <FrameDock combo={combo} video={sbVideo} frames={clips} bounds={bounds} k={editK ?? (sel?.type === 'frame' ? sel.i : fNow)} drop={drop} compact request={dockReq} slot={replaceSlot}
              onSeek={(k) => { seek(bounds[k] + 0.001); setSel({ type: 'frame', i: k }); }}
              onEdit={setEditK} onChanged={markEdited} onError={say} />
          )}
        </Stage>
      </section>
      <div className="mm-transport">
        <span className="mm-time num"><b>{tc(t)}</b><span>&nbsp;/ {tc(dur)}</span></span>
        <button type="button" className="mm-play" aria-label={tr(buffering ? 'wizard.montage.loading' : playing ? 'wizard.montage.pause' : 'wizard.montage.play')} aria-busy={buffering || undefined} aria-pressed={playing} onClick={() => setPlaying((v) => !v)}>{buffering ? <span className="spinner" aria-hidden="true" /> : <Glyph name={playing ? 'pause' : 'play'} size={22} />}</button>
        <div className="mm-hist">
          <button type="button" className="mm-ic" aria-label={tr('wizard.montage.undo')} disabled={!canUndo} onClick={undo}><Glyph name="undo" size={20} /></button>
          <button type="button" className="mm-ic" aria-label={tr('wizard.montage.redo')} disabled={!canRedo} onClick={redo}><Glyph name="redo" size={20} /></button>
        </div>
      </div>
      <section ref={tlRef} className="mm-tl" aria-label={tr('wizard.montage.timeline')}>
        <div ref={scrollRef} className="fxt-scroll mm-scroll" onScroll={onPhoneScroll}>{canvas}</div>
        <i className="mm-playhead" aria-hidden="true" />
        {/* на телефоне подсказки дорожек скрыты — загрузку и сбой склеек показываем над таймлайном */}
        {recipe.loading && <div className="mm-tlmsg" role="status"><span className="spinner" aria-hidden="true" /><span className="tx">{tr('wizard.montage.cutsLoading')}</span></div>}
        {recipe.error && !recipe.loading && <div className="mm-tlmsg err" role="alert"><span className="tx">{tr('wizard.montage.cutsFailed')}</span><button type="button" className="fxt-pill" onClick={recipe.retry}><span className="tx">{tr('wizard.montage.retry')}</span></button></div>}
      </section>
      {editK !== null
        ? <div ref={setReplaceSlot} className="mm-bar mm-replace-slot" />
        : <nav ref={barRefCb} className="mm-bar" aria-label={tr('wizard.montage.tools')} data-fade-l={barFade.l || undefined} data-fade-r={barFade.r || undefined} onScroll={syncBar}>{tools}</nav>}
      {!mobIntroDismissed && editK === null && !sheet && (
        <div className="mm-intro" role="note">
          <ul>
            <li><Glyph name="chain" size={16} /><span className="tx">{tr('wizard.montage.intro1')}</span></li>
            <li><Glyph name="check" size={16} /><span className="tx">{tr('wizard.montage.intro2')}</span></li>
            <li><Glyph name="fwd" size={16} /><span className="tx">{tr('wizard.montage.intro3')}</span></li>
          </ul>
          <button type="button" className="mm-intro-ok" onClick={() => setMobIntroDismissed(true)}><span className="tx">{tr('wizard.montage.gotIt')}</span></button>
        </div>
      )}
      {sheet && (
        <div className="mm-sheet" role="dialog" aria-label={sheetTitle}>
          <div className="mm-sheet-h">
            <b className="tx">{sheetTitle}</b>
            <button type="button" className="mm-ic" aria-label={tr('wizard.montage.sheetDone')} onClick={() => setSheet(null)}><Glyph name="check" size={20} sw={2} /></button>
          </div>
          <div className="mm-sheet-b">
            {sheet === 'pace' ? (
              <div className="mm-pace">
                <div className="fxt-seg" role="group" aria-label={tr('wizard.montage.paceGroup')}>
                  {PACES.map((pace, k) => (
                    <button key={pace} type="button" aria-pressed={recipe.pace === pace} onClick={() => changePace(pace)}>
                      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: paceGlyph(k) }} />
                      <b className="tx">{paceLabel(pace)}</b>
                      {recipe.data && <small className="tx">{tr('wizard.montage.shots', { count: recipe.data.cuts[pace].length + 1 })}</small>}
                    </button>
                  ))}
                </div>
                <p className="mm-note tx">{tr('wizard.montage.paceNote')}</p>
                <button type="button" className="fxt-pill" aria-pressed={snap} onClick={() => setSnap((v) => !v)}><Glyph name="magnet" size={16} /><span className="tx">{tr(snap ? 'wizard.montage.snapOn' : 'wizard.montage.snapOff')}</span></button>
              </div>
            ) : (
              <>
                {/* действия выделенного — те же, что в строке выделения на десктопе */}
                {sheet === 'trans' && sel?.type === 'cut' && (
                  <div className="mm-sheet-acts">
                    {cuts.length > 1 && <button type="button" className="fxt-pill" onClick={() => setTransitionAll(transitionAt(sel.i))}><span className="tx">{tr('wizard.montage.allCutsPill', { name: fxName(transitionAt(sel.i)) })}</span></button>}
                    {allPill(() => cutToAll(sel.i))}
                  </div>
                )}
                {sheet === 'style' && sel?.type === 'style' && (() => {
                  const s = styles.find((x) => x.uid === sel.uid);
                  return s ? <div className="mm-sheet-acts"><button type="button" className="fxt-pill" onClick={() => styleWhole(s)}><span className="tx">{tr('wizard.montage.wholeClip')}</span></button>{allPill(() => styleToAll(s))}</div> : null;
                })()}
                {sheet === 'hook' && activeHookLabel && hookIdx.length > 1 && <div className="mm-sheet-acts">{allPill(hookToAll, hookIdx.length)}</div>}
                <Library tab={sheet} setTab={(k) => setSheet(k === 'src' ? null : k)} open={open} setOpen={toggleOpen} used={used} activeHookKind={kind}
                  subStyle={vfx.sub} subPreviews={subPreviews} onPickSub={setSub} textCfg={<SubtitleTextCustomization guideTargetRef={cfgRef} />}
                  onAdd={sheetAdd} onDragStart={() => undefined} tapAdd
                  previewOf={previewOf}
                  frames={frameCatalog} frameId={vfx.frame} onPickFrame={pickFrame}
                  frameNote={combo.vertical ? undefined : tr('wizard.montage.frameWide')}
                  frameBase={clips[0] ? <FrameView frame={clips[0]} thumb /> : null}
                  frameAll={vfx.frame ? allPill(() => toAll((v) => ({ ...v, frame: vfx.frame ?? null }), tr('wizard.montage.frameAllMsg', { name: frameLabel(vfx.frame ?? null) }), verticalIdx), verticalIdx.length) : null} />
              </>
            )}
          </div>
        </div>
      )}
    </main>
  );

  return createPortal(
    <div ref={rootRef} className={`fxt mt${phone ? ' mob' : ''}`} data-format="9:16" role="dialog" aria-modal="true" aria-label={tr('wizard.montage.dialog')} tabIndex={-1}>
      {phone ? mobileHeader : <header className="fxt-top mt-top">
        <div className="mt-top-l">
          <button type="button" className="fxt-back" onClick={onClose} data-tip={tr('wizard.montage.toPoolKey')}><Glyph name="back" size={18} /><span className="tx">{tr('wizard.montage.pool')}</span></button>
          <div className="fxt-proj"><b className="tx">{track?.filename ?? tr('wizard.montage.track')}</b><span className="tx num">{formatTimeRange(start, start + dur)}</span></div>
        </div>
        <div className="mt-vid">
          <div className="mt-arrows">
            <button type="button" className="mt-arrow" aria-label={tr('wizard.montage.prevVideo')} data-tip={tr('wizard.montage.prevVideoKey')} onClick={() => goVideo(-1)}><Glyph name="back" size={16} sw={2} /></button>
            <button type="button" className="mt-arrow" aria-label={tr('wizard.montage.nextVideo')} data-tip={tr('wizard.montage.nextVideoKey')} onClick={() => goVideo(1)}><Glyph name="fwd" size={16} sw={2} /></button>
          </div>
          <div className="fxt-vsw">
            <button type="button" className="fxt-vsw-main" aria-haspopup="listbox" aria-expanded={switchOpen} onClick={() => setSwitchOpen((o) => !o)}>
              <span className="mt-vn num tx">{index + 1}<span className="of">/{total}</span></span>
              <span className="lb tx">{videoLabel(combo)}</span>
              {isEdited(combo) && <i className="mt-dot" data-tip={tr('wizard.montage.hasEdits')} />}
              <svg className="chev" viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="m3.5 6 4.5 4 4.5-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
            {switchOpen && (
              <div className="fxt-vsw-menu mt-vmenu" role="listbox" aria-label={tr('wizard.montage.batchVideos')}>
                {combos.map((c) => (
                  <button key={c.index} type="button" role="option" aria-selected={c.index === index} aria-current={c.index === index} className="fxt-vsw-item" onClick={() => { onIndex(c.index); setSwitchOpen(false); setView('table'); }}>
                    <span className="mt-vthumb"><FrameView frame={clipsOf(c)[0]} thumb /></span>
                    <span className="mt-vn num tx">{c.index + 1}</span>
                    <span className="lb tx">{videoLabel(c)}</span>
                    {isEdited(c) && <span className="mt-edited tx">{tr('wizard.montage.edited')}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="mt-top-r">
          <div className="fxt-seg" role="group" aria-label={tr('wizard.montage.view')}>
            <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')}><Glyph name="table" size={16} /><span className="tx">{tr('wizard.montage.viewVideo')}</span></button>
            <button type="button" aria-pressed={view === 'grid'} onClick={() => { setView('grid'); setPop(null); setSel(null); }}><Glyph name="grid" size={16} /><span className="tx">{tr('wizard.montage.toolGrid')}</span></button>
          </div>
          <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.hotkeys')} aria-expanded={keysOpen} data-tip={tr('wizard.montage.hotkeys')} onClick={() => setKeysOpen((v) => !v)}><Glyph name="keys" size={20} /></button>
          <button type="button" className="fxt-primary" disabled={busy} aria-busy={busy || undefined} onClick={() => { const blocked = onGenerate(); if (blocked) say(blocked); }}>{busy && <span className="spinner" aria-hidden="true" />}<span className="tx">{tr('wizard.montage.generate', { n: total })}</span></button>
        </div>
      </header>}

      {phone && view !== 'grid' ? mobileMain : view === 'grid' ? (
        <main ref={mainRef} className="fxt-main mt-gridmain">
          <div className="mt-grid" style={{ gridTemplateColumns: `repeat(${gridCell.cols}, ${gridCell.w}px)` }}>
            {combos.map((c) => (
              <button key={c.index} type="button" className={`mt-cell${c.index === index ? ' cur' : ''}`} onClick={() => { onIndex(c.index); setView('table'); }} aria-label={tr('wizard.montage.openVideo', { n: c.index + 1 })}
                onPointerEnter={() => setGridHot(c.index)} onPointerLeave={() => setGridHot((h) => (h === c.index ? null : h))}
                onFocus={() => setGridHot(c.index)} onBlur={() => setGridHot((h) => (h === c.index ? null : h))}>
                {c.index === gridLive
                  ? <Stage frames={clipsOf(c)} bounds={bounds} t={t} playing={playing} fx={stageFxFor(c)} sub={subFor(c)} w={gridCell.w} h={gridCell.h} />
                  : <StagePoster frames={clipsOf(c)} bounds={bounds} t={t} fx={stageFxFor(c)} w={gridCell.w} h={gridCell.h} />}
                <span className="mt-cap"><b className="num tx">{c.index + 1}</b><span className="tx">{videoLabel(c)}</span>{isEdited(c) && <i className="mt-dot" />}</span>
              </button>
            ))}
          </div>
          <div className="mt-gridbar">
            <button type="button" className="fxt-icon mt-gplay" aria-label={tr(playing ? 'wizard.montage.pause' : 'wizard.montage.playAll')} onClick={() => setPlaying((v) => !v)}><Glyph name={playing ? 'pause' : 'play'} size={18} /></button>
            <div className="mt-gprog"><i style={{ width: `${(t / dur) * 100}%` }} /></div>
            <span className="tx num mt-gtime">{tc(t)} / {tc(dur)}</span>
          </div>
        </main>
      ) : (
        <main ref={mainRef} className="fxt-main">
          <Library tab={tab} setTab={setTab} open={open} setOpen={toggleOpen} used={used} activeHookKind={kind}
            subStyle={vfx.sub} subPreviews={subPreviews} onPickSub={setSub} textCfg={<SubtitleTextCustomization guideTargetRef={cfgRef} />}
            onAdd={addFromLib} onDragStart={onLibDragStart}
            previewOf={previewOf}
            frames={frameCatalog} frameId={vfx.frame} onPickFrame={pickFrame}
            frameNote={combo.vertical ? undefined : tr('wizard.montage.frameWide')}
            frameBase={clips[0] ? <FrameView frame={clips[0]} thumb /> : null}
            frameAll={vfx.frame ? allPill(() => toAll((v) => ({ ...v, frame: vfx.frame ?? null }), tr('wizard.montage.frameAllMsg', { name: frameLabel(vfx.frame ?? null) }), verticalIdx), verticalIdx.length) : null} />

          <section className="fxt-panel fxt-pv" aria-label={tr('wizard.montage.preview')}>
            <Stage frames={clips} bounds={bounds} t={t} playing={playing} fx={{ transitionAt, styles, hookKind: kind, hookLabel: activeHookLabel, hookRange, frameUrl: frameUrlOf(vfx.frame) }} sub={subFor(combo)} w={stageSize.w} h={stageSize.h}>
              <div className="fxt-chip"><Glyph name="film" size={12} /><span className="tx">{tr('wizard.montage.chipVideo', { n: index + 1, total, bg: chip(combo.bgLabel) })}</span></div>
              {editK === null && <button type="button" className="fxt-play" aria-label={tr(buffering ? 'wizard.montage.loading' : playing ? 'wizard.montage.pause' : 'wizard.montage.play')} aria-busy={buffering || undefined} aria-pressed={playing} onClick={() => setPlaying((v) => !v)}>
                {buffering ? <span className="spinner" aria-hidden="true" /> : <Glyph name={playing ? 'pause' : 'play'} size={20} />}
              </button>}
              {sbVideo && clips.length === shots && (
                <FrameDock combo={combo} video={sbVideo} frames={clips} bounds={bounds} k={editK ?? fNow} drop={drop} dockRef={dockRef}
                  onSeek={(k) => { seek(bounds[k] + 0.001); setSel({ type: 'frame', i: k }); }}
                  onEdit={(k) => { setEditK(k); if (k !== null && !framesGuideDismissed) setFramesGuideDismissed(true); }} onChanged={markEdited} onError={say} />
              )}
              {!sbVideo && combo.bgKey && !combo.bgKey.startsWith('footage:') && combo.bgKey !== '__color__' && (
                <div className="mt-srcnote"><span className="tx">{tr(combo.bgKey.startsWith('photo:') ? 'wizard.montage.srcPhoto' : 'wizard.montage.srcUploadNote')}</span></div>
              )}

            </Stage>
          </section>

          <section ref={tlRef} className="fxt-panel fxt-tl" aria-label={tr('wizard.montage.timeline')}>
            <div className="fxt-bar">
              <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.undo')} data-tip={tr('wizard.montage.undoKey')} disabled={!canUndo} onClick={undo}><Glyph name="undo" size={18} /></button>
              <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.redo')} data-tip={tr('wizard.montage.redoKey')} disabled={!canRedo} onClick={redo}><Glyph name="redo" size={18} /></button>
              <span className="fxt-sep" />
              <div className="fxt-sel">{selInfo()}</div>
              <span className="fxt-sep" />
              <div className="fxt-pace">
                <div className="fxt-seg" role="group" aria-label={tr('wizard.montage.paceGroup')}>
                  {PACES.map((pace, k) => (
                    <button key={pace} type="button" aria-pressed={recipe.pace === pace} aria-label={paceTip(pace)} data-tip={paceTip(pace)} onClick={() => changePace(pace)}>
                      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true" dangerouslySetInnerHTML={{ __html: paceGlyph(k) }} />
                    </button>
                  ))}
                </div>
              </div>
              <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.snap')} aria-pressed={snap} data-tip={tr('wizard.montage.snapTip')} onClick={() => setSnap((v) => !v)}><Glyph name="magnet" size={18} /></button>
              <div className="fxt-zoom">
                <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.zoomOut')} onClick={() => setZoom((z) => clamp(z / 1.4, 1, 6))}><Glyph name="minus" size={16} /></button>
                <input type="range" min={1} max={6} step={0.05} value={zoom} aria-label={tr('wizard.montage.zoomRange')} onChange={(e) => setZoom(Number(e.target.value))} />
                <button type="button" className="fxt-icon" aria-label={tr('wizard.montage.zoomIn')} onClick={() => setZoom((z) => clamp(z * 1.4, 1, 6))}><Glyph name="plus" size={16} /></button>
              </div>
            </div>
            <div className="fxt-body">
              <div className="fxt-heads">
                <div className="rh" />
                {laneHead('h-frames mt-h-src', 'film', 'frame', tr('wizard.montage.laneSources'), sbVideo && Object.keys(sbVideo.pins).length ? <span className="mt-lcount num" data-tip={tr('wizard.montage.pinnedCount')}><Glyph name="lock" size={11} sw={2} />{Object.keys(sbVideo.pins).length}</span> : undefined)}
                {laneHead('h-hook', 'effects', 'hook', tr('wizard.montage.laneHook'))}
                {laneHead('h-s0', 'crystal', 'style', tr('wizard.montage.laneStyle1'), !lane2 ? <button type="button" className="mt-laneadd" aria-label={tr('wizard.montage.addLane2')} data-tip={tr('wizard.montage.lane2Tip')} onClick={() => setLane2Open(true)}><Glyph name="plus" size={13} sw={2} /></button> : undefined)}
                {lane2 && laneHead('h-s1', 'crystal', 'style', tr('wizard.montage.laneStyle2'), !styles.some((x) => x.lane === 1) ? <button type="button" className="mt-laneadd" aria-label={tr('wizard.montage.removeLane2')} data-tip={tr('wizard.montage.removeLane')} onClick={() => setLane2Open(false)}><Glyph name="close" size={12} sw={2} /></button> : undefined)}
                {laneHead('h-subs', 'text', 'trans', tr('wizard.montage.tabSubs'), vfx.sub ? <span className="mt-lcount" data-tip={tr('wizard.montage.subsLaneTip')}>{vfx.sub}</span> : undefined)}
                {laneHead('h-audio', 'audio', 'trans', tr('wizard.montage.laneBeats'))}
              </div>
              <div ref={scrollRef} className="fxt-scroll" onWheel={(e) => { if (e.ctrlKey || e.metaKey) { setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 1, 6)); } else if (zoom > 1 && Math.abs(e.deltaY) > Math.abs(e.deltaX) && scrollRef.current) scrollRef.current.scrollLeft += e.deltaY; }}>
                {canvas}
              </div>
            </div>
          </section>
        </main>
      )}

      <div className={`fxt-toast mt-toast${toast ? ' show' : ''}`} role="status" aria-live="polite" aria-atomic="true"><span className="tx">{toast}</span></div>
      {editedCount > 0 && view === 'grid' && <div className="mt-gridnote"><span className="tx">{tr('wizard.montage.gridNote', { n: editedCount, total })}</span></div>}

      {ghost && <div className="fxt-ghost" style={{ left: ghost.x, top: ghost.y }}>{ghost.item.kind === 'src' ? <Thumb url={ghost.item.url} size={28} /> : <Ic kind={ghost.item.kind === 'text' ? 'trans' : ghost.item.kind as 'hook'} label={ghost.item.kind === 'text' ? 'text' : ghost.item.label} on size={26} />}<span className="tx">{ghost.item.kind === 'src' ? clipTitle(ghost.item.label) : fxName(ghost.item.label)}</span></div>}

      {pop && view === 'table' && pop.i < cuts.length && (() => {
        const vw = window.innerWidth / zoomScale();
        const left = clamp(pop.x - 132, 8, vw - 272); const bottom = Math.max(8, window.innerHeight / zoomScale() - pop.y + 10);
        if (pop.type === 'cut') {
          return <PickPopover title={tr('wizard.montage.popTitle', { n: pop.i + 1 })} stateLabel={tr('wizard.montage.popState', { n: pop.i + 1 })} left={left} bottom={bottom}
            options={GLUE_OPTIONS} current={transitionAt(pop.i)}
            preview={(id) => <LoopStage frames={clips} bounds={bounds} at={cuts[pop.i]} dur={dur} fx={{ transitionAt: (k) => (k === pop.i ? id : transitionAt(k)), styles: [], hookRange: null }} />}
            onApply={(id) => setCutTransition(pop.i, id)}
            footer={<button type="button" className="fxt-pill" onClick={() => { setTransitionAll(transitionAt(pop.i)); setPop(null); }}><span className="tx">{tr('wizard.montage.popAll')}</span></button>} />;
        }
        return null;
      })()}

      <ActionGuideOverlay
        open={showFramesGuide}
        targetRef={dockRef as React.RefObject<HTMLElement>}
        title={tr('wizard.montage.guideFramesTitle')}
        text={tr('wizard.montage.guideFramesText')}
        dismissLabel={tr('wizard.pool.guideNext')}
        progressLabel={tr('wizard.guideProgress', { current: 1, total: 2 })}
        onDismiss={() => setFramesGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<StoryboardReplaceGuideVisual />}
      />
      <ActionGuideOverlay
        open={showLanesGuide}
        targetRef={tlRef as React.RefObject<HTMLElement>}
        title={tr('wizard.montage.guideLanesTitle')}
        text={tr('wizard.montage.guideLanesText', { total })}
        dismissLabel={tr('wizard.pool.guideDismiss')}
        progressLabel={tr('wizard.guideProgress', { current: 2, total: 2 })}
        onDismiss={() => setLanesGuideDismissed(true)}
        variant="visual"
        shell="track-top"
        visual={<TimelineEntryGuideVisual />}
      />

      {keysOpen && (
        <div className="fxt-pop" role="dialog" aria-label={tr('wizard.montage.hotkeys')} style={{ right: 16, top: 64, width: 300 }} onPointerDown={(e) => e.stopPropagation()}>
          {[[tr('wizard.montage.keys.playPause'), tr('wizard.montage.keys.space')], [tr('wizard.montage.keys.videos'), '[ ]'], [tr('wizard.montage.keys.frames'), '← →'], [tr('wizard.montage.keys.cuts'), '↑ ↓'], [tr('wizard.montage.keys.del'), 'Delete'], [tr('wizard.montage.keys.undo'), 'Ctrl+Z / Ctrl+Shift+Z'], [tr('wizard.montage.keys.zoom'), tr('wizard.montage.keys.zoomKey')], [tr('wizard.montage.keys.back'), 'Esc']].map(([a, b]) => (
            <div key={a} className="mt-keyrow"><span className="tx">{a}</span><kbd className="tx">{b}</kbd></div>
          ))}
        </div>
      )}
    </div>,
    document.body
  );
}
