import { useCallback, useEffect, useRef } from 'react';
import { create } from 'zustand';
import { api } from '../../lib/api';
import { useWizardStore } from '../../stores/wizardStore';
import { timingToSeconds } from './useFragmentAudio';

/** Бэкофф поллинга: распознавание идёт десятки секунд, долбить бэк каждые 3 с незачем */
const POLL_STEPS_MS = [3000, 5000, 10000];

/**
 * Общие часы запуска примерки. Хук живёт на странице визарда, а кнопка «Повторить
 * распознавание» — в таймлайне шага «Текст»: связываем их через этот стор, а не пропсами.
 */
interface AsrRunState {
  /** счётчик повторов: его смена перезапускает start даже при тех же вводных */
  nonce: number;
  /** start ушёл и ещё не ответил */
  starting: boolean;
  /** start упал сетью/бэком — без этого флага сбой был бы немым (статус так и IDLE) */
  startFailed: boolean;
  /** вводные готовы: без них повторять нечего */
  inputsReady: boolean;
}
export const useAsrRun = create<AsrRunState>(() => ({ nonce: 0, starting: false, startFailed: false, inputsReady: false }));

/** Последний повтор, который уже ушёл на бэк с force: перемонтирование хука не должно
 * пересчитывать готовую примерку заново — это десятки секунд ASR на проде. */
let forcedNonce = 0;

/** Перезапустить примерку с теми же вводными (после сбоя или по просьбе человека). */
export function retryAsrPreview() {
  useAsrRun.setState((s) => ({ nonce: s.nonce + 1, startFailed: false }));
}

/**
 * Примерка субтитров: запустить ASR отрывка, как только вводные трека готовы и
 * человек ушёл с первого шага, и дотянуть слова к шагу «Текст».
 *
 * Живёт на уровне страницы визарда (а не в панели «Текст»): ASR на проде — десятки
 * секунд (Demucs + CTC), а между «Трек» и «Текст» стоит целый шаг «Фон». Пока
 * человек выбирает футажи, слова уже считаются; к «Тексту» таймлайн готов.
 *
 * Ключ примерки считает бэк (трек+окно+текст). Здесь только: вводные поменялись →
 * снова `start` (бэк вернёт ту же примерку, если ключ тот же), не готово → поллим.
 */
export function useAsrPreview(active: boolean) {
  const track = useWizardStore((state) => state.track);
  const timingMode = useWizardStore((state) => state.timingMode);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const fragmentEnabled = useWizardStore((state) => state.fragmentEnabled);
  const fragmentLyrics = useWizardStore((state) => state.fragmentLyrics);
  const lyrics = useWizardStore((state) => state.lyrics);
  const status = useWizardStore((state) => state.asr.status);
  const asrKey = useWizardStore((state) => state.asr.key);
  const setAsrResult = useWizardStore((state) => state.setAsrResult);
  const nonce = useAsrRun((state) => state.nonce);

  const fragment = fragmentEnabled ? fragmentLyrics : '';
  const clipReady = timingMode === 'manual' && timingToSeconds(timingFrom) !== null && timingToSeconds(timingTo) !== null;
  const inputsReady = active && Boolean(track) && clipReady && Boolean((fragment || lyrics).trim());
  const inputsKey = inputsReady ? `${track?.id}|${timingFrom}|${timingTo}|${fragment || lyrics}` : '';
  const startedForRef = useRef('');
  /** шаг бэкоффа поллинга — на ключ примерки; новый старт начинает с 3 с */
  const attemptRef = useRef({ key: '', n: 0 });

  useEffect(() => { useAsrRun.setState({ inputsReady: Boolean(inputsKey) }); }, [inputsKey]);

  // Смена вводных или «повторить» → старт (идемпотентный) примерки. Повтор с теми же
  // вводными отличается только nonce — без него guard ниже его бы проглотил.
  useEffect(() => {
    const runKey = `${inputsKey}#${nonce}`;
    if (!inputsKey || startedForRef.current === runKey) return;
    startedForRef.current = runKey;
    attemptRef.current = { key: '', n: 0 };
    let cancelled = false;
    useAsrRun.setState({ starting: true, startFailed: false });
    // «Повторить» после успешной примерки: без force бэк вернул бы ту же готовую раскладку
    const force = nonce !== forcedNonce;
    forcedNonce = nonce;
    api.asrStart({ clipFrom: timingFrom, clipTo: timingTo, fragment, lyrics, trackId: track?.id ?? '', force })
      .then(({ asr }) => { if (!cancelled) { setAsrResult(asr); useAsrRun.setState({ starting: false }); } })
      .catch(() => { if (!cancelled) { startedForRef.current = ''; useAsrRun.setState({ starting: false, startFailed: true }); } });
    // Размонтирование до ответа (StrictMode дважды монтирует эффект) — ответ уже
    // не применится, и следующий монтаж обязан стартовать заново, иначе слова
    // никогда не доедут до стора. Бэк идемпотентен по ключу — второй start дёшев.
    return () => { cancelled = true; startedForRef.current = ''; useAsrRun.setState({ starting: false }); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputsKey, nonce]);

  // Идёт → поллим с бэкоффом 3 → 5 → 10 с; скрытая вкладка — пауза, вернулись — сразу
  // опрос. Таймер, а не refetchInterval react-query: в браузерном пане опросы react-query
  // встают на паузу (см. HANDOFF, «грабли»). Шаг бэкоффа держим на ключ примерки, чтобы
  // переход QUEUED → RUNNING не откатывал его к 3 с.
  useEffect(() => {
    // IDLE — ещё не стартовали (или бэку нечего примерять): поллить нечего, ждём start;
    // COMPLETED / FAILED — конец, опрос останавливается вместе с эффектом
    if (!inputsKey || !asrKey || (status !== 'QUEUED' && status !== 'RUNNING')) return;
    if (attemptRef.current.key !== asrKey) attemptRef.current = { key: asrKey, n: 0 };
    let cancelled = false;
    let timer = 0;
    const schedule = () => {
      window.clearTimeout(timer);
      if (cancelled || document.hidden) return;
      const delay = POLL_STEPS_MS[Math.min(attemptRef.current.n, POLL_STEPS_MS.length - 1)];
      timer = window.setTimeout(tick, delay);
    };
    const tick = () => {
      attemptRef.current.n += 1;
      api.asrState(asrKey)
        .then(({ asr }) => { if (!cancelled) setAsrResult(asr); })
        .catch(() => undefined)
        .finally(schedule);
    };
    const onVisibility = () => {
      if (document.hidden) { window.clearTimeout(timer); return; }
      window.clearTimeout(timer);
      tick();
    };
    schedule();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { cancelled = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', onVisibility); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputsKey, asrKey, status]);

  const retry = useCallback(() => retryAsrPreview(), []);
  return { retry };
}
