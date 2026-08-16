import { useMemo } from 'react';
import { Player } from '@remotion/player';
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { BratSubtitles } from '../remotion/BratSubtitles';

export interface PreviewWord {
  word: string;
  start: number;
  end: number;
}

interface SubtitleCompositionProps {
  words: PreviewWord[];
  styleName: string;
  color: string;
  effect?: string;
}

const SAMPLE = 'Твой звук уже невозможно забыть';

/** Равномерные дефолтные тайминги для интерактива: без LLM и сетевых вызовов. */
export function defaultWordTimings(text: string): PreviewWord[] {
  const source = text.trim() || SAMPLE;
  return source.replace(/\s+/g, ' ').split(' ').filter(Boolean).slice(0, 18).map((word, index) => ({
    word,
    start: index * 0.48,
    end: (index + 1) * 0.48
  }));
}

function EffectBackdrop({ effect }: { effect?: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const sweep = interpolate(frame, [0, durationInFrames], [-45, 135]);
  const normalized = (effect ?? '').toLowerCase();
  const flash = normalized.includes('молни') || normalized.includes('light') || normalized.includes('затвор') || normalized.includes('shutter');
  const glitch = normalized.includes('глитч') || normalized.includes('glitch') || normalized.includes('ксерокс');
  return (
    <AbsoluteFill style={{ overflow: 'hidden', background: 'linear-gradient(180deg,#21163b 0%,#080313 100%)' }}>
      <AbsoluteFill style={{ opacity: 0.72, background: 'radial-gradient(circle at 52% 38%,rgba(139,111,230,.8),transparent 34%),linear-gradient(145deg,#080313 0%,#2a1d4d 52%,#0c0619 100%)' }} />
      <div style={{ position: 'absolute', inset: '-25%', transform: `rotate(${sweep}deg)`, opacity: flash ? 0.62 : 0.22, background: 'linear-gradient(90deg,transparent 43%,rgba(246,245,253,.9) 49%,rgba(139,111,230,.8) 51%,transparent 57%)' }} />
      {glitch && [18, 31, 57, 74].map((top, index) => (
        <div key={top} style={{ position: 'absolute', left: index % 2 ? '-5%' : '8%', top: `${top}%`, width: '102%', height: 12 + index * 5, transform: `translateX(${Math.sin((frame + index * 7) / 4) * 54}px)`, background: index % 2 ? 'rgba(139,111,230,.46)' : 'rgba(246,245,253,.32)', mixBlendMode: 'screen' }} />
      ))}
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(180deg,rgba(5,1,15,.04),rgba(5,1,15,.46))' }} />
    </AbsoluteFill>
  );
}

function SubtitleComposition({ words, styleName, color, effect }: SubtitleCompositionProps) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const time = frame / fps;
  const found = words.findIndex((word) => time >= word.start && time < word.end);
  const activeIndex = found < 0 ? 0 : found;
  const visible = words.slice(Math.max(0, activeIndex - 2), activeIndex + 3);
  const focusIndex = Math.min(2, visible.length - 1);
  const style = styleName.toLowerCase();
  const active = words[activeIndex] ?? words[0];

  return (
    <AbsoluteFill style={{ fontFamily: 'Point, Arial, sans-serif', color }}>
      <EffectBackdrop effect={effect} />
      {style === 'brat' && (
        <BratSubtitles words={words} config={{ fillColor: color, blurRadius: 0, minimaxCssScale: 0 }} />
      )}
      {style === 'jakson' && (
        <div style={{ position: 'absolute', left: '8%', right: '8%', top: '39%', textAlign: 'center', fontSize: 106, fontWeight: 700, lineHeight: 1.05, textTransform: 'uppercase' }}>
          {visible.map((word, index) => <span key={`${word.start}-${word.word}`} style={{ display: 'inline-block', margin: '0 14px', color: index === focusIndex ? '#ff4055' : color, transform: index === focusIndex ? 'scale(1.35) skew(-6deg)' : undefined }}>{word.word}</span>)}
        </div>
      )}
      {style === 'impulse' && (
        <div style={{ position: 'absolute', left: '9%', right: '9%', top: '42%', textAlign: 'center', fontSize: 94, fontWeight: 700, lineHeight: 1.12 }}>
          {visible.map((word, index) => <span key={`${word.start}-${word.word}`} style={{ display: 'inline-block', margin: '0 10px', fontSize: index === focusIndex ? 180 : 94, color: index === focusIndex ? '#8b6fe6' : color }}>{word.word}</span>)}
        </div>
      )}
      {style === 'tape' && (
        <div style={{ position: 'absolute', left: '5%', right: '5%', top: '42%', textAlign: 'center', transform: `rotate(${Math.sin(frame / 18) * 1.5}deg)` }}>
          <span style={{ display: 'inline-block', padding: '28px 44px', background: '#f6f5fd', boxShadow: '0 18px 42px rgba(5,1,15,.45)', color: '#05010f', fontSize: 112, fontWeight: 700, lineHeight: 1, textTransform: 'uppercase' }}>{visible.map((word) => word.word).join(' ')}</span>
        </div>
      )}
      {style === 'trendy' && (
        <div style={{ position: 'absolute', left: '6%', right: '6%', top: '38%', textAlign: 'center', fontSize: 118, fontWeight: 800, lineHeight: .96, letterSpacing: -4, textTransform: 'uppercase', textShadow: '0 10px 0 rgba(5,1,15,.45)' }}>
          {visible.map((word, index) => <span key={`${word.start}-${word.word}`} style={{ display: 'block', color: index === focusIndex ? '#8b6fe6' : color }}>{word.word}</span>)}
        </div>
      )}
      {!['brat', 'jakson', 'impulse', 'tape', 'trendy'].includes(style) && (
        <div style={{ position: 'absolute', left: '8%', right: '8%', top: '44%', textAlign: 'center', fontSize: 104, fontWeight: 700 }}>{active?.word}</div>
      )}
    </AbsoluteFill>
  );
}

export function SubtitlePreview({ styleName, lyrics, color = '#f6f5fd', effect, className }: { styleName: string; lyrics?: string; color?: string; effect?: string; className?: string }) {
  const words = useMemo(() => defaultWordTimings(lyrics ?? ''), [lyrics]);
  const lastWord = words[words.length - 1];
  const durationInFrames = Math.max(120, Math.ceil((lastWord?.end ?? 5) * 23.976));
  return (
    <div
      className={`isolate overflow-hidden ${className ?? 'relative h-full w-full'}`}
      style={{ contain: 'layout paint', transform: 'translateZ(0)', backfaceVisibility: 'hidden' }}
      aria-label={`Preview: ${styleName}`}
    >
      <Player
        component={SubtitleComposition}
        inputProps={{ words, styleName, color, effect }}
        durationInFrames={durationInFrames}
        compositionWidth={1080}
        compositionHeight={1920}
        fps={23.976}
        autoPlay
        loop
        controls={false}
        style={{ display: 'block', position: 'relative', width: '100%', height: '100%', overflow: 'hidden', background: '#080313', transform: 'translateZ(0)' }}
      />
    </div>
  );
}
