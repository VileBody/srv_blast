import type { CSSProperties } from 'react';

export function SvgMaskIcon({ src, className, style }: { src: string; className?: string; style?: CSSProperties }) {
  return (
    <span
      className={`nav-icon-mask ${className ?? ''}`}
      style={{ maskImage: `url(${src})`, WebkitMaskImage: `url(${src})`, ...style }}
      aria-hidden="true"
    />
  );
}
