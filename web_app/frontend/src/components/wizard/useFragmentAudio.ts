import { useEffect, useRef, useState } from 'react';
import { useWizardStore } from '../../stores/wizardStore';

/** «01:02:44» → секунды (мм:сс:мс, мс — сотые). */
export function timingToSeconds(value: string): number | null {
  const parsed = /^(\d{2}):(\d{2}):(\d{2})$/.exec(value);
  if (!parsed) return null;
  return Number(parsed[1]) * 60 + Number(parsed[2]) + Number(parsed[3]) / 100;
}

/**
 * Проигрывание ВЫБРАННОГО ОТРЫВКА загруженного трека — поверх любого превью визарда.
 *
 * До этого послушать трек можно было только на первом шаге, и превью футажа оставалось
 * абстрактным: человек выбирал фон, не понимая, как он ляжет на его музыку. Берём файл по
 * `localUrl` (он лежит на бэке), а не blob-ссылку первого шага: blob живёт в одном экране
 * и умирает после перезагрузки страницы.
 */
export function useFragmentAudio() {
  const track = useWizardStore((state) => state.track);
  const timingFrom = useWizardStore((state) => state.timingFrom);
  const timingTo = useWizardStore((state) => state.timingTo);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const url = track?.localUrl ?? null;

  // Смена трека и уход со страницы обязаны глушить звук: иначе музыка играет «из ниоткуда»
  useEffect(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPlaying(false);
  }, [url]);
  useEffect(() => () => {
    audioRef.current?.pause();
    audioRef.current = null;
  }, []);

  const toggle = () => {
    if (!url) return;
    if (!audioRef.current) {
      audioRef.current = new Audio(url);
      audioRef.current.onended = () => setPlaying(false);
    }
    const audio = audioRef.current;
    if (playing) {
      audio.pause();
      setPlaying(false);
      return;
    }
    const from = timingToSeconds(timingFrom);
    const to = timingToSeconds(timingTo);
    // Играем ровно отрывок, который уедет в ролик, а не трек целиком
    audio.ontimeupdate = to !== null && (from === null || to > from)
      ? () => {
          if (audio.currentTime >= to) {
            audio.pause();
            setPlaying(false);
            audio.ontimeupdate = null;
          }
        }
      : null;
    audio.currentTime = from ?? 0;
    void audio.play();
    setPlaying(true);
  };

  return { available: Boolean(url), playing, toggle };
}
