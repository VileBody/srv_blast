import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cssZoom } from '../../lib/zoom';
import { findFont, type SubtitleStyleId } from '../../lib/subtitleText';
import { useSubtitleFonts } from '../../lib/useSubtitleFonts';
import { buildPlan, frameAt, wordsFromLyrics, type Line, type Seg, type SubtitleGeometry, type TimedWord } from '../../lib/subtitleGeometry';
import type { SubtitleTextSettings } from '../../stores/wizardStore';

/*
 * Превью субтитров на canvas: раскладка — lib/subtitleGeometry.ts (правила AE-шаблонов +
 * числа движка рендера с бэка), здесь — отрисовка. Рисуем ТОЛЬКО настоящими шрифтами
 * каталога, которые сайт раздаёт сам (lib/useSubtitleFonts: бандл public/fonts/subtitles по
 * манифесту + залитые на сервер /api/wizard/subtitle-font/<PS>.woff2): у подменного
 * шрифта другие ширины, и строки с фокус-словами разъезжаются с роликом. Шрифта нет на
 * сервере или он не загрузился — превью прямо об этом пишет и ничего не рисует.
 */

/** CSS-шрифт превью: только семейство @font-face с нашего сервера, без запасных. */
const cssFont = (ps: string, size: number) => `${size}px "blast-${ps}"`;

/** Шрифты, которыми рисует стиль (у brat — зафиксированный Arial Narrow и курсив фокуса). */
function neededFonts(g: SubtitleGeometry): string[] {
  if (g.style === 'brat') return [...new Set(['ArialNarrow', g.brat?.focusFont ?? 'ArialNarrow'])];
  return Object.keys(g.fonts);
}

let measureCanvas: CanvasRenderingContext2D | null | undefined;
/** общий контекст для замеров: раскладка считается до того, как появился canvas превью */
function measureCtx(): CanvasRenderingContext2D | null {
  if (measureCanvas === undefined) measureCanvas = typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d');
  return measureCanvas;
}

/** ширина куска в px компа: тот же шрифт и трекинг, что при отрисовке */
function measureSeg(seg: Seg): number {
  const ctx = measureCtx();
  if (!ctx) return 0;
  ctx.font = cssFont(seg.font, seg.size);
  return ctx.measureText(seg.text).width + (seg.tracking / 1000) * seg.size * [...seg.text].length;
}

const SHADOW = {
  jakson: { soft: [[40, 0.85]], strong: [[22, 1], [8, 0.9]] },
  tape: { soft: [[34, 0.9]], strong: [[20, 1], [7, 0.9]] },
  impulse: { soft: [[2, 1], [24, 1], [48, 0.5]], strong: [[2, 1], [38, 1], [80, 0.82]] },
  trendy: { soft: [[90, 0.95]], strong: [[56, 1], [18, 1]] },
} as const;

function paintLines(ctx: CanvasRenderingContext2D, lines: Line[], g: SubtitleGeometry, k: number) {
  for (const line of lines) {
    if (line.alpha <= 0.001) continue;
    if (line.kant) { paintKant(ctx, line, k); continue; }
    const widths = line.segs.map(measureSeg);
    const total = widths.reduce((a, b) => a + b, 0);
    let x = line.align === 'left' ? line.x : line.align === 'right' ? line.x - total : line.x - total / 2;
    ctx.save();
    if (line.scale) {
      ctx.translate(line.scale.px, line.scale.py);
      ctx.scale(line.scale.k, line.scale.k);
      ctx.translate(-line.scale.px, -line.scale.py);
    }
    const layerScale = line.scale?.k ?? 1;
    line.segs.forEach((seg, si) => {
      const w = widths[si];
      const alpha = line.alpha * (seg.alpha ?? 1);
      if (alpha > 0.001 && seg.text.trim()) paintSeg(ctx, seg, line, x, alpha, g, k * layerScale);
      x += w;
    });
    ctx.restore();
  }
}

function paintSeg(ctx: CanvasRenderingContext2D, seg: Seg, line: Line, x: number, alpha: number, g: SubtitleGeometry, k: number) {
  ctx.font = cssFont(seg.font, seg.size);
  const chars = [...seg.text];
  const trackPx = (seg.tracking / 1000) * seg.size;
  // трекинг: штатный letterSpacing (он же попадает в measureText); нет его — буквы по одной
  const ls = 'letterSpacing' in ctx;
  if (ls) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = `${trackPx}px`;
  const charW = (ch: string) => ctx.measureText(ch).width + (ls ? 0 : trackPx);
  const perChar = Boolean(line.chars) || (!ls && trackPx !== 0);
  const vy = line.vScale;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.translate(x, line.y - (seg.shift ?? 0));
  ctx.scale(1, vy);
  if (seg.faux) ctx.transform(1, 0, -0.21, 1, 0, 0);

  const draw = (fn: (text: string, cx: number) => void) => {
    if (perChar) {
      let cx = 0;
      chars.forEach((ch, i) => {
        const p = line.chars ? line.chars(i) : 1;
        const w = charW(ch);
        if (p > 0) {
          ctx.save();
          ctx.globalAlpha = alpha * p;
          // буква влетает снизу и дорастает (аниматор impulse: Position 25, Scale 50)
          const e = 1 - (1 - p) ** 3;
          ctx.translate(cx + w / 2, 25 * (1 - e));
          ctx.scale(0.5 + 0.5 * e, 0.5 + 0.5 * e);
          fn(ch, -w / 2);
          ctx.restore();
        }
        cx += w;
      });
    } else fn(seg.text, 0);
  };

  const style = g.style;
  // brat — своя тень под словом; тайтлы Kant сюда не попадают (paintKant)
  const shadows = g.shadow === 'none' || !(style in SHADOW) ? [] : SHADOW[style as keyof typeof SHADOW][g.shadow];
  const withShadows = (paint: () => void) => {
    for (const [blur, a] of shadows) {
      ctx.shadowBlur = blur * k;
      ctx.shadowColor = `rgba(0,0,0,${a})`; // ui-allow: чёрная тень субтитров из AE-шаблона (данные кадра)
      paint();
    }
    ctx.shadowBlur = 0;
    ctx.shadowColor = 'transparent';
  };

  if (style === 'trendy') {
    // обводка 5 под заливкой + градиент сверху вниз в цвет почти чёрного (S_Gradient в кадре)
    const grad = line.gradient;
    const fill = grad
      ? (() => { const lg = ctx.createLinearGradient(0, (grad.y0 - line.y) / vy, 0, (grad.y1 - line.y) / vy); lg.addColorStop(0, seg.color); lg.addColorStop(0.25, seg.color); lg.addColorStop(1, grad.to); return lg; })()
      : seg.color;
    ctx.lineJoin = 'round';
    ctx.lineWidth = 5;
    ctx.strokeStyle = '#000'; // ui-allow: чёрная обводка trendy (strokeColor шаблона)
    withShadows(() => draw((t, cx) => ctx.strokeText(t, cx, 0)));
    draw((t, cx) => ctx.strokeText(t, cx, 0));
    ctx.fillStyle = fill;
    draw((t, cx) => ctx.fillText(t, cx, 0));
  } else if (style === 'impulse') {
    // обводка в цвет текста — утолщение, не контур; три чёрные тени
    const stroke = g.impulse?.accent && seg.font === g.impulse.accent.font ? g.impulse.accent.stroke : g.impulse?.stroke ?? 3;
    ctx.lineJoin = 'round';
    ctx.lineWidth = stroke * 2;
    ctx.strokeStyle = seg.color;
    ctx.fillStyle = seg.color;
    withShadows(() => draw((t, cx) => { ctx.strokeText(t, cx, 0); ctx.fillText(t, cx, 0); }));
    draw((t, cx) => { ctx.strokeText(t, cx, 0); ctx.fillText(t, cx, 0); });
  } else if (style === 'brat') {
    // Minimax + Gaussian 10: жирно и мягко; тень под каждым словом; вход — размытием
    const B = g.brat;
    ctx.fillStyle = seg.color;
    ctx.strokeStyle = seg.color;
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.filter = `blur(${((2.2 + (line.blur ?? 0)) * k).toFixed(2)}px)`;
    if (B?.wordShadow) {
      const d = (B.wordShadowDistance ?? 6) * 0.8 * k * Math.SQRT1_2;
      // ui-allow: Drop Shadow слова brat (данные кадра)
      ctx.shadowColor = `rgba(0,0,0,${((B.wordShadowOpacity ?? 150) / 255).toFixed(3)})`;
      ctx.shadowBlur = (B.wordShadowSoftness ?? 22) * 0.8 * k;
      ctx.shadowOffsetX = d;
      ctx.shadowOffsetY = d;
    }
    draw((t, cx) => { ctx.strokeText(t, cx, 0); ctx.fillText(t, cx, 0); });
    ctx.filter = 'none';
  } else {
    ctx.fillStyle = seg.color;
    if (style === 'tape') {
      // Glow шаблона — мягкий ореол цветом текста; поверх — тень
      ctx.shadowColor = seg.color;
      ctx.shadowBlur = 16 * k;
      ctx.globalAlpha = alpha * 0.55;
      draw((t, cx) => ctx.fillText(t, cx, 0));
      ctx.globalAlpha = alpha;
    }
    withShadows(() => draw((t, cx) => ctx.fillText(t, cx, 0)));
    draw((t, cx) => ctx.fillText(t, cx, 0));
  }
  ctx.restore();
}

/**
 * Тайтл Kant: буквы по одной (вход шаблона — масштаб/видимость/подмена знака), заливка,
 * обводка и свечение шаблона; у Edit — кадр-плашка, у VHS — дрожание и полосы развёртки.
 * Эффекты AE (Deep Glow, S_Flicker, fisheye) не воспроизводим — только характер.
 */
function paintKant(ctx: CanvasRenderingContext2D, line: Line, k: number) {
  const K = line.kant!;
  const seg = line.segs[0];
  ctx.save();
  ctx.translate(line.x + K.jitter[0], line.y + K.jitter[1]);
  ctx.scale(K.sx, K.sy);
  ctx.font = cssFont(seg.font, seg.size);
  if ('letterSpacing' in ctx) (ctx as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = '0px';
  const trackPx = (seg.tracking / 1000) * seg.size;
  const chars = [...seg.text];
  const widths = chars.map((ch) => ctx.measureText(ch).width + trackPx);
  const total = widths.reduce((a, b) => a + b, 0);
  // строка по центру кадра: базовая линия на полвысоты заглавных ниже центра
  const baseY = seg.size * 0.35;
  const layerK = k * Math.max(K.sx, K.sy);
  if (K.box) {
    const side = seg.size * 0.45;
    ctx.globalAlpha = line.alpha;
    ctx.fillStyle = K.fill;
    ctx.fillRect(-total / 2 - side, -side / 2, side, side);
    ctx.restore();
    return;
  }
  if (K.blur > 0) ctx.filter = `blur(${(K.blur * k).toFixed(2)}px)`;
  const strokeW = K.stroke && K.strokeWidth ? K.strokeWidth * (seg.size / 195) : 0;
  const draw = (text: string, x: number) => {
    if (strokeW) {
      ctx.lineJoin = 'round';
      ctx.lineWidth = strokeW;
      ctx.strokeStyle = K.stroke!;
      ctx.strokeText(text, x, 0);
    }
    ctx.fillStyle = K.fill;
    ctx.fillText(text, x, 0);
  };
  let x = -total / 2;
  chars.forEach((ch, i) => {
    const w = widths[i];
    const gl = K.glyph(i);
    if (gl.alpha > 0.001 && gl.sx > 0.001 && gl.sy > 0.001 && ch.trim()) {
      const text = gl.text ?? ch;
      ctx.save();
      ctx.globalAlpha = line.alpha * gl.alpha;
      ctx.translate(x + w / 2, baseY);
      ctx.scale(gl.sx, gl.sy);
      const cw = ctx.measureText(text).width;
      if (K.glow) {
        // свечение цветом текста (Deep Glow / Glow шаблона)
        ctx.shadowColor = K.glow;
        ctx.shadowBlur = 28 * layerK;
        draw(text, -cw / 2);
        ctx.shadowBlur = 0;
        ctx.shadowColor = 'transparent';
      }
      draw(text, -cw / 2);
      ctx.restore();
    }
    x += w;
  });
  ctx.filter = 'none';
  if (K.scan) {
    // Venetian Blinds шаблона: тонкие тёмные полосы по тексту
    ctx.globalCompositeOperation = 'destination-out';
    ctx.globalAlpha = 0.35;
    ctx.fillStyle = '#000'; // ui-allow: маска полос развёртки (данные кадра)
    const top = baseY - seg.size;
    for (let y = top; y < baseY + seg.size * 0.3; y += 6) ctx.fillRect(-total / 2 - 20, y, total + 40, 2);
  }
  ctx.restore();
}

/** Настройки, от которых зависит геометрия (цвет основного текста — нет: он только краска). */
function geometryKey(settings: SubtitleTextSettings) {
  const { font, accentFont, size, height, shadow, position, accentColor, focusStyle } = settings;
  return { font, accentFont, size, height, shadow, position, accentColor, focusStyle };
}

/** Значение с задержкой: ползунок цвета шлёт событие на каждый сдвиг указателя. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/*
 * Акцентный цвет в запросе геометрии — с задержкой. Геометрию он не меняет, но бэк кладёт его
 * в конфиг стиля (focusColor tape, focusFillColor trendy/brat) и проверяет (#RRGGBB, запрет у
 * тайтла) — поэтому из запроса его не убрать. Раньше POST уходил на каждый pointermove
 * ползунка; теперь один — когда ползунок замер. Сам цвет красим сразу из настроек.
 */
const ACCENT_DEBOUNCE_MS = 200;

export interface SubtitleCanvasProps {
  style: SubtitleStyleId;
  settings: SubtitleTextSettings;
  color: string;
  /** слова с таймингом; пусто — заглушка из текста песни (lyrics) */
  words: TimedWord[];
  /** текст песни: границы фраз (сцены в проде размечаются по ним) */
  lyrics?: string;
  /** время превью в шкале слов; null — первая фраза целиком */
  time: number | null;
  /** плеер на паузе: в пустом месте показать ближайшую фразу целиком */
  rest: boolean;
  /** кадр 16:9 — текст-комп 1080×1920 вложен в 1920×1080, видна его середина */
  wide: boolean;
  /** что показать, если геометрию посчитать нельзя (настройки, которые рендер не примет) */
  onError?: (message: string | null) => void;
}

type FontState = { status: 'loading' | 'ready' | 'missing' | 'failed'; fonts: string[] };

export function SubtitleCanvas({ style, settings, color, words, lyrics, time, rest, wide, onError }: SubtitleCanvasProps) {
  const { t } = useTranslation();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0, z: 1 });
  const [fonts, setFonts] = useState<FontState>({ status: 'loading', fonts: [] });
  const accentLive = settings.accentColor;
  const accentAsked = useDebounced(accentLive, ACCENT_DEBOUNCE_MS);
  const key = geometryKey({ ...settings, accentColor: accentAsked });
  const renderPreset = wide ? 'wide' : 'vertical';
  const geometry = useQuery({
    queryKey: ['subtitle-geometry', style, key, renderPreset],
    queryFn: () => api.subtitleGeometry({ style, settings: key, renderPreset }),
    staleTime: Infinity,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const { catalog, files, settled } = useSubtitleFonts();
  const served = geometry.data && geometry.data.style === style ? geometry.data : undefined;
  // пока запрос с новым цветом ждёт паузы ползунка — акцент (jakson/impulse/tape) уже новый
  const g = useMemo(
    () => (served && accentLive && served.accentColor !== accentLive ? { ...served, accentColor: accentLive } : served),
    [served, accentLive]
  );

  // Шрифты стиля: все должны лежать на сервере и загрузиться — иначе явная ошибка, не подмена
  const needKey = g ? neededFonts(g).join('|') : '';
  useEffect(() => {
    if (!needKey || !settled || typeof document === 'undefined' || !document.fonts) { setFonts({ status: 'loading', fonts: [] }); return; }
    const need = needKey.split('|');
    const missing = need.filter((ps) => !files[ps]);
    if (missing.length) { setFonts({ status: 'missing', fonts: missing }); return; }
    let alive = true;
    setFonts((prev) => (prev.status === 'ready' && prev.fonts.join('|') === needKey ? prev : { status: 'loading', fonts: need }));
    void Promise.allSettled(need.map((ps) => document.fonts.load(cssFont(ps, 40)))).then(() => {
      if (!alive) return;
      const failed = need.filter((ps) => !document.fonts.check(cssFont(ps, 40)));
      setFonts(failed.length ? { status: 'failed', fonts: failed } : { status: 'ready', fonts: need });
    });
    return () => { alive = false; };
  }, [needKey, settled, files]);

  const label = (ps: string) => findFont(catalog, ps)?.label ?? ps;
  const geometryError = geometry.isError ? (geometry.error instanceof Error ? geometry.error.message : String(geometry.error)) : null;
  const fontError = fonts.status === 'missing' ? t('wizard.subs.fontMissing', { fonts: fonts.fonts.map(label).join(', ') })
    : fonts.status === 'failed' ? t('wizard.subs.fontFailed', { fonts: fonts.fonts.map(label).join(', ') }) : null;
  const error = geometryError ?? fontError;
  useEffect(() => { onError?.(error); }, [error, onError]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const read = () => setBox({ w: el.clientWidth, h: el.clientHeight, z: cssZoom(el) });
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const list = useMemo(() => (words.length ? words : lyrics ? wordsFromLyrics(lyrics) : []), [words, lyrics]);
  const ready = fonts.status === 'ready';
  // раскладка — только когда шрифты загружены: ширины меряются тем, чем рисуем
  const plan = useMemo(() => (g && ready ? buildPlan(g, list, lyrics, { color }, measureSeg) : null), [g, ready, list, lyrics, color]);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !box.w || !box.h) return;
    const dpr = (window.devicePixelRatio || 1) * box.z;
    const bw = Math.round(box.w * dpr);
    const bh = Math.round(box.h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, bw, bh);
    if (!plan || !g) return;
    // кадр: вертикаль — комп 1080×1920 на весь плеер; 16:9 — комп в центре кадра 1920×1080
    const frameW = wide ? 1920 : g.comp.w;
    const frameH = wide ? 1080 : g.comp.h;
    const s = Math.min(bw / frameW, bh / frameH);
    const ox = (bw - frameW * s) / 2 + (wide ? ((1920 - g.comp.w) / 2) * s : 0);
    const oy = (bh - frameH * s) / 2 + (wide ? ((1080 - g.comp.h) / 2) * s : 0);
    ctx.setTransform(s, 0, 0, s, ox, oy);
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = 'left';
    paintLines(ctx, frameAt(plan, time, rest), g, s);
  }, [plan, g, time, rest, box, wide]);

  return (
    <div ref={wrapRef} className="w12-sub-canvas" aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
