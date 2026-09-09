const fs = require('fs');
function replace(file, before, after) {
  let source = fs.readFileSync(file, 'utf8');
  if (!source.includes(before)) throw new Error(`Missing fragment in ${file}`);
  fs.writeFileSync(file, source.replace(before, after));
}
replace('frontend/src/components/project/BatchCards.tsx', "'flex shrink-0 items-center gap-[10px] whitespace-nowrap text-[24px] font-[350] leading-none transition',", "'flex h-[38px] shrink-0 items-center gap-[8px] whitespace-nowrap rounded-r10 border border-accent bg-grad-soft-20 px-[14px] text-[16px] font-[350] leading-none transition',");
replace('frontend/src/components/project/BatchCards.tsx', '<FigIcon name="pd-tiktok.svg" h={20} />', '<FigIcon name="pd-tiktok.svg" h={14} />');
replace('frontend/src/index.css', '.sidebar-icon-active { opacity: 1; filter: drop-shadow(0 0 12px var(--accent-light)); }', '.sidebar-icon-active { opacity: 1; filter: none; }');
replace('frontend/src/index.css', '.sidebar-icon-active .nav-icon-mask { filter: brightness(1.25); }', '.sidebar-icon-active .nav-icon-mask { filter: brightness(1.65); }');
