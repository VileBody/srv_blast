/*
 * Учёт живых шагов истории стола. Ключ — то, что шаг меняет: ролик (`v<индекс>`), склейки
 * (`tl`) или дроп (`drop`). У ключа — стопка номеров шагов, которые его меняли и ещё действуют.
 *
 * Зачем: история у каждого ролика своя, а общие правки (дроп, темп, «Во все N») лежат в
 * истории ролика, где их сделали, но меняют и другие ролики. Шаг хранит снимок «до»; откат
 * снимка ролика 2 из ролика 1 стёр бы всё, что в ролике 2 правили после этого шага. Поэтому
 * шаг откатывает ключ, только если он на этом ключе верхний (позже ключ никто не менял), а
 * повторяется на ключе, только если с отмены ключ никто не трогал. Остальные ключи шаг
 * пропускает — стол говорит об этом в тосте.
 */
export type HistKey = string;
export interface HistLedger { seq: number; live: Record<HistKey, number[]> }

export const createLedger = (): HistLedger => ({ seq: 0, live: {} });

const topOf = (ledger: HistLedger, key: HistKey) => {
  const stack = ledger.live[key];
  return stack && stack.length ? stack[stack.length - 1] : 0;
};

/** Новый шаг на ключах: его номер и прежние верхи ключей (по ним проверяется повтор). */
export function ledgerPush(ledger: HistLedger, keys: HistKey[]): { seq: number; prev: Record<HistKey, number> } {
  const seq = ++ledger.seq;
  const prev: Record<HistKey, number> = {};
  for (const key of keys) {
    prev[key] = topOf(ledger, key);
    (ledger.live[key] ??= []).push(seq);
  }
  return { seq, prev };
}

/** Отмена шага: какие ключи откатить (шаг с них снимается) и какие пропустить. */
export function ledgerUndo(ledger: HistLedger, seq: number, keys: HistKey[]): { ok: HistKey[]; skipped: HistKey[] } {
  const ok = keys.filter((key) => topOf(ledger, key) === seq);
  for (const key of ok) ledger.live[key].pop();
  return { ok, skipped: keys.filter((key) => !ok.includes(key)) };
}

/** Повтор шага: какие ключи вернуть (шаг снова ложится на них) и какие пропустить. */
export function ledgerRedo(ledger: HistLedger, seq: number, prev: Record<HistKey, number>, keys: HistKey[]): { ok: HistKey[]; skipped: HistKey[] } {
  const ok = keys.filter((key) => topOf(ledger, key) === (prev[key] ?? 0));
  for (const key of ok) (ledger.live[key] ??= []).push(seq);
  return { ok, skipped: keys.filter((key) => !ok.includes(key)) };
}
