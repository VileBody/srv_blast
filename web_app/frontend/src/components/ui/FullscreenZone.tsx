import { ReactNode, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

/*
 * Фуллскрин-зона (Figma W40 — FX, W52 — постинг в TikTok): один каркас на оба экрана.
 * Frame 1041: 1191×905 = вся зона контента, rgba(5,1,15,.9) + border 1px accent + r25 + blur 50.
 * Внутри — блок 783 по центру (поля 204): левая колонка 390, зазор 20, плеер 373, высота 745 (поля 80).
 * Иконка сворачивания — 20×20 в правом верхнем углу (40,40).
 * Рендерим порталом в body: в макете зона лежит поверх всех карточек, а не внутри них.
 */
export function FullscreenZone({
  onCollapse,
  left,
  right,
  responsiveScale = false
}: {
  onCollapse: () => void;
  left: ReactNode;
  right: ReactNode;
  responsiveScale?: boolean;
}) {
  const { t } = useTranslation();
  const frameRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useLayoutEffect(() => {
    if (!responsiveScale || !frameRef.current) return;
    const frame = frameRef.current;
    const sync = () => setScale(Math.max(0.72, Math.min(frame.clientWidth / 1191, frame.clientHeight / 905)));
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(frame);
    return () => observer.disconnect();
  }, [responsiveScale]);

  const columns = (
    <>
      <div className="flex w-[390px] max-w-full shrink-0 flex-col">{left}</div>
      <div className="flex w-[373px] max-w-full shrink-0 flex-col">{right}</div>
    </>
  );

  return createPortal(
    /*
     * Рамка перекрывает ВСЮ рабочую область приложения. На 1440 это ровно
     * x=219…1408 / y=60…964 — те же границы, что у основных карточек.
     * Фигмовский блок 783px остаётся центрированным: контролы не растягиваются
     * и не меняют размер на 1920/2560/2780, растёт только защитная зона вокруг них.
     */
    <div ref={frameRef} className={`pointer-events-auto fixed inset-y-[var(--rail-pad-y)] left-[calc(var(--sidebar-w)_+_var(--space-6))] right-space-6 z-[55] rounded-r25 border border-accent-light bg-[rgba(5,1,15,0.9)] backdrop-blur-[50px] max-lg:inset-0 max-lg:rounded-none ${responsiveScale ? 'flex items-center justify-center overflow-hidden max-lg:block max-lg:overflow-y-auto' : 'grid place-items-center overflow-auto subtle-scroll'}`}>
      {/* подсказка «Свернуть» — тот же стиль, что «Развернуть»: подложка иконки = высоте подсказки (37px) */}
      <button
        type="button"
        onClick={onCollapse}
        aria-label={t('common.collapse')}
        className="group/collapse absolute right-[40px] top-[40px] z-[2] flex h-[37px] w-[37px] items-center justify-center rounded-r10 transition-colors hover:bg-accent-20"
      >
        <span className="pointer-events-none absolute right-[45px] top-1/2 z-[5] flex h-[37px] -translate-y-1/2 items-center whitespace-nowrap rounded-r10 bg-[#2b2145] px-[14px] text-[14px] text-text opacity-0 shadow-soft transition-opacity group-hover/collapse:opacity-100">{t('common.collapse')}</span>
        <img src="/assets/figma/fx-expand.svg" width="20" height="20" alt="" />
      </button>

      {/* 80px сверху/снизу дают точную композицию 905px; на низком окне зона скроллится, а не обрезает колонки. */}
      {responsiveScale ? (
        <>
          <div className="relative shrink-0 max-lg:hidden" style={{ width: 783 * scale, height: 745 * scale }}>
            <div className="absolute left-0 top-0 flex h-[745px] w-[783px] gap-[20px]" style={{ transform: `scale(${scale})`, transformOrigin: 'top left' }}>
              {columns}
            </div>
          </div>
          <div className="no-scrollbar hidden min-h-full w-full flex-col items-center gap-[20px] overflow-y-auto px-[8px] pb-[40px] pt-[88px] max-lg:flex">
            {columns}
          </div>
        </>
      ) : (
        // grid place-items-center центрирует по обеим осям; my-[80px] — минимальный зазор,
        // чтобы на низком окне зона скроллилась, а не обрезала колонки (Figma: поля 80).
        <div className="my-[80px] flex h-[745px] w-[783px] max-w-[calc(100%_-_40px)] gap-[20px] max-lg:w-full max-lg:px-space-5">
          {columns}
        </div>
      )}
    </div>,
    document.body
  );
}
