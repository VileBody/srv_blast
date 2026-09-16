import type { CSSProperties } from 'react';

export function SvgMaskIcon({ src, className, style }: { src: string; className?: string; style?: CSSProperties }) {
  // числовые width/height масштабируются на телефоне вместе с текстом (--fig-scale, index.css)
  const scaled: CSSProperties = { ...style };
  if (typeof style?.width === 'number') scaled.width = `calc(${style.width}px * var(--fig-scale, 1))`;
  if (typeof style?.height === 'number') scaled.height = `calc(${style.height}px * var(--fig-scale, 1))`;
  return (
    <span
      className={`nav-icon-mask ${className ?? ''}`}
      style={{ maskImage: `url(${src})`, WebkitMaskImage: `url(${src})`, ...scaled }}
      aria-hidden="true"
    />
  );
}
