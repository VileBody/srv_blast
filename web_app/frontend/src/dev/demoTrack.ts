/*
 * Демо-трек для визуального стенда (?qaStage=…, только dev): синтезируется в браузере, чтобы
 * у волны отрывка была настоящая форма и отрывок можно было послушать без файла в репозитории.
 * Структура как у типичного трека: тихое интро → нарастание → дроп → куплет → брейк →
 * нарастание → второй дроп → куплет → затухание. 128 BPM, моно, 22 050 Гц, WAV.
 */
const RATE = 22050;
const BPM = 128;

type Part = { until: number; kick: number; bass: number; hats: number; pad: number; riser?: boolean };
const PARTS: Part[] = [
  { until: 16, kick: 0, bass: 0, hats: 0, pad: 0.12 },
  { until: 32, kick: 0.35, bass: 0, hats: 0.08, pad: 0.12, riser: true },
  { until: 64, kick: 1, bass: 0.55, hats: 0.2, pad: 0.1 },
  { until: 96, kick: 0.55, bass: 0.3, hats: 0.14, pad: 0.1 },
  { until: 112, kick: 0, bass: 0, hats: 0, pad: 0.16 },
  { until: 120, kick: 0.45, bass: 0, hats: 0.1, pad: 0.12, riser: true },
  { until: 152, kick: 1, bass: 0.6, hats: 0.22, pad: 0.1 },
  { until: 184, kick: 0.5, bass: 0.3, hats: 0.14, pad: 0.1 },
  { until: 204, kick: 0.3, bass: 0, hats: 0.06, pad: 0.14 }
];

function wav(samples: Float32Array): Blob {
  const data = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i += 1) data.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, 'RIFF'); data.setUint32(4, 36 + samples.length * 2, true); text(8, 'WAVE');
  text(12, 'fmt '); data.setUint32(16, 16, true); data.setUint16(20, 1, true); data.setUint16(22, 1, true);
  data.setUint32(24, RATE, true); data.setUint32(28, RATE * 2, true); data.setUint16(32, 2, true); data.setUint16(34, 16, true);
  text(36, 'data'); data.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => data.setInt16(44 + index * 2, Math.max(-1, Math.min(1, sample)) * 0x7fff, true));
  return new Blob([data], { type: 'audio/wav' });
}

let cached: string | null = null;

/** blob:-ссылка на демо-трек (одна на вкладку). */
export function demoTrackUrl(seconds = 204): string {
  if (cached) return cached;
  const out = new Float32Array(Math.round(seconds * RATE));
  const beat = 60 / BPM;
  let seed = 7;
  const noise = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x3fffffff - 1; };
  let from = 0;
  for (const part of PARTS) {
    const to = Math.min(seconds, part.until);
    for (let n = Math.round(from * RATE); n < Math.round(to * RATE); n += 1) {
      const t = n / RATE;
      const inBeat = t % beat;
      const progress = (t - from) / Math.max(1, to - from);
      let sample = 0;
      // пэд: аккорд из трёх синусов с медленным дыханием
      sample += part.pad * (Math.sin(2 * Math.PI * 220 * t) + Math.sin(2 * Math.PI * 277.2 * t) + Math.sin(2 * Math.PI * 329.6 * t)) / 3 * (0.7 + 0.3 * Math.sin(2 * Math.PI * 0.25 * t));
      // бочка: свип 110→45 Гц с быстрым затуханием, на каждую долю
      if (part.kick) sample += part.kick * Math.sin(2 * Math.PI * (45 + 65 * Math.exp(-inBeat * 30)) * inBeat) * Math.exp(-inBeat * 9);
      // бас: на офбит, приглушается бочкой (сайдчейн)
      if (part.bass) sample += part.bass * Math.sign(Math.sin(2 * Math.PI * 55 * t)) * 0.5 * Math.min(1, inBeat / (beat * 0.45));
      // хэты: короткий шум на каждую восьмую
      if (part.hats) { const inEighth = t % (beat / 2); sample += part.hats * noise() * Math.exp(-inEighth * 60); }
      // райзер: шум, растущий к дропу
      if (part.riser) sample += 0.35 * progress * progress * noise();
      out[n] = sample * 0.6;
    }
    from = to;
  }
  // затухание в конце
  const fade = Math.round(8 * RATE);
  for (let i = 0; i < fade; i += 1) out[out.length - 1 - i] *= i / fade;
  cached = URL.createObjectURL(wav(out));
  return cached;
}
