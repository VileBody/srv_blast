import { cn } from '../../lib/cn';

function isPlaceholder(url?: string | null): boolean {
  return !url || url.endsWith('/cover-placeholder.svg');
}

/** Реальная обложка, либо спокойный брендовый стейт без имитации видеокадра. */
export function ProjectCover({ name, src, className }: { name: string; src?: string | null; className?: string }) {
  if (!isPlaceholder(src)) {
    return <img src={src ?? undefined} alt="" className={cn('object-cover', className)} />;
  }

  const initial = name.trim().charAt(0).toLocaleUpperCase() || 'Б';
  return (
    <span
      className={cn('relative flex overflow-hidden bg-[#120b25]', className)}
      style={{ backgroundImage: 'radial-gradient(circle at 78% 20%, rgba(139,111,230,.30), transparent 34%), radial-gradient(circle at 18% 88%, rgba(94,63,170,.24), transparent 40%)' }}
      aria-hidden="true"
    >
      <span className="absolute inset-[10%] rounded-[18px] border border-[rgba(164,134,255,.18)]" />
      <span className="absolute left-[14%] top-[14%] h-[7px] w-[7px] rotate-45 bg-accent-light shadow-[0_0_18px_rgba(164,134,255,.85)]" />
      <span className="m-auto bg-gradient-to-br from-white to-[#a486ff] bg-clip-text text-[clamp(30px,6vw,72px)] font-[350] leading-none text-transparent opacity-90">{initial}</span>
    </span>
  );
}
