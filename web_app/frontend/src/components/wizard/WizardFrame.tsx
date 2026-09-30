import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { useWizardStore } from '../../stores/wizardStore';
import { useWizardAttempt } from './wizardAttempt';
import './wizard12.css';

/*
 * Каркас визарда — один в один по утверждённому макету «Трек / Фон» (артефакт wizard12 v3):
 * слева шапка с этапами и карточка шага, справа колонка шага и нижняя карточка с итогом
 * и строкой «Назад / Продолжить». Стили — wizard12.css (перенесены из макета как есть).
 */

/* Иконки макета — пути для viewBox 24, рисуются обводкой (svg.w12-i). */
export const W12 = {
  left: <path d="M14.5 6 8.5 12l6 6" />,
  right: <path d="M9.5 6l6 6-6 6" />,
  down: <path d="M6 9.5l6 6 6-6" />,
  arrow: <path d="M4 12h15M13.5 6.5 19 12l-5.5 5.5" />,
  back: <path d="M20 12H5M10.5 6.5 5 12l5.5 5.5" />,
  check: <path d="M5 12.5 9.5 17 19 7.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  pencil: <path d="M15.5 5.5l3 3L9 18l-4 1 1-4z" />,
  reset: <path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3M4.5 4.5v4h4" />,
  upload: <path d="M12 15V4.5M7.5 9 12 4.5 16.5 9M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15" />,
  spark: <path d="M12 4v4M12 16v4M4 12h4M16 12h4M6.5 6.5l2.5 2.5M15 15l2.5 2.5M17.5 6.5 15 9M9 15l-2.5 2.5" />,
  note: <><path d="M9 18V5.5l10-2V16" /><circle cx="6.5" cy="18" r="2.5" /><circle cx="16.5" cy="16" r="2.5" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />
};
export const PLAY = <svg viewBox="0 0 24 24" className="w12-pl"><path d="M7 4.5v15l12.5-7.5z" /></svg>;
export const PAUSE = <svg viewBox="0 0 24 24"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg>;

export function Svg({ children, className, style }: { children: ReactNode; className?: string; style?: React.CSSProperties }) {
  return <svg viewBox="0 0 24 24" className={cn('w12-i', className)} style={style} aria-hidden="true">{children}</svg>;
}

const tabs: { stage: number; label: string; icon: ReactNode }[] = [
  { stage: 1, label: 'wizard.tabs.track', icon: <span className="w12-mi w12-cap w12-heavy" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-note.svg)', '--r': 0.75 } as React.CSSProperties} /> },
  { stage: 2, label: 'wizard.tabs.background', icon: <span className="w12-sq" aria-hidden="true" /> },
  { stage: 4, label: 'wizard.tabs.text', icon: <span className="w12-t-it" aria-hidden="true">Т</span> },
  { stage: 3, label: 'wizard.tabs.fx', icon: <span className="w12-mi w12-cap w12-heavy" aria-hidden="true" style={{ '--m': 'url(/assets/wizard/ic-bolt.svg)', '--r': 0.65 } as React.CSSProperties} /> },
  { stage: 5, label: 'wizard.tabs.pool', icon: <span className="w12-t-it" aria-hidden="true">V</span> }
];

export function StageTabs() {
  const { t } = useTranslation();
  const stage = useWizardStore((state) => state.stage);
  const setStage = useWizardStore((state) => state.setStage);
  const reachedIndex = useWizardStore((state) => state.reachedIndex);
  const currentIndex = tabs.findIndex((tab) => tab.stage === stage);
  // Кликается всё, где человек уже был, — и левее, и правее текущего этапа
  const reached = Math.max(currentIndex, reachedIndex);
  return (
    <nav className="w12-tabs" role="tablist" aria-label={t('wizard.stagesAria')}>
      {tabs.map((tab, index) => {
        const current = index === currentIndex;
        const done = !current && index < reached;
        return (
          <button
            key={tab.stage}
            type="button"
            role="tab"
            className="w12-tab"
            aria-selected={current}
            disabled={index > reached}
            onClick={() => setStage(tab.stage)}
          >
            {done ? <span className="w12-done"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.6 8.4 6.9 10.6 11.4 5.6" /></svg></span> : tab.icon}
            <span className="w12-l">{t(tab.label)}</span>
          </button>
        );
      })}
    </nav>
  );
}

export function WizardHeaderCard({ title, artist, onRename }: { title: string; artist?: string; onRename?: (value: string) => void }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <header className="w12-card w12-head">
      <div className="w12-head-row">
        {editing ? (
          <input
            ref={inputRef}
            defaultValue={title}
            autoFocus
            aria-label={t('wizard.rename')}
            className="w12-rename"
            onBlur={(e) => { onRename?.(e.target.value.trim()); setEditing(false); }}
            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false); }}
          />
        ) : <h1>{title}</h1>}
        {onRename && !editing && (
          <button type="button" className="w12-icon-btn" aria-label={t('wizard.rename')} onClick={() => setEditing(true)}><Svg>{W12.pencil}</Svg></button>
        )}
      </div>
      <p className="w12-artist">{artist ?? '—'}</p>
      <StageTabs />
    </header>
  );
}

/** Строка «Назад / Продолжить». «Продолжить» не бывает мёртвым: на неготовом шаге нажатие
 *  подсвечивает пропуски, а над кнопками пишется, чего не хватает. */
/** tone='field' — «дальше» не главное действие карточки: тон «Назад» вместо акцента. */
export function WizardActions({ ready, loading, onBack, onNext, nextLabel, tone }: { ready: boolean; loading?: boolean; onBack?: () => void; onNext: () => void; nextLabel?: string; tone?: 'field' }) {
  const { t } = useTranslation();
  const stage = useWizardStore((state) => state.stage);
  const missing = useWizardAttempt((state) => (state.stage === stage ? state.message : ''));
  const next = (
    <button type="button" className={cn('w12-cta', tone === 'field' ? 'w12-cta-field' : ready && 'w12-ready')} onClick={onNext} aria-busy={loading || undefined}>
      {loading ? <span className="spinner" aria-hidden="true" /> : <><span className="w12-l">{nextLabel ?? t('wizard.continue')}</span><Svg>{W12.arrow}</Svg></>}
    </button>
  );
  return (
    <>
      {!ready && missing && <p role="alert" className="w12-miss">{missing}</p>}
      {onBack ? (
        <div className="w12-cta-row">
          <button type="button" className="w12-back" aria-label={t('wizard.back')} onClick={onBack}><Svg>{W12.back}</Svg></button>
          {next}
        </div>
      ) : next}
    </>
  );
}

/**
 * Нижняя карточка шага: итог (пилюли разделов — живое отражение настроенного, клик ведёт
 * в раздел) и строка «Назад / Продолжить».
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
  nextLabel
}: {
  /** icon — счётчик или значок раздела; trail — метка после подписи (цвет варианта FX) */
  pills: { key: string; label: string; icon: ReactNode; trail?: ReactNode; zero?: boolean }[];
  activeKey?: string;
  emptyLabel: string;
  onPill: (key: string) => void;
  onPlus?: () => void;
  plusDisabled?: boolean;
  ready: boolean;
  canContinue?: boolean;
  loading?: boolean;
  onBack: () => void;
  onNext: () => void;
  nextLabel?: string;
  /** устарело: лента больше не листается драгом, пилюли переносятся */
  dragScroll?: unknown;
}) {
  const { t } = useTranslation();
  // Итог — одной строкой: лишние пилюли уходят за край и листаются (колесо/тач/драг скролла),
  // край, за которым что-то есть, мягко затухает.
  const sumRef = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ left: false, right: false });
  const sync = () => {
    const el = sumRef.current;
    if (!el) return;
    setFade({ left: el.scrollLeft > 2, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 });
  };
  useEffect(() => {
    sync();
    const el = sumRef.current;
    if (!el) return undefined;
    const observer = new ResizeObserver(sync);
    observer.observe(el);
    // вертикальное колесо листает строку вбок
    const onWheel = (event: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
      event.preventDefault();
      el.scrollLeft += event.deltaY;
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => { observer.disconnect(); el.removeEventListener('wheel', onWheel); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pills.length]);
  return (
    <div className="w12-card w12-foot-card">
      <div ref={sumRef} className="w12-sum" data-fade-l={fade.left || undefined} data-fade-r={fade.right || undefined} onScroll={sync}>
        {pills.length === 0 && <span className="w12-sum-empty"><span className="w12-l">{emptyLabel}</span></span>}
        {pills.map((pill) => (
          <button key={pill.key} type="button" className={cn(pill.key === activeKey && 'w12-on', pill.zero && 'w12-zero')} onClick={() => onPill(pill.key)}>
            <b>{pill.icon}</b><span className="w12-l">{pill.label}</span>{pill.trail}
          </button>
        ))}
        {onPlus && (
          <button type="button" className="w12-sum-plus" aria-label={t('wizard.nextSection')} disabled={plusDisabled} onClick={onPlus}><Svg>{W12.plus}</Svg></button>
        )}
      </div>
      <WizardActions ready={ready} loading={loading} onBack={onBack} onNext={onNext} nextLabel={nextLabel} />
    </div>
  );
}

/* Модель макета (артефакт wizard12 v3): ширина композиции всегда 1240, высота плавает 700–860. */
const CANVAS_W = 1240;
const CANVAS_MIN_H = 700;

/**
 * Холст визарда — та же модель, что у макета. Ширина 1240 в единицах макета растянута на всю
 * зону (zoom), поэтому кегли и колонки растут вместе с экраном в тех же пропорциях; высота —
 * остаток зоны в тех же единицах, так что визард заполняет её целиком и ничего не режется.
 * Только на очень широком и низком экране (высота меньше 700 единиц) масштаб берётся по высоте,
 * и визард встаёт по центру. Ниже 1024 — обычная колонка без масштаба, как у всего приложения.
 */
export function WizardCanvas({ children }: { children: ReactNode }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState<{ zoom: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return undefined;
    const desktop = window.matchMedia('(min-width: 1024px)');
    const sync = () => {
      const { clientWidth: w, clientHeight: h } = box;
      if (!desktop.matches || !w || !h) { setFit(null); return; }
      const zoom = Math.min(w / CANVAS_W, h / CANVAS_MIN_H);
      setFit({ zoom, height: h / zoom });
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(box);
    desktop.addEventListener('change', sync);
    return () => {
      observer.disconnect();
      desktop.removeEventListener('change', sync);
    };
  }, []);
  return (
    <div ref={boxRef} className="w12-fit">
      <div className="w12 w12-app" style={fit ? { zoom: fit.zoom, height: fit.height } : undefined}>{children}</div>
    </div>
  );
}

/** Карточка правой колонки (текст, превью, рабочая зона). */
export function AsideCard({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('w12-card w12-aside', className)}>{children}</div>;
}
