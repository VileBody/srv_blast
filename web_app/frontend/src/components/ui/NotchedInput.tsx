import { InputHTMLAttributes, useId } from 'react';
import { cn } from '../../lib/cn';

/*
 * Инпут с «вырезом» под лейбл (Figma W38, 712:1040): бокс 528×80 r25, обводка 2px
 * rgba(246,245,253,.5); лейбл 24 сидит НА верхней грани, а под ним — плашка цвета фона,
 * которая разрывает обводку (в макете это отдельный прямоугольник 80×3 цвета #05010f).
 * Здесь вырез делает сам лейбл своим фоном — фон страницы тот же #05010f.
 */
export function NotchedInput({
  label,
  error,
  className,
  ...props
}: { label: string; error?: string | false } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="relative">
      <input
        id={id}
        {...props}
        className={cn(
          'h-[80px] w-full rounded-r25 border-2 border-[rgba(246,245,253,0.5)] bg-transparent px-[30px] text-[24px] font-[400] leading-normal text-text outline-none transition',
          // focus-visible:outline-none гасит глобальную обводку :focus-visible, иначе она
          // дублирует собственную border-accent-light инпута (двойная рамка).
          'placeholder:text-text-40 focus:border-accent-light focus-visible:outline-none',
          error && 'border-[var(--warning)]',
          className
        )}
      />
      {/* фон лейбла = фон страницы: он и есть «вырез» в обводке (в макете — плашка 80×3) */}
      <label htmlFor={id} className="pointer-events-none absolute left-[74px] top-0 -translate-y-1/2 bg-bg px-[16px] leading-[29px]">
        <span
          className="text-[24px] font-[400] text-transparent"
          style={{
            backgroundImage: 'linear-gradient(190deg, rgba(246,245,253,0.8) 8.5%, rgba(246,245,253,0.64) 94.6%)',
            WebkitBackgroundClip: 'text',
            backgroundClip: 'text'
          }}
        >
          {label}
        </span>
      </label>
      {error && <p className="mt-[6px] pl-[30px] text-[14px] text-[var(--warning)]">{error}</p>}
    </div>
  );
}
