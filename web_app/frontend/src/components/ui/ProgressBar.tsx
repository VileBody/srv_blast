export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const safe = Math.max(0, Math.min(100, value));
  return (
    <div className="w-full">
      <div className="h-[10px] overflow-hidden rounded-r40 bg-[rgba(246,245,253,.08)]" aria-label={label ?? `Прогресс ${safe}%`}>
        <div className="h-full rounded-r40 bg-grad-btn transition-[width] duration-300" style={{ width: `${safe}%` }} />
      </div>
    </div>
  );
}
