const fs = require('fs');
function edit(file, changes) {
  let source = fs.readFileSync(file, 'utf8');
  for (const [before, after] of changes) {
    if (!source.includes(before)) throw new Error(`Missing fragment in ${file}: ${before.slice(0, 120)}`);
    source = source.replace(before, after);
  }
  fs.writeFileSync(file, source);
}

edit('frontend/src/components/wizard/SlicePanel.tsx', [
  ['(units.length > 0 || subtitleStyles.length > 0 || hooksInPool.length > 0)', '(bgRest !== 0 || subsRest !== 0 || hooksRest !== 0)'],
  ['className="translate-y-px whitespace-nowrap text-[14px] text-text-60 underline decoration-dotted underline-offset-4 transition hover:text-text"', 'className="flex h-[34px] items-center whitespace-nowrap rounded-r10 border border-accent bg-grad-soft-20 px-[14px] text-[14px] leading-none text-text-80 transition hover:text-text hover:brightness-125"']
]);

edit('frontend/src/components/project/BatchCards.tsx', [
  ["'group flex items-center gap-[12px] whitespace-nowrap text-[24px] font-[350] leading-[29px] transition',", "'group flex h-[38px] items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent bg-grad-soft-20 px-[14px] text-[16px] font-[350] leading-none transition',"],
  ['<FigIcon name="batch-tiktok.svg" h={20} className="transition-transform group-hover:scale-105" />', '<FigIcon name="batch-tiktok.svg" h={14} className="transition-transform group-hover:scale-105" />']
]);

edit('frontend/src/index.css', [
  ['.sidebar-icon-active { opacity: 1; filter: drop-shadow(0 0 12px var(--accent-light)); }', '.sidebar-icon-active { opacity: 1; filter: none; }'],
  ['.sidebar-icon-active .nav-icon-mask { filter: brightness(1.25); }', '.sidebar-icon-active .nav-icon-mask { filter: brightness(1.65); }']
]);
