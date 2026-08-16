const fs = require('fs');

function edit(file, changes) {
  let source = fs.readFileSync(file, 'utf8');
  for (const [before, after] of changes) {
    if (!source.includes(before)) throw new Error(`Missing fragment in ${file}: ${before.slice(0, 100)}`);
    source = source.replace(before, after);
  }
  fs.writeFileSync(file, source);
}

edit('frontend/src/pages/PricingPage.tsx', [
  ['  headlineAsset: string;', '  numberAsset: string;\n  particlesAsset: string;\n  particlesClass: string;'],
  ['<img src={`/assets/figma/${plan.headlineAsset}`} alt="" aria-hidden className="pointer-events-none absolute left-0 top-0 h-[240px] w-full max-w-none select-none object-fill" />', '<img src={`/assets/figma/${plan.particlesAsset}`} alt="" aria-hidden className={cn(\'pointer-events-none absolute left-1/2 max-w-none -translate-x-1/2 select-none\', plan.particlesClass)} />\n      <img src={`/assets/figma/${plan.numberAsset}`} alt="" aria-hidden className="pointer-events-none absolute left-0 top-0 h-auto w-full max-w-none select-none object-contain" />'],
  ['<span className="hidden absolute right-[28px] top-[100px] h-[60px] w-[150px] items-center justify-center gap-[12px] rounded-r15 border border-accent-light backdrop-blur-[50px]" style={{ backgroundImage: \'linear-gradient(175deg, rgba(21,15,37,0.4) 8.4%, rgba(17,13,29,0.4) 97.9%)\' }}>', '<span className="absolute right-[28px] top-[100px] flex h-[60px] w-[150px] items-center justify-center gap-[12px] rounded-r15 border border-accent-light backdrop-blur-[50px]" style={{ backgroundImage: \'linear-gradient(175deg, rgba(21,15,37,0.78) 8.4%, rgba(17,13,29,0.78) 97.9%)\' }}>'],
  ["type: 'BLAST', headlineAsset: 'pr-head-blast.png', badge:", "type: 'BLAST', numberAsset: 'pr-number-blast.png', particlesAsset: 'pr-particles-blast.svg', particlesClass: 'top-[-75px] h-[487px] w-[875px]', badge:"],
  ["type: 'GLOW', headlineAsset: 'pr-head-glow.png', badge:", "type: 'GLOW', numberAsset: 'pr-number-glow.png', particlesAsset: 'pr-particles-glow.svg', particlesClass: 'top-[-66px] h-[512px] w-[660px]', badge:"],
  ["type: 'IMPULSE', headlineAsset: 'pr-head-impulse.png', badge:", "type: 'IMPULSE', numberAsset: 'pr-number-impulse.png', particlesAsset: 'pr-particles-impulse.svg', particlesClass: 'top-[-237px] h-[1071px] w-[1100px]', badge:"]
]);

edit('frontend/src/pages/ProfilePage.tsx', [
  ['className="no-scrollbar flex min-h-0 flex-1 flex-col gap-[20px] md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:overflow-y-auto md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]"', 'className="flex min-h-0 flex-1 flex-col gap-[20px] overflow-hidden md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]"'],
  ['className="inline-grid min-w-[1ch] max-w-[calc(100%-36px)]"', 'className="inline-grid min-w-[1ch] max-w-[calc(100%-36px)] rounded-r10 bg-accent-10 px-[8px] shadow-[inset_0_0_0_1px_var(--accent-light)]"'],
  ['className="card-2 h-[366px] shrink-0 overflow-hidden p-[40px]"', 'className="card-2 min-h-[366px] flex-1 overflow-hidden p-[40px]"']
]);

edit('frontend/src/pages/StatsPage.tsx', [
  ['className="subtle-scroll flex min-h-0 flex-1 flex-col overflow-y-auto md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]"', 'className="flex min-h-0 flex-1 flex-col overflow-hidden md:h-[calc(100dvh_-_2*var(--space-6))] md:flex-none md:py-[calc(var(--rail-pad-y)_-_var(--space-6))]"'],
  ['className="flex min-h-[904px] shrink-0 flex-col gap-[20px]"', 'className="flex min-h-0 flex-1 flex-col gap-[20px]"']
]);

edit('frontend/src/components/wizard/BackgroundPanel.tsx', [
  ["  }, []);\n\n  return {\n    ref,\n    moved: () => drag.current.moved,", "  });\n\n  return {\n    ref,\n    moved: () => drag.current.moved,"],
  ['  const pills = backgroundPills(background);', "  const pills = backgroundPills(background);\n  const footerPills = pills.length > 0\n    ? pills\n    : [{ mode: background.mode, label: modes.find((item) => item.value === background.mode)?.label ?? 'wizard.bg.modeFootage', count: 0 }];"],
  ['pills={pills.map((pill) => ({', 'pills={footerPills.map((pill) => ({'],
  ['label: chip(pill.label),', "label: pill.label.startsWith('wizard.') ? t(pill.label) : chip(pill.label),"]
]);

edit('frontend/src/components/wizard/HookPanel.tsx', [
  ['{fade.left && <span className="pointer-events-none absolute inset-y-0 left-0 z-[1] w-[37px] bg-[linear-gradient(90deg,rgba(18,13,37,.98),rgba(18,13,37,0))]" />}', '{fade.left && <span className="pointer-events-none absolute inset-y-0 left-0 z-[1] w-[24px] bg-[linear-gradient(90deg,#1e1635_0%,rgba(30,22,53,0)_100%)]" />}'],
  ['{fade.right && <span className="pointer-events-none absolute inset-y-0 right-0 z-[1] w-[54px] bg-[linear-gradient(270deg,rgba(18,13,37,.98),rgba(18,13,37,0))]" />}', '{fade.right && <span className="pointer-events-none absolute inset-y-0 right-0 z-[1] w-[24px] bg-[linear-gradient(270deg,#1e1635_0%,rgba(30,22,53,0)_100%)]" />}'],
  ['{pillsFade.left && <span className="pointer-events-none absolute inset-y-0 left-0 z-[1] w-[37px] bg-[linear-gradient(90deg,rgba(18,13,37,.98),rgba(18,13,37,0))]" />}', '{pillsFade.left && <span className="pointer-events-none absolute inset-y-0 left-0 z-[1] w-[24px] bg-[linear-gradient(90deg,#140e24_0%,rgba(20,14,36,0)_100%)]" />}'],
  ['{pillsFade.right && <span className="pointer-events-none absolute inset-y-0 right-0 z-[1] w-[54px] bg-[linear-gradient(270deg,rgba(18,13,37,.98),rgba(18,13,37,0))]" />}', '{pillsFade.right && <span className="pointer-events-none absolute inset-y-0 right-0 z-[1] w-[24px] bg-[linear-gradient(270deg,#140e24_0%,rgba(20,14,36,0)_100%)]" />}']
]);

edit('frontend/src/components/wizard/SlicePanel.tsx', [
  ['(units.length > 0 || subtitles.length > 0 || hooks.length > 0)', '(bgRest !== 0 || subsRest !== 0 || hooksRest !== 0)'],
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
