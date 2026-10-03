import { hueAt } from './color';

/**
 * Позиция ползунка радужной шкалы (0–100), ближе всего дающая этот цвет. Ползунок хранит
 * только цвет, поэтому при каждом показе шкалы позицию восстанавливаем из него — иначе
 * сохранённый свой цвет показывался ползунком посередине шкалы.
 */
export function nearestHuePercent(hex: string): number {
  const target = Number.parseInt(hex.replace('#', ''), 16);
  if (!Number.isFinite(target)) return 50;
  const channelsOf = (value: number) => [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  const targetRgb = channelsOf(target);
  let best = 50;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let pct = 0; pct <= 100; pct++) {
    const candidate = Number.parseInt(hueAt(pct).slice(1), 16);
    const [r, g, b] = channelsOf(candidate);
    const distance = (r - targetRgb[0]) ** 2 + (g - targetRgb[1]) ** 2 + (b - targetRgb[2]) ** 2;
    if (distance < bestDistance) { best = pct; bestDistance = distance; }
  }
  return best;
}
