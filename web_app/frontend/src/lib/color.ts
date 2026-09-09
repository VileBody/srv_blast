/*
 * Палитра этапа «Фон» (Figma Wireframe 14/15).
 * Радужная полоса — точные стопы из макета (узел 590:1190),
 * цвет вычисляется интерполяцией между теми же стопами,
 * чтобы позиция ползунка всегда совпадала с выбираемым цветом.
 */
const HUE_STOPS: [number, string][] = [
  [0, '#ff0000'],
  [13.942, '#f2b500'],
  [24.519, '#6fba00'],
  [35.106, '#00bf30'],
  [45.196, '#1bc5ba'],
  [56.27, '#388ecc'],
  [66.359, '#5368a2'],
  [77.926, '#8d6a93'],
  [89.246, '#a94795'],
  [99.095, '#990000']
];

export const HUE_GRADIENT = `linear-gradient(90deg, ${HUE_STOPS.map(([p, c]) => `${c} ${p}%`).join(', ')})`;

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  const h = (v: number) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/** Позиция на радужной полосе (0–100%) → hex по стопам Figma */
export function hueAt(percent: number): string {
  const p = Math.min(HUE_STOPS[HUE_STOPS.length - 1][0], Math.max(0, percent));
  for (let i = 1; i < HUE_STOPS.length; i++) {
    const [p0, c0] = HUE_STOPS[i - 1];
    const [p1, c1] = HUE_STOPS[i];
    if (p <= p1) {
      const t = p1 === p0 ? 0 : (p - p0) / (p1 - p0);
      const [r0, g0, b0] = hexToRgb(c0);
      const [r1, g1, b1] = hexToRgb(c1);
      return rgbToHex(r0 + (r1 - r0) * t, g0 + (g1 - g0) * t, b0 + (b1 - b0) * t);
    }
  }
  return HUE_STOPS[HUE_STOPS.length - 1][1];
}

/** Плоскость оттенка (Figma: linear-gradient(90deg, #fff 0%, hue 100%)) — 0% = белый, 100% = чистый тон */
export function mixWithWhite(hex: string, percent: number): string {
  const [r, g, b] = hexToRgb(hex);
  const t = Math.min(100, Math.max(0, percent)) / 100;
  return rgbToHex(255 + (r - 255) * t, 255 + (g - 255) * t, 255 + (b - 255) * t);
}
