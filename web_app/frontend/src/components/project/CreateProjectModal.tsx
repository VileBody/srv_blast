import { type ChangeEvent, type DragEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError, durationLabel } from '../../lib/api';
import type { SavedTrack } from '../../lib/types';
import { useToast } from '../../contexts/ToastContext';
import { useWizardStore } from '../../stores/wizardStore';

/**
 * Модалка «Новый проект» (Wireframe 61): заголовок → трек → «Назови проект» → обложка → «Создать».
 *
 * Обложка опциональна: раньше создание запускалось ТОЛЬКО из дроп-зоны, поэтому без картинки
 * проект было не завести, а сам файл в API не уходил. Теперь создаёт кнопка, а файл
 * догружается отдельным запросом (`/api/projects/{id}/cover`) — провал загрузки обложки
 * не отменяет уже созданный проект.
 *
 * Трек тоже опционален, но стоит первым: для пользователя проект — это трек, и имя файла
 * само становится названием проекта (раньше название придумывали с нуля). Загруженный трек
 * кладём в стор визарда, чтобы на этапе «Трек» его не грузили второй раз.
 */
export function CreateProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const meQuery = useQuery({ queryKey: ['me'], queryFn: api.me, enabled: open });
  const inputRef = useRef<HTMLInputElement>(null);
  const trackInputRef = useRef<HTMLInputElement>(null);
  const resetWizard = useWizardStore((state) => state.reset);
  const setWizardTrack = useWizardStore((state) => state.setTrack);
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  const [track, setTrack] = useState<SavedTrack | null>(null);
  const [cover, setCover] = useState<{ file: File; url: string } | null>(null);
  const [nameFocused, setNameFocused] = useState(false);
  /*
   * Загруженный трек надо дать ПРОСЛУШАТЬ: имени файла мало, чтобы убедиться, что залился
   * именно тот трек, а ошибка здесь тянется через весь визард до готовых роликов.
   */
  const trackAudioRef = useRef<HTMLAudioElement | null>(null);
  const trackUrlRef = useRef<string | null>(null);
  const [trackPlaying, setTrackPlaying] = useState(false);

  const stopTrack = () => {
    trackAudioRef.current?.pause();
    trackAudioRef.current = null;
    setTrackPlaying(false);
  };

  const toggleTrackPlay = () => {
    if (!trackUrlRef.current) return;
    if (!trackAudioRef.current) {
      trackAudioRef.current = new Audio(trackUrlRef.current);
      trackAudioRef.current.onended = () => setTrackPlaying(false);
    }
    if (trackPlaying) {
      trackAudioRef.current.pause();
      setTrackPlaying(false);
      return;
    }
    void trackAudioRef.current.play();
    setTrackPlaying(true);
  };

  const trackMutation = useMutation({
    mutationFn: api.uploadTrack,
    onSuccess: async (data, file) => {
      setTrack(data.track);
      stopTrack();
      if (trackUrlRef.current) URL.revokeObjectURL(trackUrlRef.current);
      trackUrlRef.current = URL.createObjectURL(file);
      // название проекта = имя файла без расширения, пока человек не вписал своё
      if (!nameTouched) setName(data.track.filename.replace(/\.[^.]+$/, ''));
      await queryClient.invalidateQueries({ queryKey: ['me'] });
    },
    // 402 — лимит треков: причина и путь к решению, как на этапе «Трек» в визарде
    onError: (error) => {
      const limitReached = error instanceof ApiError && error.status === 402;
      push({
        variant: 'error',
        title: limitReached ? t('wizard.track.limitReached') : t('projectModal.trackFailed'),
        text: limitReached ? String((error.detail as { detail?: string })?.detail ?? '') : undefined,
        action: limitReached ? { label: t('wizard.track.limitCta'), href: '/app/pricing' } : undefined
      });
    }
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const created = await api.createProject({
        name: name.trim() || t('projectModal.defaultName'),
        coverChoice: cover ? 'upload' : 'auto',
        packageType: meQuery.data?.subscription.tier ?? 'TRIAL'
      });
      if (cover) {
        try {
          await api.uploadProjectCover(created.project.id, cover.file);
        } catch {
          // Проект уже создан — обложку можно поменять позже, флоу не рвём.
          push({ variant: 'warning', title: t('projectModal.coverFailed') });
        }
      }
      return created;
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['projects'] });
      push({ variant: 'success', title: t('projectModal.created') });
      /*
       * Сначала переводим черновик визарда на новый проект (reset), потом кладём трек:
       * setProjectId в визарде чистит черновик при смене проекта, и трек бы потерялся.
       */
      if (track) {
        resetWizard(data.project.id);
        setWizardTrack(track);
      }
      onClose();
      navigate(data.redirectTo);
    },
    onError: (error) => push({
      variant: 'error',
      title: t('projectModal.createFailed'),
      text: error instanceof Error ? error.message : undefined
    })
  });

  /* Чистка формы — ТОЛЬКО на открытие модалки. В одном эффекте с обработчиком Escape
     она зависела ещё и от isPending: старт сабмита перезапускал эффект и стирал уже
     выбранные трек/обложку прямо во время создания проекта. */
  useEffect(() => {
    if (!open) return;
    setName('');
    setNameTouched(false);
    setTrack(null);
    setCover(null);
    stopTrack();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // блоб трека живёт ровно столько, сколько открыта модалка
  useEffect(() => () => {
    trackAudioRef.current?.pause();
    if (trackUrlRef.current) URL.revokeObjectURL(trackUrlRef.current);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !createMutation.isPending) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, createMutation.isPending]);

  // objectURL живёт ровно столько, сколько превью обложки
  useEffect(() => () => { if (cover) URL.revokeObjectURL(cover.url); }, [cover]);

  const acceptCover = (file?: File) => {
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type)) {
      push({ variant: 'error', title: t('projectModal.coverFormat') });
      return;
    }
    setCover((current) => {
      if (current) URL.revokeObjectURL(current.url);
      return { file, url: URL.createObjectURL(file) };
    });
  };

  const onInput = (event: ChangeEvent<HTMLInputElement>) => acceptCover(event.target.files?.[0]);
  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    acceptCover(event.dataTransfer.files?.[0]);
  };

  if (!open) return null;
  const busy = createMutation.isPending;

  return createPortal(
    <div
      className="fixed inset-y-[var(--rail-pad-y)] left-[calc(var(--sidebar-w)_+_var(--space-6))] right-space-6 z-overlay flex items-center justify-center rounded-r25 bg-[rgba(5,1,15,0.72)] max-lg:inset-0 max-lg:rounded-none"
      onMouseDown={() => { if (!busy) onClose(); }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={t('projectModal.title')}
        className="subtle-scroll relative max-h-[calc(100%-40px)] w-[460px] max-w-full overflow-y-auto rounded-r25 bg-card-2 p-[40px]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-[20px]">
          <button type="button" onClick={onClose} disabled={busy} aria-label={t('common.back')} className="h-[60px] w-[60px] shrink-0 transition hover:brightness-125 disabled:opacity-40">
            <img src="/assets/figma/btn-back.svg" width="60" height="60" alt="" />
          </button>
          <h2 className="text-[32px] font-[400] leading-[38px] text-text">{t('projectModal.title')}</h2>
        </div>
        {/* Что такое «проект» — для пользователя это трек, но нигде не было сказано */}
        <p className="mt-[14px] text-[16px] leading-[20px] text-text-60">{t('projectModal.whatIsProject')}</p>

        {/* Трек идёт первым: из имени файла берётся название проекта */}
        <div className="mt-[24px]">
          <span className="text-[24px] font-[350] leading-[29px] text-text-80">
            {t('projectModal.trackLabel')} <span className="text-[16px] text-text-40">{t('projectModal.coverOptional')}</span>
          </span>
          <input
            ref={trackInputRef}
            type="file"
            accept="audio/*"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              if (!file.type.startsWith('audio/')) {
                push({ variant: 'error', title: t('projectModal.trackFormat') });
                return;
              }
              trackMutation.mutate(file);
            }}
          />
          {track ? (
            /* Проверка «тот ли файл»: прослушать, увидеть длительность, заменить одним кликом */
            <div className="dash-panel mt-[16px] flex h-[86px] w-full items-center gap-[16px] px-[20px]">
              <button
                type="button"
                onClick={toggleTrackPlay}
                aria-label={trackPlaying ? t('wizard.track.pause') : t('wizard.track.play')}
                className="flex h-[40px] w-[40px] shrink-0 items-center justify-center rounded-full bg-accent-light text-text transition hover:opacity-85"
              >
                {trackPlaying ? (
                  <span className="flex gap-[4px]" aria-hidden="true"><span className="h-[14px] w-[3px] rounded-[1px] bg-text" /><span className="h-[14px] w-[3px] rounded-[1px] bg-text" /></span>
                ) : (
                  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.5 2.5v11l9-5.5-9-5.5Z" fill="currentColor" /></svg>
                )}
              </button>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[18px] leading-[22px] text-text">{track.filename}</span>
                <span className="mt-[4px] block text-[14px] leading-[18px] text-text-60">
                  {durationLabel(track.durationS)} · {t('projectModal.trackReady')}
                </span>
              </span>
              <button
                type="button"
                disabled={busy || trackMutation.isPending}
                onClick={() => trackInputRef.current?.click()}
                className="shrink-0 whitespace-nowrap rounded-r10 border border-[rgba(246,245,253,0.2)] px-[14px] py-[8px] text-[14px] leading-none text-text-60 transition hover:border-accent-light hover:text-text disabled:opacity-50"
              >
                {t('wizard.track.replaceFile')}
              </button>
            </div>
          ) : (
            <button
              type="button"
              disabled={busy || trackMutation.isPending}
              onClick={() => trackInputRef.current?.click()}
              className="dash-panel mt-[16px] flex h-[86px] w-full items-center justify-center gap-[16px] px-[20px] transition hover:brightness-125 disabled:opacity-60"
            >
              <span className="text-[16px] font-[350] leading-[19px] text-text-80">
                {trackMutation.isPending ? t('projectModal.trackUploading') : t('projectModal.trackDrop')}
              </span>
            </button>
          )}
        </div>

        <label className="mt-[24px] block">
          <span className="text-[24px] font-[350] leading-[29px] text-text-80">{t('projectModal.nameLabel')}</span>
          {/* backgroundColor задаём явно: у <input> браузерный дефолт — белая подложка,
              а bg-grad-soft-10 кладёт поверх лишь 10%-градиент, и поле выходило белым
              (светлый текст на нём не читался). Обводка accent появляется на фокусе. */}
          <input
            value={name}
            onChange={(event) => { setNameTouched(true); setName(event.target.value); }}
            placeholder={t('projectModal.namePlaceholder')}
            disabled={busy}
            onFocus={() => setNameFocused(true)}
            onBlur={() => setNameFocused(false)}
            /* boxShadow только инлайном: два Tailwind-класса shadow-* конфликтуют между собой,
               и выигрывает тот, что стоит позже в собранном CSS, а не в className */
            style={{
              backgroundColor: 'var(--card-2)',
              backgroundImage: 'var(--grad-soft-10)',
              boxShadow: nameFocused || name.trim()
                ? 'inset 0 0 0 2px var(--accent-light)'
                : 'inset 0 0 0 1px rgba(246,245,253,0.12)'
            }}
            className="mt-[16px] h-[60px] w-full rounded-r15 px-[20px] text-[18px] text-text outline-none transition placeholder:text-text-40 focus-visible:outline-none"
          />
          {track && !nameTouched && <span className="mt-[10px] block text-[14px] leading-[18px] text-text-60">{t('projectModal.nameFromTrack')}</span>}
        </label>

        <div className="mt-[24px]">
          <span className="text-[24px] font-[350] leading-[29px] text-text-80">
            {t('projectModal.uploadCover')} <span className="text-[16px] text-text-40">{t('projectModal.coverOptional')}</span>
          </span>
          {/* Обложка карточки ≠ обложка ролика — человек мог потратить время не на то */}
          <p className="mt-[8px] text-[15px] leading-[19px] text-text-60">{t('projectModal.coverExplain')}</p>
          <input ref={inputRef} type="file" accept="image/png,image/jpeg" className="sr-only" onChange={onInput} />
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
            className="dash-panel relative mt-[16px] flex h-[220px] w-full flex-col items-center justify-center overflow-hidden rounded-r15 transition hover:brightness-125 disabled:opacity-60"
          >
            {cover ? (
              <>
                <img src={cover.url} alt="" className="absolute inset-0 h-full w-full object-cover" />
                <span className="absolute inset-x-0 bottom-0 truncate bg-[rgba(5,1,15,0.72)] px-[16px] py-[10px] text-[14px] leading-[18px] text-text">
                  {cover.file.name}
                </span>
              </>
            ) : (
              <>
                <span className="flex h-[40px] w-[42px] items-center justify-center rounded-r10 bg-text">
                  <img src="/assets/figma/bg-upload.svg" width="21" height="21" alt="" />
                </span>
                <span className="mt-[12px] w-[160px] text-center text-[16px] font-[350] leading-[19px] text-text-80">{t('projectModal.dropCover')}</span>
              </>
            )}
          </button>
        </div>

        <button
          type="button"
          disabled={busy}
          onClick={() => createMutation.mutate()}
          className="mt-[28px] flex h-[60px] w-full items-center justify-center rounded-r15 bg-grad-main text-[20px] font-[400] leading-none text-text transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60"
        >
          {busy ? <span className="spinner !h-[24px] !w-[24px]" /> : t('projectModal.submit')}
        </button>
      </section>
    </div>,
    document.body
  );
}
