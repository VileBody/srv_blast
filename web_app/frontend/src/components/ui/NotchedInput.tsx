import { InputHTMLAttributes, useId } from 'react';
import { cn } from '../../lib/cn';

/*
 * Инпут с «вырезом» под лейбл (Figma W38, 712:1040): бокс 528×80 r25, обводка 2px
 * rgba(246,245,253,.5); лейбл 24 сидит НА верхней грани и разрывает обводку.
 *
 * Разрыв — настоящий: рамку рисует fieldset, а невидимый legend с тем же текстом вырезает
 * в ней щель (нативный notched outline). Раньше щель изображал фон лейбла цветом страницы,
 * и в модалке («Как тебя зовут?») под «Имя»/«Фамилия» проступали тёмные полосы.
 */
export function NotchedInput({
  label,
  error,
  className,
  ...props
}: { label: string; error?: string | false } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div>
      {/* рамка (fieldset inset-0) обнимает только поле — сообщение об ошибке живёт ниже, снаружи */}
      <div className="relative">
      <input
        id={id}
        {...props}
        className={cn(
          'peer h-[80px] w-full rounded-r25 border-0 bg-transparent px-[30px] text-[24px] font-[400] leading-normal text-text outline-none',
          // рамка и фокус — у fieldset ниже; глобальную обводку :focus-visible гасим, иначе двойная рамка
          'placeholder:text-text-40 focus-visible:outline-none',
          className
        )}
      />
      <fieldset
        aria-hidden="true"
        className={cn(
          'pointer-events-none absolute inset-0 m-0 min-w-0 rounded-r25 border-2 p-0 transition-colors',
          error ? 'border-[var(--warning)]' : 'border-[rgba(246,245,253,0.5)] peer-focus:border-accent-light'
        )}
      >
        {/* щель в обводке шириной с подпись: сам текст не виден, его показывает label */}
        {/* ui-allow: legend повторяет кегль подписи 1:1, иначе щель не совпадёт с текстом */}
        <legend className="invisible ml-[58px] h-0 whitespace-nowrap px-[16px] text-[24px] font-[400] leading-[0]">{label}</legend>
      </fieldset>
      <label htmlFor={id} className="pointer-events-none absolute left-[74px] top-0 -translate-y-1/2 px-[16px] leading-[29px]">
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
      </div>
      {error && <p className="mt-[6px] pl-[30px] text-[14px] text-[var(--warning)]">{error}</p>}
    </div>
  );
}
