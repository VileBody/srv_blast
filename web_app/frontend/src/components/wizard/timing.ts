import { timingToSeconds } from './useFragmentAudio';

/*
 * Время отрывка. В сторе и на бэке — «мм:сс:сс» (сотые, `render_job.mmss_seconds`), а человек
 * видит и пишет время как на плеере: «0:40» или «0:40.5». Раньше в полях был тот же «мм:сс:мс»,
 * и «00:00:40» читалось как 40 секунд, хотя значило 0,4 с.
 */

/** Максимальная длина отрывка: 15 с на триале, 30 с на платном тарифе. */
export const SEGMENT_SECONDS = { trial: 15, paid: 30 } as const;

export function segmentSeconds(from: string, to: string): number | null {
  const a = timingToSeconds(from);
  const b = timingToSeconds(to);
  return a === null || b === null ? null : b - a;
}

/** Секунды → формат стора «мм:сс:сс». */
export function toStoreTiming(seconds: number): string {
  const centi = Math.round(Math.max(0, seconds) * 100);
  const mm = String(Math.floor(centi / 6000)).padStart(2, '0');
  const ss = String(Math.floor(centi / 100) % 60).padStart(2, '0');
  const cc = String(centi % 100).padStart(2, '0');
  return `${mm}:${ss}:${cc}`;
}

/** Секунды → «0:40» / «0:40.5» (десятые — только если они есть). */
export function formatClock(seconds: number): string {
  const tenths = Math.round(Math.max(0, seconds) * 10);
  const m = Math.floor(tenths / 600);
  const s = Math.floor(tenths / 10) % 60;
  const frac = tenths % 10;
  return `${m}:${String(s).padStart(2, '0')}${frac ? `.${frac}` : ''}`;
}

/** Длина «15,0 с» — с запятой, как в остальных текстах. */
export function formatSeconds(seconds: number): string {
  return (Math.round(seconds * 10) / 10).toFixed(1).replace('.', ',');
}

/** «0:40», «0:40.5», «40», «1:05», «0:40,5» → секунды; всё остальное — null. */
export function parseClock(value: string): number | null {
  const match = /^(?:(\d{1,2}):)?(\d{1,2}(?:\.\d+)?)$/.exec(value.trim().replace(',', '.'));
  if (!match) return null;
  const seconds = (match[1] ? Number(match[1]) * 60 : 0) + Number(match[2]);
  return Number.isFinite(seconds) ? seconds : null;
}

/** Шаг окна и полей — десятая секунды. */
export const snapTenth = (seconds: number) => Math.round(seconds * 10) / 10;
