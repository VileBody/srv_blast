import { useEffect, useRef } from 'react';
import { api } from '../../lib/api';
import { useWizardStore } from '../../stores/wizardStore';
import { timingToSeconds } from './useFragmentAudio';

const POLL_MS = 3000;

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

  const fragment = fragmentEnabled ? fragmentLyrics : '';
  const clipReady = timingMode === 'manual' && timingToSeconds(timingFrom) !== null && timingToSeconds(timingTo) !== null;
  const inputsReady = active && Boolean(track) && clipReady && Boolean((fragment || lyrics).trim());
  const inputsKey = inputsReady ? `${track?.id}|${timingFrom}|${timingTo}|${fragment || lyrics}` : '';
  const startedForRef = useRef('');

  // Смена вводных → старт (идемпотентный) новой примерки
  useEffect(() => {
    if (!inputsKey || startedForRef.current === inputsKey) return;
    startedForRef.current = inputsKey;
    let cancelled = false;
    api.asrStart({ clipFrom: timingFrom, clipTo: timingTo, fragment, lyrics, trackId: track?.id ?? '' })
      .then(({ asr }) => { if (!cancelled) setAsrResult(asr); })
      .catch(() => { if (!cancelled) startedForRef.current = ''; });
    // Размонтирование до ответа (StrictMode дважды монтирует эффект) — ответ уже
    // не применится, и следующий монтаж обязан стартовать заново, иначе слова
    // никогда не доедут до стора. Бэк идемпотентен по ключу — второй start дёшев.
    return () => { cancelled = true; startedForRef.current = ''; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputsKey]);

  // Идёт → поллим. Таймер, а не refetchInterval react-query: в браузерном пане
  // опросы react-query встают на паузу (см. HANDOFF, «грабли»).
  useEffect(() => {
    // IDLE — ещё не стартовали (или бэку нечего примерять): поллить нечего, ждём start
    if (!inputsKey || !asrKey || (status !== 'QUEUED' && status !== 'RUNNING')) return;
    let cancelled = false;
    const tick = () => {
      api.asrState(asrKey)
        .then(({ asr }) => { if (!cancelled) setAsrResult(asr); })
        .catch(() => undefined);
    };
    const id = window.setInterval(tick, POLL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputsKey, asrKey, status]);
}
