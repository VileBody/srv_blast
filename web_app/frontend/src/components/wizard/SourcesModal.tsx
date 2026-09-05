import { DragEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/cn';
import { SvgMaskIcon } from '../layout/SvgMaskIcon';

/*
 * «Загрузи свои исходники» — Figma W39 (QR, загрузка с телефона) / W49 (дроп-зона, загрузка с ПК).
 * Два состояния одной модалки, переключаются ссылкой под инпутом.
 * Геометрия W39/W49: затемнение rgba(5,1,15,.6) r25 поверх зоны карточек; панель 400×576 r15
 * grad-soft-20 + backdrop-blur; колонка контента 320 (40 / 29 / 28 / слот 320 / 40 / 60 / 19 / 19 / 21).
 */

type SourcesTab = 'qr' | 'pc';

/** Копи-глиф инпута (Figma 760:2590 + 760:2592): задний контур + передний залитый квадрат */
function CopyIcon() {
  return (
    <span aria-hidden="true" className="relative block h-[26px] w-[26px]">
      <span className="absolute left-[8px] top-[0px] h-[12px] w-[12px] rounded-[2px] border-2 border-accent-light" />
      <span className="absolute left-[0px] top-[4px] h-[15.2px] w-[15.2px] rounded-[3px] border-2 border-accent-light bg-[#f6f5fd]" />
    </span>
  );
}

/**
 * Слот QR (Figma 737:1282, 320×320).
 * QR кодирует одноразовую ссылку загрузки с телефона — её выдаёт бэкенд, эндпоинта пока нет,
 * как и оффлайн-генератора QR в зависимостях. Пока слот держит геометрию макета;
 * подмена на реальный QR — ровно здесь, в одном месте.
 */
function QrSlot({ url }: { url: string }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-[320px] w-[320px] shrink-0 flex-col items-center justify-center gap-space-3 rounded-r15 bg-[#f6f5fd] px-space-5 text-center">
      <span className="text-[16px] font-[400] text-[#05010f]/60">{t('wizard.sources.qrPending')}</span>
      <span className="max-w-full break-all text-[12px] text-[#05010f]/40">{url}</span>
    </div>
  );
}

/** Дроп-зона MP4 (Figma 760:2576): 320×320 r15, пунктир accent, grad-soft-10 */
function DropSlot({ onFiles }: { onFiles: (files: FileList | null) => void }) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    onFiles(event.dataTransfer.files);
  };

  return (
    <>
      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        accept="video/mp4,video/*"
        multiple
        onChange={(event) => onFiles(event.target.files)}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => { event.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          'relative flex h-[320px] w-[320px] shrink-0 flex-col items-center justify-center overflow-hidden rounded-r15 bg-grad-soft-10 transition',
          over && 'brightness-150'
        )}
      >
        <span className="dash-panel pointer-events-none absolute inset-0" aria-hidden="true" />
        {/* иконка 41.7×40 r10 на белом (Figma 760:2582) */}
        <span className="relative z-[1] flex h-[40px] w-[41.694px] items-center justify-center rounded-r10 bg-[#f6f5fd]">
          <SvgMaskIcon src="/assets/figma/bg-upload.svg" style={{ width: 20, height: 20, color: 'var(--accent)' }} />
        </span>
        <span className="relative z-[1] mt-[18px] w-[195px] text-center text-[24px] font-[350] leading-normal text-text-80">
          {t('wizard.sources.drop')}
        </span>
      </button>
    </>
  );
}

export function SourcesModal({
  open,
  shareUrl,
  onClose,
  onFiles
}: {
  open: boolean;
  shareUrl: string;
  onClose: () => void;
  onFiles: (files: FileList | null) => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<SourcesTab>('qr');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // Открываемся на вкладке «с ПК»: это единственный путь, который сейчас реально
  // грузит файл. Вкладка QR держит геометрию макета, но ссылка в ней — заглушка
  // (одноразовый токен загрузки с телефона бэкенд пока не выдаёт), и стартовать
  // с неё значит показывать человеку нерабочую ссылку первым экраном.
  useEffect(() => { if (open) { setTab('pc'); setCopied(false); } }, [open]);

  if (!open) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard недоступен (http/permissions) — молча, ссылка видна в инпуте */
    }
  };

  return createPortal(
    /*
     * Затемнение накрывает ВСЮ зону контента — обе колонки (Figma 737:324: 1191×905 r25
     * rgba(5,1,15,.6)). В макете оно и модалка лежат на уровне фрейма, соседями колонок,
     * а не внутри карточки, поэтому рендерим порталом в body с fixed-геометрией по токенам
     * (иначе оверлей запирается в ближайшем relative-предке — карточке этапа).
     */
    <div
      className="fixed left-[calc(var(--sidebar-w)_+_var(--space-6))] right-space-6 top-[var(--rail-pad-y)] z-[60] flex items-center justify-center rounded-r25 bg-[rgba(5,1,15,0.6)] max-lg:inset-0 max-lg:rounded-none"
      style={{ bottom: 'var(--rail-pad-y)' }}
      onMouseDown={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={t('wizard.sources.title')}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-[576px] w-[400px] max-w-[calc(100%-32px)] flex-col rounded-r15 bg-grad-soft-20 px-[40px] pb-[21px] pt-[40px] backdrop-blur-[250px]"
      >
        {/* h=29 по Figma (737:326): базовый line-height 1.5 дал бы 36 и съел бы 7px у слота */}
        <h2 className="h-[29px] shrink-0 text-[24px] font-[400] leading-[29px] text-text-80">{t('wizard.sources.title')}</h2>

        <div className="mt-[28px] shrink-0">
          {tab === 'qr' ? <QrSlot url={shareUrl} /> : <DropSlot onFiles={onFiles} />}
        </div>

        {/* инпут ссылки 320×60 r10 «whitey» + копи-кнопка 60×60 (Figma 760:2586).
            Пока эндпоинта одноразовой ссылки нет, показываем её только на вкладке QR
            и подписываем как недоступную — копировать нерабочий адрес незачем. */}
        <div className="mt-[40px] flex h-[60px] w-[320px] shrink-0 items-center gap-[3px]" hidden={tab === 'pc'}>
          <div className="flex h-full min-w-0 flex-1 items-center rounded-r10 bg-grad-whitey px-[17px]">
            <input
              readOnly
              value={shareUrl}
              aria-label={t('wizard.sources.link')}
              onFocus={(event) => event.currentTarget.select()}
              className="w-full truncate bg-transparent text-[24px] font-[400] text-transparent outline-none"
              style={{ backgroundImage: 'var(--grad-main)', WebkitBackgroundClip: 'text', backgroundClip: 'text' }}
            />
          </div>
          <button
            type="button"
            onClick={copy}
            aria-label={t('wizard.sources.copy')}
            className="flex h-[60px] w-[60px] shrink-0 items-center justify-center rounded-r10 bg-grad-whitey transition hover:brightness-95"
          >
            <CopyIcon />
          </button>
        </div>

        <button
          type="button"
          onClick={() => setTab(tab === 'qr' ? 'pc' : 'qr')}
          className="mt-[19px] h-[19px] shrink-0 text-center text-[16px] font-[400] leading-none text-text-80 underline underline-offset-2 transition hover:text-text"
        >
          {tab === 'qr' ? t('wizard.sources.fromPc') : t('wizard.sources.fromPhone')}
        </button>

        <span aria-live="polite" className="sr-only">{copied ? t('wizard.sources.copied') : ''}</span>
      </section>
    </div>,
    document.body
  );
}
