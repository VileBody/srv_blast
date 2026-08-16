import { PointerEvent as ReactPointerEvent, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { cn } from '../../lib/cn';
import { HUE_GRADIENT, hueAt } from '../../lib/color';
import { useDragScroll } from './BackgroundPanel';
import { PillsFooter } from './WizardFrame';
import { useWizardStore } from '../../stores/wizardStore';
import { SubtitlePreview } from './SubtitlePreview';
import { FigIcon } from '../ui/FigIcon';
import { InlineError, queryDown } from '../ui/ErrorState';

/*
 * Этап «Текст» (Figma W16 → 17 → 23): стили субтитров включаются/выключаются
 * кликом по карточке, футер — живое отражение выбранного. Белый свотч — дефолтный
 * цвет; выбор с полосы показывает точку, клик по свотчу возвращает белый.
 */


function StyleCard({ name, previewUrl, inPool, onClick }: { name: string; previewUrl: string; inPool: boolean; onClick: () => void }) {
  const [broken, setBroken] = useState(false);
  return (
    <button type="button" onClick={onClick} aria-pressed={inPool} className="media-card h-full" style={{ aspectRatio: '4 / 3' }}>
      {!broken && <img src={previewUrl} alt="" onError={() => setBroken(true)} />}
      {broken && <span className="absolute inset-0 bg-grad-card" aria-hidden="true" />}
      <span className="absolute inset-0 z-[1] bg-[rgba(5,1,15,0.5)] backdrop-blur-[1.5px]" aria-hidden="true" />
      <span className="absolute inset-0 z-[2] flex items-center justify-center text-[32px] text-text">{name}</span>
      {/* Выбор показывает только обводка — галочку с примеров убрали (как у фото и футажа):
          она закрывала кадр и дублировала и без того заметную рамку. */}
      {inPool && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-0 z-[3] rounded-r10" style={{ boxShadow: 'inset 0 0 0 2px var(--accent-light)' }} />
      )}
    </button>
  );
}

export function StageSubtitles() {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const setSubtitles = useWizardStore((state) => state.setSubtitles);
  const toggleStyle = useWizardStore((state) => state.toggleSubtitleStyle);
  const stylesQuery = useQuery({ queryKey: ['subtitle-styles'], queryFn: api.subtitleStyles });
  const cardsScroll = useDragScroll();
  const barRef = useRef<HTMLDivElement>(null);
  const [huePct, setHuePct] = useState(50);

  const pickFromBar = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    const rect = barRef.current.getBoundingClientRect();
    const pct = Math.min(100, Math.max(0, ((e.clientX - rect.left) / rect.width) * 100));
    setHuePct(pct);
    setSubtitles({ color: hueAt(pct) });
  };
  const onBarDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!barRef.current) return;
    try {
      barRef.current.setPointerCapture(e.pointerId);
    } catch {
      /* синтетический pointerId */
    }
    pickFromBar(e);
    barRef.current.onpointermove = ((ev: PointerEvent) => {
      if (ev.buttons) pickFromBar(ev as unknown as ReactPointerEvent<HTMLDivElement>);
    }) as never;
    barRef.current.onpointerup = () => {
      if (barRef.current) { barRef.current.onpointermove = null; barRef.current.onpointerup = null; }
    };
  };

  const isWhite = subtitles.color === '#f6f5fd';

  return (
    <div className="flex h-full flex-col">
      <h2 className="wizard-h flex items-center gap-space-3">
        <em className="bg-grad-text bg-clip-text font-bold italic text-[30px] leading-none text-transparent">Т</em>
        {t('wizard.subs.title')}
      </h2>

      {/* Белый свотч — дефолт; обводка на нём = включён белый. Клик возвращает белый. */}
      <div className="mt-[20px] flex items-center gap-[28px]">
        <button
          type="button"
          aria-label={t('wizard.subs.whiteColor')}
          className="h-[60px] w-[60px] shrink-0 rounded-r15 bg-[#f6f5fd] transition"
          style={{ boxShadow: isWhite ? '0 0 0 2px var(--accent-light)' : undefined }}
          onClick={() => setSubtitles({ color: '#f6f5fd' })}
        />
        <div
          ref={barRef}
          className="color-slider h-[60px] flex-1"
          style={{ background: HUE_GRADIENT }}
          onPointerDown={onBarDown}
          role="slider"
          aria-label={t('wizard.subs.color')}
          aria-valuenow={Math.round(huePct)}
        >
          {!isWhite && <span className="color-slider-thumb" style={{ left: `${huePct}%` }} />}
        </div>
      </div>

      <div className="relative mt-[40px] flex min-h-[382px] w-full flex-1 flex-col overflow-hidden rounded-r15 bg-grad-soft-10 pb-[40px] pt-[40px]">
        <div className="px-[40px]">
          <span className="wizard-body">{t('wizard.subs.chooseType')}</span>
        </div>
        {stylesQuery.isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <span className="spinner !h-[48px] !w-[48px] !border-[3px] !border-accent-20 !border-t-accent-light" />
          </div>
        ) : queryDown(stylesQuery) ? (
          /* список стилей не пришёл — раньше здесь оставалась пустая полоса без объяснения */
          <div className="flex flex-1 items-center justify-center">
            <InlineError error={stylesQuery.error} offline={stylesQuery.fetchStatus === 'paused'} onRetry={() => stylesQuery.refetch()} retrying={stylesQuery.isFetching} />
          </div>
        ) : (
          <div className="relative mt-[12px] min-h-[253px] flex-1">
            <span className="scroll-fade-l" />
            <span className="scroll-fade-r" />
            <div
              ref={cardsScroll.ref}
              className="media-row cursor-grab select-none items-stretch gap-[20px] px-[40px] active:cursor-grabbing"
              {...cardsScroll.handlers}
            >
              {stylesQuery.data?.styles.map((item) => (
                <StyleCard
                  key={item.id}
                  name={item.name}
                  previewUrl={item.previewUrl}
                  inPool={subtitles.pool.includes(item.name)}
                  onClick={() => { if (!cardsScroll.moved()) toggleStyle(item.name); }}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function SubtitlesWorkZone({ ready, canContinue, loading, onBack, onNext }: { ready: boolean; canContinue: boolean; loading?: boolean; onBack: () => void; onNext: () => void }) {
  const { t } = useTranslation();
  const subtitles = useWizardStore((state) => state.subtitles);
  const lyrics = useWizardStore((state) => state.lyrics);
  const fragmentLyrics = useWizardStore((state) => state.fragmentLyrics);
  const pillsScroll = useDragScroll();
  const [previewName, setPreviewName] = useState<string | undefined>(undefined);

  const currentName = previewName && subtitles.pool.includes(previewName) ? previewName : subtitles.pool[subtitles.pool.length - 1];
  const previewLyrics = (fragmentLyrics.trim() || lyrics.trim()) || t('wizard.subs.lyricsPlaceholder');

  return (
    <aside className="wizard-aside flex min-h-0 shrink-0 flex-col gap-[20px] max-lg:w-full">
      <div className="card-2 flex min-h-0 flex-1 flex-col px-space-6 py-space-6 max-lg:px-space-5">
        <h2 className="wizard-h mb-space-5 shrink-0 whitespace-nowrap">{t('wizard.workZone')}</h2>
        <div className="relative min-h-0 flex-1 overflow-hidden rounded-r15 bg-grad-soft-10">
          {currentName && <SubtitlePreview className="absolute inset-0" styleName={currentName} lyrics={previewLyrics} color={subtitles.color} />}
          <span className="dash-panel-plain pointer-events-none absolute inset-0 z-[3]" aria-hidden="true" />
          {!currentName && (
            <div className="flex h-full items-center justify-center p-space-5">
              <p className="wizard-body max-w-[223px] text-center">{t('wizard.subs.empty')}</p>
            </div>
          )}
        </div>
      </div>

      <PillsFooter
        pills={subtitles.pool.map((name) => ({
          key: name,
          label: name,
          icon: <em className="font-bold italic text-[24px] leading-none text-text-80">T</em>
        }))}
        activeKey={undefined}
        emptyLabel={t('wizard.subs.footerEmpty')}
        onPill={(key) => setPreviewName(key)}
        onPlus={undefined}
        ready={ready}
        canContinue={canContinue}
        loading={loading}
        onBack={onBack}
        onNext={onNext}
        dragScroll={pillsScroll}
      />
    </aside>
  );
}
