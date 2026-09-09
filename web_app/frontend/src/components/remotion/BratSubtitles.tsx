import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';

export type WordTiming = {
  word: string;
  start: number;
  end: number;
  focus?: boolean;
  voice?: boolean;
};

type BratConfig = {
  fontFamily: string;
  fontFallback: string;
  fontSize: number;
  minFontSize: number;
  fitMargin: number;
  leading: number;
  tracking: number;
  fillColor: string;
  wordsPerLine: number;
  maxLines: number;
  boxWFactor: number;
  boxHFactor: number;
  scale: number;
  yNudge: number;
  blurRadius: number;
  blurCssScale: number;
  minimaxRadius: number;
  minimaxCssScale: number;
  effectFps: number;
  revealLeadFrames: number;
  tailFrames: number;
  fontStretch: string;
};

type WordBlock = {
  words: WordTiming[];
  lines: WordTiming[][];
  voice: boolean;
};

const DEFAULT_CONFIG: BratConfig = {
  fontFamily: 'Arial Narrow',
  fontFallback: 'Arial, Helvetica, sans-serif',
  fontSize: 130,
  minFontSize: 56,
  fitMargin: 0.97,
  leading: 130,
  tracking: -20,
  fillColor: '#ffffff',
  wordsPerLine: 2,
  maxLines: 4,
  boxWFactor: 0.8,
  boxHFactor: 0.5,
  scale: 0.8,
  yNudge: 0,
  blurRadius: 10,
  blurCssScale: 1,
  minimaxRadius: 15,
  minimaxCssScale: 0,
  effectFps: 30,
  revealLeadFrames: 0,
  tailFrames: 0,
  fontStretch: 'condensed',
};

const normalizeWord = (word: string) => String(word ?? '').toLowerCase();

const packRun = (
  run: WordTiming[],
  wordsPerLine: number,
  maxLines: number,
  isVoice: boolean
): WordBlock[] => {
  const perBlock = wordsPerLine * maxLines;
  const blocks: WordBlock[] = [];

  for (let i = 0; i < run.length; i += perBlock) {
    const slice = run.slice(i, Math.min(i + perBlock, run.length));
    const lines: WordTiming[][] = [];

    for (let j = 0; j < slice.length; j += wordsPerLine) {
      lines.push(slice.slice(j, Math.min(j + wordsPerLine, slice.length)));
    }

    if (lines.length > 1 && lines[lines.length - 1].length < wordsPerLine) {
      const tail = lines.pop();
      const prev = lines[lines.length - 1];
      if (tail && prev) {
        prev.push(...tail);
      }
    }

    blocks.push({words: slice, lines, voice: isVoice});
  }

  return blocks;
};

const packBlocks = (words: WordTiming[], config: BratConfig): WordBlock[] => {
  const blocks: WordBlock[] = [];
  let run: WordTiming[] = [];
  let runVoice: boolean | null = null;

  for (const word of words) {
    const voice = Boolean(word.voice);
    if (runVoice === null) {
      runVoice = voice;
    }

    if (voice !== runVoice) {
      blocks.push(...packRun(run, config.wordsPerLine, config.maxLines, runVoice));
      run = [];
      runVoice = voice;
    }

    run.push(word);
  }

  if (run.length > 0) {
    blocks.push(...packRun(run, config.wordsPerLine, config.maxLines, Boolean(runVoice)));
  }

  return blocks;
};

const estimateLineWidth = (line: WordTiming[], fontSize: number, tracking: number) => {
  const text = line.map((w) => normalizeWord(w.word)).join(' ');
  const narrowGlyphFactor = 0.47;
  const spaceFactor = 0.28;
  let width = 0;

  for (const ch of text) {
    width += ch === ' ' ? fontSize * spaceFactor : fontSize * narrowGlyphFactor;
  }

  const cssTracking = tracking / 1000 * fontSize;
  return width + Math.max(0, text.length - 1) * cssTracking;
};

const computeFitFontSize = (blocks: WordBlock[], boxWidth: number, config: BratConfig) => {
  const widest = Math.max(
    1,
    ...blocks.flatMap((block) =>
      block.lines.map((line) => estimateLineWidth(line, config.fontSize, config.tracking))
    )
  );
  const available = boxWidth * config.fitMargin;
  if (widest <= available) {
    return config.fontSize;
  }
  return Math.max(config.minFontSize, config.fontSize * (available / widest));
};

const blockText = (block: WordBlock) =>
  block.lines.map((line) => line.map((w) => normalizeWord(w.word)).join(' ')).join('\n');

const isBlockVisible = (block: WordBlock, t: number, fps: number, config: BratConfig) => {
  const start = block.words[0]?.start ?? 0;
  const end = (block.words[block.words.length - 1]?.end ?? start + 1 / fps) + config.tailFrames / fps;
  return t >= start && t < end;
};

const wordVisible = (word: WordTiming, t: number, fps: number, config: BratConfig) =>
  t >= word.start + config.revealLeadFrames / fps;

const minimaxApprox = (block: WordBlock, t: number, fps: number, config: BratConfig) => {
  const start = block.words[0]?.start ?? 0;
  const progress = Math.min(1, Math.max(0, (t - start) * config.effectFps));
  const radius = config.minimaxRadius * (1 - progress);
  const cssRadius = radius * config.minimaxCssScale;
  return {radius, cssRadius};
};

const minimaxTextShadow = (radius: number, color: string) => {
  if (radius <= 0.01) {
    return undefined;
  }

  const diagonal = radius * 0.7071;
  const offsets = [
    [radius, 0],
    [-radius, 0],
    [0, radius],
    [0, -radius],
    [diagonal, diagonal],
    [-diagonal, diagonal],
    [diagonal, -diagonal],
    [-diagonal, -diagonal],
  ];

  return offsets.map(([x, y]) => `${x.toFixed(2)}px ${y.toFixed(2)}px 0 ${color}`).join(', ');
};

type BratSubtitlesProps = {
  words: WordTiming[];
  config?: Partial<BratConfig>;
};

export const BratSubtitles = ({words, config: partialConfig}: BratSubtitlesProps) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const config = {...DEFAULT_CONFIG, ...partialConfig};
  const boxWidth = Math.round(width * config.boxWFactor);
  const boxHeight = Math.round(height * config.boxHFactor);
  const blocks = React.useMemo(() => packBlocks(words, config), [words, config]);
  const fontSize = computeFitFontSize(blocks, boxWidth, config);
  const letterSpacing = (config.tracking / 1000) * fontSize;
  const lineHeight = fontSize * (config.leading / config.fontSize);
  const blurPx = config.blurRadius * config.blurCssScale;

  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      {blocks.map((block, blockIndex) => {
        if (!isBlockVisible(block, t, fps, config)) {
          return null;
        }

        const oneWord = block.words.length < 2;
        const mm = minimaxApprox(block, t, fps, config);
        const lineWords = block.lines.flat();
        const visibleWordSet = new Set(
          lineWords.filter((word) => wordVisible(word, t, fps, config)).map((word) => word)
        );
        const phrase = blockText(block);

        return (
          <div
            key={`${blockIndex}-${phrase}`}
            style={{
              position: 'absolute',
              left: '50%',
              top: `calc(50% + ${config.yNudge}px)`,
              width: boxWidth,
              height: boxHeight,
              transform: `translate(-50%, -50%) scale(${config.scale})`,
              transformOrigin: 'center center',
              fontFamily: `"${config.fontFamily}", ${config.fontFallback}`,
              fontStretch: config.fontStretch,
              fontSize,
              lineHeight: `${lineHeight}px`,
              letterSpacing,
              color: config.fillColor,
              textShadow: minimaxTextShadow(mm.cssRadius, config.fillColor),
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              textAlign: oneWord ? 'left' : 'justify',
              textAlignLast: oneWord ? 'left' : 'justify',
              textTransform: 'lowercase',
              whiteSpace: 'pre-line',
              filter: blurPx > 0 ? `blur(${blurPx}px)` : undefined,
              mixBlendMode: 'normal',
              WebkitFontSmoothing: 'antialiased',
              textRendering: 'geometricPrecision',
            }}
          >
            {block.lines.map((line, lineIndex) => (
              <div
                key={lineIndex}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: oneWord ? 'left' : 'justify',
                  textAlignLast: oneWord ? 'left' : 'justify',
                }}
              >
                {line.map((word, wordIndex) => (
                  <React.Fragment key={`${word.word}-${word.start}-${wordIndex}`}>
                    <span
                      style={{
                        opacity: visibleWordSet.has(word) ? 1 : 0,
                      }}
                    >
                      {normalizeWord(word.word)}
                    </span>
                    {wordIndex < line.length - 1 ? ' ' : null}
                  </React.Fragment>
                ))}
              </div>
            ))}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
