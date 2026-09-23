import { useEffect, useRef, useState } from 'react';
import { readGuideRecord, writeGuideRecord } from './guideMemory';
import { useGuideLiveStore } from './guideLiveState';
import { api } from '../../lib/api';

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
 * уже пройденный шаг вместо актуального). Это ТОЛЬКО про idle-реактивацию —
 * «юзер завис на незавершённой задаче» — а не про то, показывается ли сейчас
 * подсказка вообще.
 *
 * `visible` (опционально, дефолт = active) — отдельный сигнал «подсказку
 * сейчас физически показали» (её HARD-prerequisite, т.е. цель вообще
 * существует и осмысленна — например, трек загружен), БЕЗ учёта «задача уже
 * выполнена». Нужен для принудительного разового тура: подсказку показывают
 * ОДИН раз всем — и новым юзерам, и тем, у кого поле уже заполнено — если
 * они физически долистали до места, где она живёт. «Показано» фиксируется
 * по visible, а не по active: иначе у юзера с уже готовой задачей seen
 * никогда бы не записался (active всегда false), и принудительный тур не
 * «завершался» бы, а лез снова при каждом визите на этот шаг.
 *
 * ВАЖНО для цепочек (шаг N показывается только когда шаг N−1 уже закрыт):
 * `visible` здесь вычисляется ПРИ ВЫЗОВЕ хука, а хуки цепочки объявлены в
 * ОБРАТНОМ порядке (последний шаг первым — так его dismissed доступен для
 * active предыдущего). Это значит на месте вызова dismissed БОЛЕЕ РАННИХ
 * шагов ещё не существует — точное «предыдущий шаг закрыт» тут не собрать.
 * В этом случае передавайте `visible=false` и отмечайте показ отдельно,
 * ПОСЛЕ того как обычным способом (в конце функции, в прямом порядке)
 * посчитан итоговый showXGuide — через `useMarkGuideSeen(id, showXGuide)`.
 */
export function useMarkGuideSeen(id: string, shown: boolean) {
  const markedSeenRef = useRef(false);
  useEffect(() => {
    if (!shown || markedSeenRef.current) return;
    markedSeenRef.current = true;
    writeGuideRecord(id, { seen: true });
    void api.trackEvent('wizard_guide_seen', { guideId: id }).catch(() => {});
  }, [shown, id]);
}

export function useGuideDismiss(id: string, active: boolean, visible: boolean = active): [boolean, (value: boolean) => void] {
  const [initialRecord] = useState(() => readGuideRecord(id));
  const [dismissed, setDismissedState] = useState(initialRecord.seen ?? false);
  const idleUsedRef = useRef(initialRecord.idleUsed ?? false);
  useMarkGuideSeen(id, visible && !dismissed);
  // Живая трансляция для соседних компонентов, которым нужно «этот гайд уже
  // закрыт ПРЯМО СЕЙЧАС» (не персистентно и не «когда-либо видел») — см.
  // guideLiveState.ts. Дешёво: просто пишем в общий zustand-стор при смене.
  useEffect(() => {
    useGuideLiveStore.getState().setDismissed(id, dismissed);
  }, [id, dismissed]);

  const setDismissed = (value: boolean) => {
    setDismissedState(value);
    if (value) {
      writeGuideRecord(id, { seen: true });
      void api.trackEvent('wizard_guide_dismissed', { guideId: id }).catch(() => {});
    }
  };

  useEffect(() => {
    if (!active || !dismissed || idleUsedRef.current) return;

    let timer = 0;
    const arm = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        idleUsedRef.current = true;
        writeGuideRecord(id, { idleUsed: true });
        void api.trackEvent('wizard_guide_idle_reactivated', { guideId: id }).catch(() => {});
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
