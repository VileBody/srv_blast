import { ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';
import { useWizardStore } from '../../stores/wizardStore';

/*
 * Каркас визарда по макетам Figma (Wireframe 7–17):
 * шапка с названием трека и табами этапов + правая панель «Текст».
 * Порядок табов — как в макете; stage хранит старую нумерацию стора.
 * Иконка активного таба — акцентная, неактивного — белая (W7: «Фон» белый; W12: «Фон» #8b6fe6).
 */
const tabs: { stage: number; label: string; icon: (color: string) => ReactNode }[] = [
  { stage: 1, label: 'wizard.tabs.track', icon: (color) => <SvgMaskIcon src="/assets/figma/icon-note.svg" style={{ width: 12, height: 17, color }} /> },
  {
    stage: 2,
    label: 'wizard.tabs.background',
    icon: (color) => (
      <span aria-hidden="true" className="relative inline-block h-[19px] w-[19px] shrink-0">
        <span className="absolute bottom-0 left-0 h-[11px] w-[11px] border border-dashed" style={{ borderColor: color }} />
        <span className="absolute right-0 top-0 h-[15px] w-[15px]" style={{ backgroundColor: color }} />
      </span>
    )
  },
  { stage: 4, label: 'wizard.tabs.text', icon: (color) => <em className="font-bold italic text-[25px] leading-none" style={{ color }}>Т</em> },
  { stage: 3, label: 'wizard.tabs.fx', icon: (color) => <SvgMaskIcon src="/assets/figma/icon-bolt.svg" style={{ width: 13, height: 20, color }} /> },
  { stage: 5, label: 'wizard.tabs.pool', icon: (color) => <em className="font-bold italic text-[25px] leading-none" style={{ color }}>V</em> }
];

export function StageTabs() {
  const { t } = useTranslation();
  const stage = useWizardStore((state) => state.stage);
  const setStage = useWizardStore((state) => state.setStage);
  // Порядок прохождения = порядок табов (Фон → Текст → Хук), «пройденность» — по индексу
  const currentIndex = tabs.findIndex((tab) => tab.stage === stage);
  // Прогресс-подложка: заполняется от старта бара до правого края активного пила
  const fillPct = ((currentIndex + 1) / tabs.length) * 100;
  return (
    <div className="relative flex h-[60px] w-full overflow-hidden rounded-r15 bg-grad-soft-10" role="tablist" aria-label={t('wizard.stagesAria')}>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 left-0 z-0 rounded-r15 transition-[width] duration-300 ease-out"
        style={{ width: `${fillPct}%`, background: 'linear-gradient(90deg, rgba(139,111,230,0.28) 0%, rgba(95,66,185,0.22) 100%)' }}
      />
      {tabs.map((tab, index) => {
        const current = tab.stage === stage;
        const passed = index < currentIndex;
        return (
          <button
            key={tab.stage}
            type="button"
            role="tab"
            aria-selected={current}
            disabled={!passed && !current}
            onClick={() => passed && setStage(tab.stage)}
            className={cn(
              // min-w-0 обязателен: иначе длинный лейбл (EN «Background») раздувает свой таб
              // и ломает равные пилюли из макета (5×124)
              'relative z-[1] flex h-full min-w-0 flex-1 items-center justify-center gap-space-2 rounded-r15 text-[24px] font-[350] text-text-80 transition disabled:cursor-default max-xl:gap-space-1 max-xl:text-[17px]',
              current && 'border-2 border-accent-light bg-grad-soft-20 text-text',
              passed && 'cursor-pointer hover:text-text'
            )}
          >
            {tab.icon(current ? 'var(--accent-light)' : 'var(--text-80)')}
            <span className="truncate">{t(tab.label)}</span>
          </button>
        );
      })}
    </div>
  );
}

export function WizardHeaderCard({ title, artist, onRename }: { title: string; artist?: string; onRename?: (value: string) => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <header className="card-2 flex h-[240px] shrink-0 flex-col px-space-7 py-space-6 max-lg:h-auto max-lg:px-space-5">
      <div className="flex items-center gap-space-3">
        {editing ? (
          <input
            ref={inputRef}
            defaultValue={title}
            autoFocus
            className="wizard-h w-full max-w-[420px] rounded-r10 border border-border bg-transparent px-space-2 outline-none focus:border-accent-light"
            onBlur={(e) => { onRename?.(e.target.value.trim()); setEditing(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false); }}
          />
        ) : (
          <h1 className="wizard-h truncate">{title}</h1>
        )}
        {onRename && !editing && (
          <button
            type="button"
            aria-label={t('wizard.rename')}
            className="flex h-[32px] w-[32px] shrink-0 items-center justify-center rounded-r10 bg-transparent transition-colors hover:bg-accent-20"
            onClick={() => setEditing(true)}
          >
            <img src="/assets/figma/icon-pencil.svg" width="15" height="17" alt="" />
          </button>
        )}
      </div>
      <p className="wizard-body mt-space-2">{artist ?? '—'}</p>
      <div className="mt-auto pt-space-4">
        <StageTabs />
      </div>
    </header>
  );
}

/**
 * Нижняя карточка рабочей зоны (Figma W22/W23/W18, 472×226):
 * пилюли — живое отражение настроенных разделов, «+» переводит к следующему разделу,
 * «Продолжить» подсвечивается только при непустом наборе.
 */
export function PillsFooter({
  pills,
  activeKey,
  emptyLabel,
  onPill,
  onPlus,
  plusDisabled,
  ready,
  canContinue,
  loading,
  onBack,
  onNext,
  nextLabel,
  dragScroll
}: {
  pills: { key: string; label: string; icon: ReactNode }[];
  activeKey?: string;
  emptyLabel: string;
  onPill: (key: string) => void;
  onPlus?: () => void;
  plusDisabled?: boolean;
  ready: boolean;
  canContinue: boolean;
  loading?: boolean;
  onBack: () => void;
  onNext: () => void;
  /** подпись кнопки «дальше» — на опциональном этапе это «Пропустить» */
  nextLabel?: string;
  dragScroll: { ref: React.RefObject<HTMLDivElement>; moved: () => boolean; handlers: Record<string, unknown> };
}) {
  const { t } = useTranslation();
  const hasPlus = Boolean(onPlus);
  // Фейды зависят от прокрутки: левый появляется только когда есть контент слева,
  // правый — только когда есть контент справа (иначе съедали пилюли в покое)
  const [fade, setFade] = useState({ left: false, right: false });
  const syncFades = () => {
    const el = dragScroll.ref.current;
    if (!el) return;
    setFade({
      left: el.scrollLeft > 4,
      right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4
    });
  };
  useEffect(() => {
    syncFades();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pills.length]);
  // Маска вместо цветного оверлея: пилюли тают в прозрачность у краёв (в тон любого фона),
  // справа фейд заканчивается перед «+», под кнопкой лента полностью прозрачна.
  const rightGap = hasPlus ? 80 : 0;
  const mask = `linear-gradient(to right, transparent 0px, #000 ${fade.left ? 40 : 0}px, #000 calc(100% - ${rightGap + (fade.right ? 40 : 0)}px), transparent calc(100% - ${rightGap}px))`;

  return (
    <div className="card-2 flex h-[226px] shrink-0 flex-col justify-between px-space-6 py-space-6 max-lg:px-space-5">
      <div className="relative">
        <div
          ref={dragScroll.ref}
          className="media-row cursor-grab select-none items-center gap-[20px] active:cursor-grabbing"
          style={{ height: 60, paddingRight: hasPlus ? 80 : 0, maskImage: mask, WebkitMaskImage: mask }}
          onScroll={syncFades}
          {...dragScroll.handlers}
        >
          {pills.length === 0 ? (
            <span className="soft-btn pointer-events-none h-[60px] w-[310px] shrink-0 !text-text-60">{emptyLabel}</span>
          ) : (
            pills.map((pill) => (
              <button
                key={pill.key}
                type="button"
                className={cn('pool-pill', pill.key === activeKey && 'shadow-[inset_0_0_0_2px_var(--accent-light)]')}
                onClick={() => { if (!dragScroll.moved()) onPill(pill.key); }}
              >
                <span className="pool-pill-count">{pill.icon}</span>
                {pill.label}
              </button>
            ))
          )}
        </div>
        {hasPlus && (
          <button
            type="button"
            aria-label={t('wizard.nextSection')}
            onClick={onPlus}
            disabled={plusDisabled}
            className="absolute right-0 top-0 z-[2] flex h-[60px] w-[60px] items-center justify-center rounded-r15 bg-text transition hover:opacity-90 active:scale-95 disabled:opacity-40"
          >
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <defs>
                <linearGradient id="footerPlus" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#8b6fe6" />
                  <stop offset="1" stopColor="#5f42b9" />
                </linearGradient>
              </defs>
              <path d="M12 4v16M4 12h16" stroke="url(#footerPlus)" strokeWidth="2.4" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      <div className="flex items-center gap-[20px]">
        <BackSquareButton onClick={onBack} />
        <button type="button" disabled={!canContinue || loading} onClick={onNext} className={cn('soft-btn h-[60px] flex-1 gap-space-4', ready && 'soft-btn-ready')}>
          {loading ? <span className="spinner" /> : (<>
            {nextLabel ?? t('wizard.continue')}
            <svg viewBox="0 0 26 16" width="25" height="15" fill="none" aria-hidden="true">
              <path d="M1 8h22.5M17 1.5 24.5 8 17 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </>)}
        </button>
      </div>
    </div>
  );
}

export function BackSquareButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
  const { t } = useTranslation();
  return (
    <button type="button" aria-label={t('wizard.back')} disabled={disabled} onClick={onClick} className="soft-btn h-[60px] w-[60px] shrink-0">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
        <path d="M14.5 5.5 8 12l6.5 6.5M8 12h11.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}
