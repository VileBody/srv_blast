import { ReactNode, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { useWizardStore } from '../../stores/wizardStore';
import { ActionBar, Button, GLYPH, Icon, Pill, Segmented, Surface } from '../ui/kit';
import { useWizardAttempt } from './wizardAttempt';

/*
 * Каркас визарда (UI_RULES.md, волна 3): шапка с названием и этапами, у каждого шага одна
 * строка действий — «Назад» и «Продолжить» (WizardActions). Этапы — общий Segmented:
 * текущий залит accent-strong, пройденные с галочкой, непройденные недоступны.
 * Порядок табов — порядок прохождения; stage хранит старую нумерацию стора.
 */
const BG_ICON = (
  <span aria-hidden="true" className="relative inline-block h-[0.64em] w-[0.64em] shrink-0">
    <span className="absolute bottom-0 left-0 h-[58%] w-[58%] border border-dashed border-current" />
    <span className="absolute right-0 top-0 h-[72%] w-[72%] bg-current" />
  </span>
);
const LETTER = (letter: string) => <span aria-hidden="true" className="text-[1em] font-[700] italic">{letter}</span>;

const tabs: { stage: number; label: string; icon: ReactNode }[] = [
  { stage: 1, label: 'wizard.tabs.track', icon: <Icon src="/assets/figma/icon-note.svg" ratio={0.72} heavy /> },
  { stage: 2, label: 'wizard.tabs.background', icon: BG_ICON },
  { stage: 4, label: 'wizard.tabs.text', icon: LETTER('Т') },
  { stage: 3, label: 'wizard.tabs.fx', icon: <Icon src="/assets/figma/icon-bolt.svg" ratio={0.65} heavy /> },
  { stage: 5, label: 'wizard.tabs.pool', icon: LETTER('V') }
];

export function StageTabs() {
  const { t } = useTranslation();
  const stage = useWizardStore((state) => state.stage);
  const setStage = useWizardStore((state) => state.setStage);
  const reachedIndex = useWizardStore((state) => state.reachedIndex);
  const currentIndex = tabs.findIndex((tab) => tab.stage === stage);
  /*
   * Кликается всё, где человек уже был, — и левее, и правее текущего этапа: вернувшись из
   * Пула в Фон, вперёд можно идти табом, не подтверждая заново уже настроенные шаги.
   */
  const reached = Math.max(currentIndex, reachedIndex);
  return (
    <Segmented
      semantics="tabs"
      fill
      ariaLabel={t('wizard.stagesAria')}
      value={String(stage)}
      onChange={(value) => setStage(Number(value))}
      items={tabs.map((tab, index) => ({
        value: String(tab.stage),
        label: t(tab.label),
        icon: tab.icon,
        done: index !== currentIndex && index < reached,
        disabled: index > reached
      }))}
    />
  );
}

export function WizardHeaderCard({ title, artist, onRename }: { title: string; artist?: string; onRename?: (value: string) => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <Surface level="card" className="flex shrink-0 flex-col gap-[14px] px-[22px] pb-[16px] pt-[18px] max-md:px-[16px]">
      <div>
        <div className="flex min-w-0 items-center gap-[8px]">
          {editing ? (
            <input
              ref={inputRef}
              defaultValue={title}
              autoFocus
              aria-label={t('wizard.rename')}
              className="h-ctl w-full max-w-[420px] rounded-r10 border border-accent-line bg-field px-[12px] text-ui-24 font-[400] text-text outline-none"
              onBlur={(e) => { onRename?.(e.target.value.trim()); setEditing(false); }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false); }}
            />
          ) : (
            <h1 className="min-w-0 truncate text-ui-24 font-[400] text-text">{title}</h1>
          )}
          {onRename && !editing && (
            <Button variant="ghost" size="sm" iconOnly aria-label={t('wizard.rename')} onClick={() => setEditing(true)} icon={<Icon><path d="M15.5 5.5l3 3L9 18l-4 1 1-4z" /></Icon>} />
          )}
        </div>
        <p className="text-ui-14 text-text-40">{artist ?? '—'}</p>
      </div>
      <StageTabs />
    </Surface>
  );
}

/**
 * Строка действий шага — одна на все пять шагов. «Продолжить» не бывает мёртвым: на
 * неготовом шаге нажатие подсвечивает пропуски, а здесь пишется, чего не хватает.
 */
export function WizardActions({
  ready,
  loading,
  onBack,
  onNext,
  nextLabel
}: {
  ready: boolean;
  loading?: boolean;
  /** на первом шаге «Назад» нет */
  onBack?: () => void;
  onNext: () => void;
  nextLabel?: string;
}) {
  const { t } = useTranslation();
  const stage = useWizardStore((state) => state.stage);
  // чего не хватает — пишет WizardPage при нажатии на неготовом шаге
  const missing = useWizardAttempt((state) => (state.stage === stage ? state.message : ''));
  const showMissing = !ready && Boolean(missing);
  return (
    <div className="flex shrink-0 flex-col gap-[10px]">
      {showMissing && <p role="alert" className="text-ui-14 text-warning">{missing}</p>}
      <ActionBar className="max-md:flex-row">
        {onBack && (
          <Button variant="secondary" size="lg" iconOnly aria-label={t('wizard.back')} onClick={onBack} icon={<Icon>{GLYPH.arrowLeft}</Icon>} />
        )}
        <Button
          variant="primary"
          size="lg"
          ready={ready}
          loading={loading}
          onClick={onNext}
          className="min-w-0 flex-1"
          iconEnd={<Icon>{GLYPH.arrowRight}</Icon>}
        >
          {nextLabel ?? t('wizard.continue')}
        </Button>
      </ActionBar>
    </div>
  );
}

/**
 * Нижняя карточка рабочей зоны: пилюли — живое отражение настроенных разделов (итог
 * с переходом), «+» ведёт к следующему разделу, под ними — строка действий шага.
 */
export function PillsFooter({
  pills,
  activeKey,
  emptyLabel,
  onPill,
  onPlus,
  plusDisabled,
  ready,
  loading,
  onBack,
  onNext,
  nextLabel,
  dragScroll
}: {
  /** trail — метка после подписи (варианты FX: цвет варианта) */
  pills: { key: string; label: string; icon: ReactNode; trail?: ReactNode }[];
  activeKey?: string;
  emptyLabel: string;
  onPill: (key: string) => void;
  onPlus?: () => void;
  plusDisabled?: boolean;
  ready: boolean;
  /** устарело: кликабельность больше не отключается — см. WizardActions */
  canContinue?: boolean;
  loading?: boolean;
  onBack: () => void;
  onNext: () => void;
  /** подпись кнопки «дальше» — на опциональном этапе это «Пропустить» */
  nextLabel?: string;
  dragScroll: { ref: React.RefObject<HTMLDivElement>; moved: () => boolean; handlers: Record<string, unknown> };
}) {
  const { t } = useTranslation();
  // Фейды зависят от прокрутки: левый — только когда есть контент слева, правый — справа
  const [fade, setFade] = useState({ left: false, right: false });
  const syncFades = () => {
    const el = dragScroll.ref.current;
    if (!el) return;
    setFade({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  };
  useEffect(() => {
    syncFades();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pills.length]);
  const mask = `linear-gradient(to right, transparent 0px, #000 ${fade.left ? 32 : 0}px, #000 calc(100% - ${fade.right ? 32 : 0}px), transparent 100%)`;

  return (
    <Surface level="card" className="flex shrink-0 flex-col gap-[14px] px-[18px] py-[16px]">
      <div className="flex items-center gap-[8px]">
        <div
          ref={dragScroll.ref}
          className="media-row min-w-0 flex-1 cursor-grab select-none items-center gap-[8px] active:cursor-grabbing"
          style={{ maskImage: mask, WebkitMaskImage: mask }}
          onScroll={syncFades}
          {...dragScroll.handlers}
        >
          {pills.length === 0 ? (
            <span className="inline-flex h-ctl-sm shrink-0 items-center text-ui-14 text-text-40">{emptyLabel}</span>
          ) : (
            pills.map((pill) => (
              <Pill
                key={pill.key}
                pressed={pill.key === activeKey}
                icon={<span className="grid h-[22px] w-[22px] shrink-0 place-items-center">{pill.icon}</span>}
                className="pl-[6px]"
                onClick={() => { if (!dragScroll.moved()) onPill(pill.key); }}
              >
                <span className="inline-flex items-center gap-[6px]">{pill.label}{pill.trail}</span>
              </Pill>
            ))
          )}
        </div>
        {onPlus && (
          <Button variant="secondary" size="sm" iconOnly aria-label={t('wizard.nextSection')} onClick={onPlus} disabled={plusDisabled} icon={<Icon>{GLYPH.plus}</Icon>} />
        )}
      </div>
      <WizardActions ready={ready} loading={loading} onBack={onBack} onNext={onNext} nextLabel={nextLabel} />
    </Surface>
  );
}

/** Карточка правой колонки шага (превью, текст): та же поверхность, что у шапки. */
export function AsideCard({ className, children }: { className?: string; children: ReactNode }) {
  return <Surface level="card" className={cn('flex min-h-0 flex-col gap-[12px] p-[22px] max-md:p-[16px]', className)}>{children}</Surface>;
}
