import { type RefObject, useCallback, useEffect, useRef, useState } from 'react';
import { readGuideRecord, subscribeGuideRecord, writeGuideRecord } from './guideMemory';
import { useGuideLiveStore } from './guideLiveState';

/**
 * Цепочка подсказок «подсказка → действие → подсказка → действие».
 *
 * Раньше следующий шаг ждал только закрытия предыдущего, и «Дальше, Дальше, Дальше»
 * пролистывало весь тур до того, как человек успевал что-то сделать. Этот хук — латч
 * «действие, о котором говорит подсказка `id`, сделано»: следующий шаг гейтится им
 * (вместе с закрытием предыдущего), а не одним закрытием.
 *
 * Действие засчитывается, только когда до шага дошла очередь (`armed`) — то, что
 * человек сделал ДО подсказки, не в счёт: иначе следующая подсказка всплыла бы сразу
 * следом, без паузы на действие. Способы засчитать (любой):
 *  - `done` — условие стало истинным (выбрал/настроил то, о чём шла речь);
 *  - `targetRef` — клик (тап) внутри цели подсказки: нажал контрол, на который она
 *    указывала. Именно click, а не pointerdown — прокрутка пальцем не считается;
 *  - вызов возвращённой `markActed()` — для действий вне цели (кнопка в соседней панели).
 *
 * Латч навсегда и per-аккаунт (guideMemory, рядом с seen) — перезагрузка посреди тура
 * не заставляет повторять уже сделанное — и транслируется соседним компонентам
 * (useGuideActed), потому что шаги одной цепочки живут в разных панелях.
 *
 * `onAct` — один раз, в момент срабатывания латча: обычно закрыть свою подсказку
 * («сделал — подсказка ушла, всплыла следующая»).
 */
export function useGuideAction(
  id: string,
  armed: boolean,
  options: { done?: boolean; targetRef?: RefObject<HTMLElement>; onAct?: () => void } = {}
): [boolean, () => void] {
  const { done = false, targetRef, onAct } = options;
  const [acted, setActed] = useState(() => readGuideRecord(id).acted ?? false);
  const actedRef = useRef(acted);
  const armedRef = useRef(armed);
  armedRef.current = armed;
  const onActRef = useRef(onAct);
  onActRef.current = onAct;

  const markActed = useCallback(() => {
    if (actedRef.current || !armedRef.current) return;
    actedRef.current = true;
    setActed(true);
    writeGuideRecord(id, { acted: true });
    onActRef.current?.();
  }, [id]);

  useEffect(() => {
    useGuideLiveStore.getState().setActed(id, acted);
  }, [id, acted]);

  useEffect(() => {
    if (armed && done) markActed();
  }, [armed, done, markActed]);

  useEffect(() => {
    if (!targetRef || !armed || acted) return undefined;
    // Слушаем документ, а не саму цель: ref цели может переехать на другой узел
    // (раскрыли другой тип хука), а подписка на старый узел молча бы умерла.
    const onClick = (event: Event) => {
      if (event.target instanceof Node && targetRef.current?.contains(event.target)) markActed();
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [targetRef, armed, acted, markActed]);

  return [acted, markActed];
}

/** «Действие подсказки `id` сделано» — для шага, который живёт в соседнем компоненте. */
export function useGuideActed(id: string): boolean {
  const live = useGuideLiveStore((state) => state.acted[id] ?? false);
  const [stored, setStored] = useState(() => readGuideRecord(id).acted ?? false);
  useEffect(() => {
    setStored(readGuideRecord(id).acted ?? false);
    return subscribeGuideRecord(() => setStored(readGuideRecord(id).acted ?? false));
  }, [id]);
  return live || stored;
}
