/*
 * Единый вид времени в подписях визарда (не в полях ввода — те принимают свои форматы).
 *   грубое время — «мм:сс»     (00:38): линейки, длительности, дропы из анализа;
 *   точное время — «мм:сс.сс»  (00:38.50): тайминги слов, плейхед, правленые вручную точки.
 * Тройку «00:38:50» в подписях не показываем: её читали как «38 минут 50 секунд».
 */

const pad = (n: number) => String(n).padStart(2, '0');

/** «мм:сс» — до ближайшей секунды. */
export function formatTimeCoarse(seconds: number): string {
  const total = Math.round(Math.max(0, seconds));
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`;
}

/** «мм:сс.сс» — до сотой. */
export function formatTimePrecise(seconds: number): string {
  const centi = Math.round(Math.max(0, seconds) * 100);
  return `${pad(Math.floor(centi / 6000))}:${pad(Math.floor(centi / 100) % 60)}.${pad(centi % 100)}`;
}

/** Ровная секунда — грубо, иначе точно: «00:15», но «00:42.50». */
export function formatTimeAuto(seconds: number): string {
  return Math.round(Math.max(0, seconds) * 100) % 100 === 0 ? formatTimeCoarse(seconds) : formatTimePrecise(seconds);
}

/** Отрезок одной точностью на оба конца: «00:40 – 00:55» или «00:40.50 – 00:55.50». */
export function formatTimeRange(from: number, to: number): string {
  const whole = (s: number) => Math.round(Math.max(0, s) * 100) % 100 === 0;
  const format = whole(from) && whole(to) ? formatTimeCoarse : formatTimePrecise;
  return `${format(from)} – ${format(to)}`;
}
