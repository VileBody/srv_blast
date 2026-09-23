import { useEffect, useRef, useState } from 'react';
import { readGuideRecord, writeGuideRecord } from './guideMemory';

const IDLE_MS = 45_000;
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'scroll', 'touchstart', 'wheel'] as const;

/**
 * Дроп-ин замена useState(false) для dismissed-флага подсказки — плюс память
 * в localStorage (per-браузер), общая для всех проектов этого юзера.
 *
 * Первый показ — уже штатное поведение вызывающей стороны (open строится из
 * !dismissed && <условие незавершённости>). Но теперь dismissed СТАРТУЕТ не с
 * false, а с «уже видел эту подсказку когда-либо» — значит у нового юзера (в
 * пустом localStorage) она включится сама, а у юзера, который её уже видел
 * в прошлом проекте, — нет, даже если он начинает новый проект.
 *
 * «Видел» фиксируется в тот момент, когда подсказка реально стала видна
 * (open === true), а не только по клику на кнопку — юзер мог просто сделать
 * действие и закрыть её самим прогрессом, не читая текст до конца.
 *
 * Второе требование — idle-реактивация: если подсказку закрыли (вручную или
 * потому что она уже была отмечена «видел» из прошлого раза), а её условие
 * актуальности (`active`) всё ещё истинно (юзер так и не сделал действие), то
 * после 45с БЕЗ активности на странице подсказка включается обратно РОВНО
 * ОДИН РАЗ — и это тоже навсегда, а не один раз за визит на этап (стейджи
 * визарда размонтируются при переключении вкладок, поэтому обычный useRef
 * сбросился бы при каждом возврате).
 *
 * `active` — это условие «эта конкретная подсказка сейчас актуальна» БЕЗ
 * учёта её же dismissed (его даёт вызывающая сторона: обычно тот же chain-
 * expression, что и для open, но с вычтенным «!moiDismissed» и ОБЯЗАТЕЛЬНО
 * с «и мы ещё не ушли дальше по цепочке» — иначе после простоя может вернуться
 * уже пройденный шаг вместо актуального).
 */
export function useGuideDismiss(id: string, active: boolean): [boolean, (value: boolean) => void] {
  const [initialRecord] = useState(() => readGuideRecord(id));
  const [dismissed, setDismissedState] = useState(initialRecord.seen ?? false);
  const idleUsedRef = useRef(initialRecord.idleUsed ?? false);
  const markedSeenRef = useRef(false);

  useEffect(() => {
    if (!active || dismissed || markedSeenRef.current) return;
    markedSeenRef.current = true;
    writeGuideRecord(id, { seen: true });
  }, [active, dismissed, id]);

  const setDismissed = (value: boolean) => {
    setDismissedState(value);
    if (value) writeGuideRecord(id, { seen: true });
  };

  useEffect(() => {
    if (!active || !dismissed || idleUsedRef.current) return;

    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        idleUsedRef.current = true;
        writeGuideRecord(id, { idleUsed: true });
        setDismissedState(false);
      }, IDLE_MS);
    };

    arm();
    ACTIVITY_EVENTS.forEach((event) => window.addEventListener(event, arm, { passive: true }));
    return () => {
      window.clearTimeout(timer);
      ACTIVITY_EVENTS.forEach((event) => window.removeEventListener(event, arm));
    };
  }, [active, dismissed, id]);

  return [dismissed, setDismissed];
}
