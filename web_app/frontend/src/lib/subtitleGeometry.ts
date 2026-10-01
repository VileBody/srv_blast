import type { SubtitleStyleId } from './subtitleText';

/*
 * Геометрия субтитров для превью — JS-порт правил AE-шаблонов стиля.
 *
 * Числа (размеры, интервалы, поля, центр, трекинг, вместимость строки) приходят с бэка из
 * того же движка, что считает сборку (`app/subtitle_font_layout.py` →
 * POST /api/wizard/subtitle-geometry). Здесь — то, ЧТО с ними делают шаблоны:
 *   jakson  — app/scenes_3rd_reference_builder.py: сцена = 1–2 строки, TYPE_1 80/120 капсом,
 *             TYPE_2 фокус-слово курсивом/парой, TYPE_4 одно ударное слово крупно и красным;
 *             слова открываются по своим стартам, сцена растёт 85→100% и гаснет на последних 30%;
 *   impulse — app/text_flow_renderer.py: long одной строкой (≤16 знаков, держится на 75%),
 *             буквы влетают по очереди; short (ударное слово) вспухает до пика по центру;
 *   tape    — 4th_template/tape.jsx: 1–2 строки в коробе 900px (разбивка как break_lines),
 *             слова проявляются плавно по своим стартам, фокус цветом;
 *   trendy  — 5th_template/trendy_subtitles.jsx: одно слово = слой до старта следующего,
 *             ужимается под 86%×80% кадра, буквы ×4 по высоте, трекинг 7→−1 за слой;
 *   brat    — 5th_template/brat_subtitles.jsx: блоки по 2 слова в строке до 4 строк (хвост
 *             вливается в прошлую строку), полная выключка в коробе, один кегль на весь ролик,
 *             слово висит от своего старта до конца блока.
 * Эффекты (Minimax, Turbulent, Sapphire) не воспроизводим — только расстановку, тайминг и
 * характер начертания; поэтому превью приблизительное, но строки, переносы и размеры — как в AE.
 */

export interface FontBox {
  capH: number;
  advance: number;
  lcAdvance: number | null;
  bodyTop: number | null;
  bodyBottom: number | null;
  lowercase: boolean;
}

export interface AccentBox { font: string; size: number; tracking: number; baselineShift: number; advance: number; spaceTracking: number }

export interface SubtitleGeometry {
  style: SubtitleStyleId;
  comp: { w: number; h: number };
  alignX: 'left' | 'center' | 'right';
  centerY: number;
  marginX: number;
  marginY: number;
  shadow: 'none' | 'soft' | 'strong';
  accentColor: string | null;
  fonts: Record<string, FontBox>;
  jakson?: {
    font: string; fontFocus: string; sizeBase: number; sizeLine2: number; sizeFocus: number;
    leadingSingle: number; leadingType1: number; capHBase: number; verticalScale: number; tracking: number;
    advanceBase: number; lineCharsType1: number; lineCharsTwoGroups: number; accent: AccentBox | null;
  };
  impulse?: {
    font: string; size: number; stroke: number; anchorY: number; advance: number; safeWidth: number;
    verticalScale: number; tracking: number; longHold: number; shortMinPeak: number;
    accent: { font: string; size: number; stroke: number; anchorY: number; advance: number } | null;
    positionLong: number[]; positionShort: number[];
  };
  tape?: {
    font: string; size: number; leading: number; advance: number; fauxItalic: boolean; case: 'upper' | 'lower';
    verticalScale: number; tracking: number; boxW: number; focusColor: string; accent: AccentBox | null;
  };
  trendy?: {
    font: string; fontSize: number; verticalScale: number; tracking: number; yOffsetRatio: number; uppercase: boolean;
    fitWidthFactor: number; fitHeightFactor: number; alignX: 'left' | 'center' | 'right'; marginX: number; centerYFrac: number;
    focusFillColor?: number[];
    accent?: { font: string; fontSize: number; verticalScale: number; tracking: number; yOffsetRatio: number };
  };
  brat?: {
    boxWFactor: number; fontSize: number; minFontSize: number; leading: number; centerXFrac: number; centerYFrac: number;
    layerScale: number; tracking: number; wordsPerLine: number; maxLines: number; fitMargin: number;
    focusStyle: 'italic' | 'bold_italic' | 'faux_italic' | null;
    focusFont?: string; focusFauxItalic?: boolean; focusFillColor?: number[];
    wordShadow?: boolean; wordShadowOpacity?: number; wordShadowDistance?: number; wordShadowSoftness?: number;
  };
}

/** Слово с таймингом (секунды — в той же шкале, что и время превью). */
export interface TimedWord { text: string; start: number; end: number; focus?: boolean }

/** Кусок строки одним шрифтом (слово с пробелом после него). */
export interface Seg {
  text: string;
  font: string;          // PostScript-имя (как в AE)
  size: number;          // pt = px компа 1080×1920
  tracking: number;      // AE: 1/1000 em
  upper: boolean;        // капс (для подгонки чернил, если шрифта нет у человека)
  color: string;
  alpha?: number;
  faux?: boolean;        // faux italic (курсив/жирный задаёт сам PostScript-шрифт)
  shift?: number;        // сдвиг базовой линии вверх, px
}

/** Строка (слой) в координатах компа. */
export interface Line {
  segs: Seg[];
  x: number;             // точка выравнивания
  y: number;             // базовая линия
  align: 'left' | 'center' | 'right';
  vScale: number;
  alpha: number;
  /** масштаб слоя вокруг точки (AE Scale/Geometry) */
  scale?: { px: number; py: number; k: number };
  /** посимвольное появление (impulse): доля [0..1] для буквы i */
  chars?: (i: number) => number;
  /** brat: дополнительное размытие входа (px компа) */
  blur?: number;
  /** trendy: градиент заливки по Y компа (как S_Gradient шаблона) */
  gradient?: { y0: number; y1: number; to: string };
}

export type Measure = (seg: Seg) => number;

/**
 * play — живой кадр со всеми анимациями; settled — пауза: открыто то, что успело открыться к t,
 * но без входа/выхода в процессе (кадр не застывает в размытии или на середине затухания);
 * full — фраза целиком (пауза между фразами или превью без плеера).
 */
type FrameMode = 'play' | 'settled' | 'full';

interface Group {
  tIn: number;
  tOut: number;
  /** момент «всё открыто» — для кадра на паузе */
  restT: number;
  lines: (t: number, mode?: FrameMode) => Line[];
}

export interface FrameOptions { color: string }

const COMP_W = 1080;
const COMP_H = 1920;
const FRAME = 1 / 23.976;

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const easeOut = (p: number) => 1 - (1 - p) ** 3;
const rgbHex = (rgb?: number[]) => (rgb && rgb.length >= 3 ? `#${rgb.slice(0, 3).map((c) => Math.round(clamp(c, 0, 1) * 255).toString(16).padStart(2, '0')).join('')}` : null);

/** Чистка слова как у рендера: без знаков по краям (дефис/апостроф внутри слова остаются). */
export function cleanWord(text: string): string {
  return text.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
}

const norm = (text: string) => cleanWord(text).toLowerCase().replace(/ё/g, 'е');

/**
 * Номер строки текста песни для каждого слова: сцены в проде размечает Stage2 по фразам,
 * а фраза почти всегда — строка песни. Слова ASR идут по тексту по порядку, поэтому
 * сопоставляем последовательно, с небольшим окном вперёд на пропуски выравнивателя.
 */
export function phraseIndex(words: TimedWord[], lyrics?: string): number[] {
  const tokens: { t: string; line: number }[] = [];
  (lyrics ?? '').split(/\n+/).filter((l) => l.trim()).forEach((line, li) => {
    for (const tok of line.split(/\s+/)) { const t = norm(tok); if (t) tokens.push({ t, line: li }); }
  });
  const out: number[] = [];
  let p = 0;
  let line = 0;
  let prevEnd = -Infinity;
  let gapPhrase = 0;
  for (const w of words) {
    const t = norm(w.text);
    let found = -1;
    for (let j = p; j < Math.min(tokens.length, p + 5); j++) if (tokens[j].t === t) { found = j; break; }
    if (found >= 0) { line = tokens[found].line; p = found + 1; } else if (p < tokens.length) { line = tokens[p].line; p += 1; }
    // длинная пауза — тоже граница фразы (если текста нет или он не совпал)
    if (w.start - prevEnd > 0.8) gapPhrase += 1;
    prevEnd = w.end;
    out.push(tokens.length ? line * 1000 + gapPhrase : gapPhrase);
  }
  return out;
}

function phrases(words: TimedWord[], lyrics?: string): TimedWord[][] {
  const idx = phraseIndex(words, lyrics);
  const out: TimedWord[][] = [];
  words.forEach((w, i) => { if (i === 0 || idx[i] !== idx[i - 1]) out.push([]); out[out.length - 1].push(w); });
  return out;
}

const chars = (list: TimedWord[]) => list.map((w) => cleanWord(w.text)).join(' ').length;

/** выход группы: до старта следующей, если она близко, иначе чуть после последнего слова */
function groupOut(last: TimedWord, next: TimedWord | undefined, tail: number): number {
  if (next && next.start - last.end < 1) return Math.max(last.end, next.start);
  return last.end + tail;
}

function alignX(g: SubtitleGeometry): number {
  return g.alignX === 'left' ? COMP_W * g.marginX : g.alignX === 'right' ? COMP_W * (1 - g.marginX) : COMP_W / 2;
}

/* ───────────────────────── jakson ───────────────────────── */

function jaksonGroups(g: SubtitleGeometry, words: TimedWord[], lyrics: string | undefined, o: FrameOptions, measure: Measure): Group[] {
  const J = g.jakson!;
  const upper = !(g.fonts[J.font]?.lowercase);
  const cap = J.lineCharsType1 + J.lineCharsTwoGroups;
  const scenes: TimedWord[][] = [];
  for (const ph of phrases(words, lyrics)) {
    let cur: TimedWord[] = [];
    for (const w of ph) {
      if (cur.length && chars([...cur, w]) > cap) { scenes.push(cur); cur = []; }
      cur.push(w);
    }
    if (cur.length) scenes.push(cur);
  }
  const cx = alignX(g);
  const cy = COMP_H * g.centerY;
  const safeW = COMP_W * (1 - 2 * g.marginX);
  const capOf = (ps: string, size: number) => ((g.fonts[ps]?.capH ?? 70) * size / 100) * J.verticalScale;
  const caseOf = (s: string) => (upper ? s.toUpperCase() : s.toLowerCase());

  return scenes.map((scene, si) => {
    const next = scenes[si + 1]?.[0];
    const tIn = scene[0].start;
    const tOut = Math.max(tIn + 0.3, groupOut(scene[scene.length - 1], next, 0.45));
    const dur = tOut - tIn;
    const focusIdx = scene.findIndex((w) => w.focus);
    const type = scene.length === 1 && focusIdx === 0 ? 4 : focusIdx >= 0 ? 2 : 1;

    // строки сцены
    let lines: TimedWord[][];
    if (type === 4 || scene.length === 1) lines = [scene];
    else if (type === 1) {
      // TYPE_1: вторая (крупная) строка — самый длинный хвост, что влезает в её вместимость
      let k = 1;
      while (k < scene.length - 1 && chars(scene.slice(k)) > J.lineCharsType1) k += 1;
      lines = [scene.slice(0, k), scene.slice(k)];
    } else {
      // TYPE_2: две смысловые группы — ровнее по длине
      let best = 1;
      let bestW = Infinity;
      for (let k = 1; k < scene.length; k++) {
        const wmax = Math.max(chars(scene.slice(0, k)), chars(scene.slice(k)));
        if (wmax < bestW) { bestW = wmax; best = k; }
      }
      lines = chars(scene) <= J.lineCharsTwoGroups && scene.length <= 2 ? [scene] : [scene.slice(0, best), scene.slice(best)];
    }

    const sizeOf = (li: number) => (type === 4 ? J.sizeFocus : type === 1 && lines.length > 1 && li === 1 ? J.sizeLine2 : type === 1 && lines.length === 1 ? J.sizeLine2 : J.sizeBase);
    const fontOf = () => (type === 4 ? J.fontFocus : J.font);
    const leading = type === 1 ? J.leadingType1 : J.leadingSingle;

    // открытие слов по стартам; короткое последнее слово строки открывается вместе с предыдущим
    const starts = scene.map((w) => w.start);
    let flat = 0;
    for (const line of lines) {
      flat += line.length;
      const i = flat - 1;
      if (i > 0 && scene[i].end - scene[i].start < 0.43) starts[i] = starts[i - 1];
    }

    const segsFor = (t: number, full: boolean): Seg[][] => {
      let wi = 0;
      return lines.map((line, li) => line.map((w, k) => {
        const reveal = full || wi === 0 || t >= starts[wi] ? 1 : 0;
        wi += 1;
        const text = cleanWord(w.text) + (k < line.length - 1 ? ' ' : '');
        const size = sizeOf(li);
        if (type === 4) return { text: caseOf(text), font: fontOf(), size, tracking: J.tracking, upper, color: g.accentColor ?? o.color, alpha: reveal };
        if (type === 2 && w.focus && J.accent) {
          return { text: text.toLowerCase(), font: J.accent.font, size: J.accent.size, tracking: J.accent.tracking, upper: false, color: o.color, alpha: reveal, shift: J.accent.baselineShift };
        }
        return { text: caseOf(text), font: type === 2 && w.focus ? J.fontFocus : J.font, size, tracking: J.tracking, upper, color: o.color, alpha: reveal, faux: type === 2 && w.focus && !J.accent };
      }));
    };

    // вписать в безопасную зону (AE-ширина строки = сумма ширин слов)
    const base = segsFor(Infinity, true);
    const widest = Math.max(...base.map((segs) => segs.reduce((s, seg) => s + measure(seg), 0)));
    const fit = widest > safeW ? safeW / widest : 1;
    const cap1 = capOf(fontOf(), sizeOf(0));
    const blockH = cap1 + leading * (lines.length - 1);
    const y0 = cy - blockH / 2 + cap1;
    const fadeDur = Math.max(dur * 0.3, FRAME * 5);
    const x = type === 4 ? COMP_W / 2 : cx;
    const align = type === 4 ? 'center' : g.alignX;

    return {
      tIn, tOut,
      restT: Math.max(tIn, Math.min(starts[starts.length - 1] + 0.05, tOut)),
      lines: (t: number, mode: FrameMode = 'play') => {
        const alpha = mode === 'play' && t > tOut - fadeDur ? clamp((tOut - t) / fadeDur, 0, 1) : 1;
        // Geometry2: сцена растёт 85→100% до (выход + 0.5 с)
        const k = (0.85 + 0.15 * clamp((t - tIn) / (dur + 0.5), 0, 1)) * fit;
        return segsFor(t, mode === 'full').map((segs, li) => ({
          segs, x, y: y0 + leading * li * fit, align, vScale: J.verticalScale, alpha,
          scale: { px: COMP_W / 2, py: cy, k },
        }));
      },
    };
  });
}

/* ───────────────────────── impulse ───────────────────────── */

function impulseGroups(g: SubtitleGeometry, words: TimedWord[], lyrics: string | undefined, o: FrameOptions, measure: Measure): Group[] {
  const I = g.impulse!;
  type Seg2 = { words: TimedWord[]; short: boolean };
  const segs: Seg2[] = [];
  let longsSinceShort = 2;
  for (const ph of phrases(words, lyrics)) {
    let cur: TimedWord[] = [];
    const flush = () => { if (cur.length) { segs.push({ words: cur, short: false }); longsSinceShort += 1; cur = []; } };
    ph.forEach((w, i) => {
      const after = ph[i + 1]?.start ?? words[words.indexOf(w) + 1]?.start ?? Infinity;
      // short: фокус-слово человека или ударное в конце фразы (≥0.4 с и пауза ≥0.4 с), не чаще 1 на 2 строки
      const auto = i === ph.length - 1 && w.end - w.start >= 0.4 && after - w.end >= 0.4 && longsSinceShort >= 2 && cleanWord(w.text).length <= 10;
      if (w.focus || auto) { flush(); segs.push({ words: [w], short: true }); longsSinceShort = 0; return; }
      if (cur.length && chars([...cur, w]) > 16) flush();
      cur.push(w);
    });
    flush();
  }
  const text = (s: Seg2) => s.words.map((w) => cleanWord(w.text).toLowerCase()).join(' ');
  const posL = I.positionLong;
  const posS = I.positionShort;

  return segs.map((s, si) => {
    const next = segs[si + 1];
    const first = s.words[0];
    const last = s.words[s.words.length - 1];
    const tIn = first.start;
    const tOut = Math.max(tIn + 0.25, groupOut(last, next?.words[0], 0.3));
    const str = text(s);
    if (!s.short) {
      const n = str.length;
      const delay = n > 1 ? clamp((tOut - tIn - 7 * FRAME - 0.1) / (n - 1), 0.005, 0.05) : 0.05;
      const inT = Math.max(0, tIn - delay * (n - 1) * 0.25);
      const seg: Seg = { text: str, font: I.font, size: I.size, tracking: I.tracking, upper: false, color: o.color };
      const width = measure(seg) + 2 * I.stroke;
      const hold = Math.min(I.longHold, (100 * I.safeWidth) / Math.max(1, width)) / 100;
      // выход: ударное слово сразу за строкой «съедает» её (масштаб → 0), иначе — последние 7 кадров
      const exitAt = next?.short ? Math.max(next.words[0].start, inT + delay * (n - 1) + 0.15) : tOut - 7 * FRAME;
      const exitEnd = next?.short ? exitAt + 7 * FRAME : tOut;
      return {
        tIn: inT, tOut: Math.max(tOut, exitEnd),
        restT: Math.max(inT, Math.min(inT + delay * n + 0.3, exitAt - 0.01)),
        lines: (t: number, mode: FrameMode = 'play') => {
          const ex = mode === 'play' && t > exitAt ? clamp((t - exitAt) / Math.max(FRAME, exitEnd - exitAt), 0, 1) : 0;
          return [{
            segs: [seg], x: posL[0], y: posL[1] - I.anchorY, align: g.alignX, vScale: I.verticalScale, alpha: 1 - ex * 0.6,
            scale: { px: posL[0], py: posL[1], k: hold * (1 - ex) },
            chars: mode === 'full' ? undefined
              : mode === 'settled' ? (i: number) => (t >= inT + i * delay ? 1 : 0)
                : (i: number) => clamp((t - (inT + i * delay)) / 0.22, 0, 1),
          }];
        },
      };
    }
    const acc = I.accent;
    const seg: Seg = { text: str, font: acc?.font ?? I.font, size: acc?.size ?? I.size, tracking: acc ? 0 : I.tracking, upper: false, color: g.accentColor ?? o.color };
    const width = measure(seg) + 2 * (acc?.stroke ?? I.stroke);
    const dur = tOut - tIn;
    const byWidth = Math.round((100 * I.safeWidth) / Math.max(1, width));
    const byDur = Math.round(190 + (1 - dur) * 180);
    const peak = Math.max(Math.min(I.shortMinPeak, byWidth), Math.min(byDur, byWidth)) / 100;
    const mid = tIn + dur * 0.5;
    return {
      tIn, tOut,
      restT: mid,
      lines: (t: number, mode: FrameMode = 'play') => {
        const grow = 0.75 + (peak - 0.75) * easeOut(clamp((t - tIn) / (mid - tIn), 0, 1));
        const k = mode === 'full' ? peak : t < mid ? grow : mode === 'settled' ? peak : peak * (1 - clamp((t - mid) / (tOut - mid), 0, 1));
        const alpha = mode === 'play' && t > tOut - 3 * FRAME ? clamp((tOut - t) / (3 * FRAME), 0, 1) : 1;
        return [{
          segs: [seg], x: posS[0], y: posS[1] - (acc?.anchorY ?? I.anchorY), align: 'center' as const,
          vScale: acc ? 1 : I.verticalScale, alpha, scale: { px: posS[0], py: posS[1], k },
        }];
      },
    };
  });
}

/* ───────────────────────── tape ───────────────────────── */

function tapeGroups(g: SubtitleGeometry, words: TimedWord[], lyrics: string | undefined, o: FrameOptions): Group[] {
  const T = g.tape!;
  const upper = T.case !== 'lower';
  const wordW = (w: TimedWord) => cleanWord(w.text).length * (w.focus && T.accent ? T.accent.advance : T.advance);
  // как TapeLayout.break_lines: одна строка, если влезает; иначе две по границе слов с минимальной длинной строкой
  const breakLines = (list: TimedWord[]): TimedWord[][] => {
    const widths = list.map(wordW);
    const total = widths.reduce((a, b) => a + b, 0) + T.advance * (list.length - 1);
    if (list.length < 2 || total <= T.boxW) return [list];
    let best = 1;
    let bestW = Infinity;
    for (let k = 1; k < list.length; k++) {
      const wa = widths.slice(0, k).reduce((a, b) => a + b, 0) + T.advance * (k - 1);
      const wb = widths.slice(k).reduce((a, b) => a + b, 0) + T.advance * (list.length - k - 1);
      if (Math.max(wa, wb) < bestW) { bestW = Math.max(wa, wb); best = k; }
    }
    return [list.slice(0, best), list.slice(best)];
  };
  const fits = (list: TimedWord[]) => {
    const lines = breakLines(list);
    return lines.every((l) => l.reduce((s, w) => s + wordW(w), 0) + T.advance * (l.length - 1) <= T.boxW);
  };
  const scenes: TimedWord[][] = [];
  for (const ph of phrases(words, lyrics)) {
    let cur: TimedWord[] = [];
    for (const w of ph) {
      if (cur.length && !fits([...cur, w])) { scenes.push(cur); cur = []; }
      cur.push(w);
    }
    if (cur.length) scenes.push(cur);
  }
  const ant = 2 * FRAME;
  const fade = 2 * FRAME;
  const capPx = ((upper ? g.fonts[T.font]?.capH : g.fonts[T.font]?.bodyTop) ?? 70) * T.size / 100 * T.verticalScale;
  const cx = alignX(g);
  const cy = COMP_H * g.centerY;
  const focusColor = g.accentColor ?? T.focusColor;

  return scenes.map((scene, si) => {
    const next = scenes[si + 1]?.[0];
    const tIn = Math.max(0, scene[0].start - ant);
    const tOut = Math.max(tIn + 0.3, groupOut(scene[scene.length - 1], next, 0.4));
    const lines = breakLines(scene);
    const y0 = cy - (capPx + T.leading * (lines.length - 1)) / 2 + capPx;
    const order = new Map(scene.map((w, i) => [w, i]));
    // проявление по словам (Range Selector по словам, сглаживание 100): слово k проявляется
    // от своего старта (с упреждением 2 кадра) к старту следующего
    const revealOf = (w: TimedWord, t: number) => {
      const i = order.get(w) ?? 0;
      if (scene.length < 2) return clamp((t - tIn) / Math.max(0.1, (tOut - fade - tIn) * 0.7), 0, 1);
      const a = Math.max(tIn, w.start - ant);
      const b = Math.max(a + 0.08, Math.min(scene[i + 1]?.start ?? w.end, w.end) - ant);
      return clamp((t - a) / (b - a), 0, 1);
    };
    return {
      tIn, tOut,
      restT: Math.max(tIn, Math.min(scene[scene.length - 1].end, tOut)),
      lines: (t: number, mode: FrameMode = 'play') => {
        const alpha = mode === 'play' && t > tOut - fade ? clamp((tOut - t) / fade, 0, 1) : 1;
        return lines.map((line, li) => ({
          segs: line.map((w, k) => {
            const raw = cleanWord(w.text) + (k < line.length - 1 ? ' ' : '');
            const focusAccent = w.focus && T.accent;
            return {
              text: focusAccent || !upper ? raw.toLowerCase() : raw.toUpperCase(),
              font: focusAccent ? T.accent!.font : T.font,
              size: focusAccent ? T.accent!.size : T.size,
              tracking: focusAccent ? T.accent!.tracking : T.tracking,
              upper: !focusAccent && upper,
              color: w.focus ? focusColor : o.color,
              alpha: mode === 'full' ? 1 : mode === 'settled' ? (t >= w.start - ant ? 1 : 0) : revealOf(w, t),
              faux: !focusAccent && T.fauxItalic,
              shift: focusAccent ? T.accent!.baselineShift : 0,
            } satisfies Seg;
          }),
          x: cx, y: y0 + T.leading * li, align: g.alignX, vScale: T.verticalScale, alpha,
        }));
      },
    };
  });
}

/* ───────────────────────── trendy ───────────────────────── */

function trendyGroups(g: SubtitleGeometry, words: TimedWord[], o: FrameOptions, measure: Measure): Group[] {
  const C = g.trendy!;
  const maxW = COMP_W * C.fitWidthFactor;
  const maxH = COMP_H * C.fitHeightFactor;
  const cx = C.alignX === 'left' ? COMP_W * C.marginX : C.alignX === 'right' ? COMP_W * (1 - C.marginX) : COMP_W / 2;
  const cy = COMP_H * C.centerYFrac;
  const focusColor = rgbHex(C.focusFillColor);
  return words.map((w, i) => {
    const next = words[i + 1];
    const tIn = w.start;
    const tOut = Math.max(tIn + 0.1, next ? next.start : w.end + 0.3);
    const acc = w.focus && C.accent ? C.accent : null;
    const st = acc ?? C;
    const upper = acc ? false : C.uppercase;
    const raw = cleanWord(w.text);
    const text = upper ? raw.toUpperCase() : raw.toLowerCase();
    const font = acc ? acc.font : C.font;
    const box = g.fonts[font];
    const inkH = ((upper ? box?.capH : box?.bodyTop) ?? 70) / 100;
    // авто-уменьшение под ширину/высоту, как цикл шаблона (до 4 проходов, не мельче 40)
    let size = st.fontSize;
    for (let pass = 0; pass < 4; pass++) {
      const width = measure({ text, font, size, tracking: st.tracking, upper, color: o.color });
      const s = Math.min(maxW / width, maxH / (size * inkH * st.verticalScale));
      if (s >= 1 || size <= 40) break;
      size = Math.max(40, Math.floor(size * s * 0.99));
    }
    const color = w.focus && focusColor ? focusColor : o.color;
    return {
      tIn, tOut,
      restT: tIn + Math.min(0.15, (tOut - tIn) / 2),
      lines: (t: number) => {
        // аниматор Tracking Amount 7 → −1 за длину слоя (easy ease)
        const p = clamp((t - tIn) / (tOut - tIn), 0, 1);
        const e = p < 0.5 ? 2 * p * p : 1 - (-2 * p + 2) ** 2 / 2;
        const tracking = st.tracking + 7 + (-1 - 7) * e;
        return [{
          segs: [{ text, font, size, tracking, upper, color }],
          x: cx, y: cy + size * st.verticalScale * st.yOffsetRatio, align: C.alignX, vScale: st.verticalScale, alpha: 1,
          // S_Gradient шаблона: из цвета текста в почти чёрный между y 772 и 1570 кадра
          gradient: { y0: 772, y1: 1570, to: '#0b0b0b' }, // ui-allow: цвет данных кадра
        }];
      },
    };
  });
}

/* ───────────────────────── brat ───────────────────────── */

const BRAT_FONT = 'ArialNarrow';

function bratGroups(g: SubtitleGeometry, words: TimedWord[], o: FrameOptions, measure: Measure): Group[] {
  const B = g.brat!;
  const per = B.wordsPerLine * B.maxLines;
  type Block = { words: TimedWord[]; lines: TimedWord[][] };
  const blocks: Block[] = [];
  for (let i = 0; i < words.length; i += per) {
    const slice = words.slice(i, i + per);
    const lines: TimedWord[][] = [];
    for (let j = 0; j < slice.length; j += B.wordsPerLine) lines.push(slice.slice(j, j + B.wordsPerLine));
    // одиночный хвост вливается в предыдущую строку — строк из одного слова не бывает
    if (lines.length > 1 && lines[lines.length - 1].length < B.wordsPerLine) lines[lines.length - 2].push(...lines.pop()!);
    blocks.push({ words: slice, lines });
  }
  const focusFont = B.focusFont ?? BRAT_FONT;
  const segOf = (w: TimedWord, size: number): Seg => ({
    text: cleanWord(w.text).toLowerCase(),
    font: w.focus ? focusFont : BRAT_FONT,
    faux: Boolean(w.focus && B.focusFauxItalic),
    size, tracking: B.tracking, upper: false,
    color: w.focus && B.focusFillColor ? rgbHex(B.focusFillColor)! : o.color,
  });
  const boxW = COMP_W * B.boxWFactor;
  // ГЛОБАЛЬНЫЙ fit: один кегль на весь ролик — самая широкая строка влезает в 97% короба
  let maxW = 1;
  for (const b of blocks) for (const line of b.lines) {
    const w = line.reduce((s, word, j) => s + measure(segOf(word, B.fontSize)) + (j ? measure({ ...segOf(word, B.fontSize), text: ' ' }) : 0), 0);
    maxW = Math.max(maxW, w);
  }
  const avail = boxW * B.fitMargin;
  const fit = maxW > avail ? Math.max(B.minFontSize, B.fontSize * (avail / maxW)) : B.fontSize;
  const lineStep = fit * (B.leading / B.fontSize) * B.layerScale;
  const rowX = COMP_W * B.centerXFrac;
  const cy = COMP_H * B.centerYFrac;
  const blur = 6 * (1 / 30);

  return blocks.map((block, bi) => {
    const t0 = block.words[0].start;
    let out = block.words[block.words.length - 1].end;
    const next = blocks[bi + 1];
    if (block.lines.length === B.maxLines && next && next.words[0].start > t0) out = next.words[0].start;
    out = Math.max(out, t0 + FRAME);
    // раскладка один раз: полная выключка — зазор между словами добирает ширину короба
    const placed = block.lines.map((line, row) => {
      const ms = line.map((w) => measure(segOf(w, fit)));
      const natural = ms.reduce((a, b) => a + b, 0);
      const gap = line.length > 1 ? (boxW - natural) / (line.length - 1) : 0;
      let cursor = -boxW / 2;
      const rowY = cy + (row - (block.lines.length - 1) / 2) * lineStep;
      return line.map((w, i) => {
        const center = cursor + ms[i] / 2;
        cursor += ms[i] + gap;
        return { w, x: rowX + center * B.layerScale, y: rowY };
      });
    });
    return {
      tIn: t0, tOut: out,
      restT: clamp(block.words[block.words.length - 1].start + 0.25, t0, out - 0.01),
      lines: (t: number, mode: FrameMode = 'play') => placed.flat().filter((p) => mode === 'full' || t >= p.w.start).map((p) => {
        const seg = segOf(p.w, fit * B.layerScale);
        const enter = mode === 'play' ? clamp((t - p.w.start) / blur, 0, 1) : 1;
        return { segs: [seg], x: p.x, y: p.y, align: 'center' as const, vScale: 1, alpha: 1, blur: 14 * (1 - enter) };
      }),
    };
  });
}

/* ───────────────────────── кадр ───────────────────────── */

export interface SubtitlePlan { groups: Group[] }

export function buildPlan(g: SubtitleGeometry, words: TimedWord[], lyrics: string | undefined, o: FrameOptions, measure: Measure): SubtitlePlan {
  const list = words.filter((w) => cleanWord(w.text)).sort((a, b) => a.start - b.start);
  if (!list.length) return { groups: [] };
  switch (g.style) {
    case 'jakson': return { groups: g.jakson ? jaksonGroups(g, list, lyrics, o, measure) : [] };
    case 'impulse': return { groups: g.impulse ? impulseGroups(g, list, lyrics, o, measure) : [] };
    case 'tape': return { groups: g.tape ? tapeGroups(g, list, lyrics, o) : [] };
    case 'trendy': return { groups: g.trendy ? trendyGroups(g, list, o, measure) : [] };
    case 'brat': return { groups: g.brat ? bratGroups(g, list, o, measure) : [] };
    default: return { groups: [] };
  }
}

/**
 * Строки кадра на момент t. rest — плеер на паузе: если в t ничего не видно, показываем
 * ближайшую следующую группу (или последнюю) целиком открытой, чтобы превью не пустело.
 */
export function frameAt(plan: SubtitlePlan, t: number | null, rest: boolean): Line[] {
  const groups = plan.groups;
  if (!groups.length) return [];
  if (t !== null) {
    const active = groups.filter((gr) => t >= gr.tIn && t < gr.tOut);
    if (active.length) return active.flatMap((gr) => gr.lines(t, rest ? 'settled' : 'play'));
    if (!rest) return [];
  }
  const pick = t === null ? groups[0] : groups.find((gr) => gr.tIn >= t) ?? groups[groups.length - 1];
  return pick.lines(pick.restT, 'full');
}

/** Слова-заглушки из текста песни, пока нет распознавания: по ~0.4 с на слово. */
export function wordsFromLyrics(lyrics: string, limitLines = 2): TimedWord[] {
  const out: TimedWord[] = [];
  let t = 0;
  for (const line of lyrics.split(/\n+/).map((l) => l.trim()).filter(Boolean).slice(0, limitLines)) {
    for (const tok of line.split(/\s+/)) {
      if (!cleanWord(tok)) continue;
      out.push({ text: tok, start: t, end: t + 0.36 });
      t += 0.42;
    }
    t += 0.5;
  }
  return out;
}
