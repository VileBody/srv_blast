import { DragEvent, useEffect, useRef, useState } from 'react';
import { create } from 'zustand';
import { Svg, W12 } from './WizardFrame';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { HookConfig, useWizardStore } from '../../stores/wizardStore';
import { AUDIO_FILE_ACCEPT, isAudioFile, isVideoFile, VIDEO_FILE_ACCEPT } from '../../lib/mediaFiles';

type WarmupKind = 'audio' | 'video';

/**
 * Загрузка живёт дольше компонента. Шаг FX перемонтирует ввод на каждый вариант: ушёл на
 * другой вариант посреди загрузки и вернулся — раньше видел пустое поле и мог запустить
 * вторую загрузку параллельно, а ошибка первой терялась. Состояние — по ключу варианта.
 */
type UploadJob = { kind: WarmupKind; busy: boolean; error: string };
const useWarmupUploads = create<{ jobs: Record<string, UploadJob> }>(() => ({ jobs: {} }));
const IDLE_JOB: UploadJob = { kind: 'audio', busy: false, error: '' };
const patchJob = (key: string, patch: Partial<UploadJob>) =>
  useWarmupUploads.setState((s) => ({ jobs: { ...s.jobs, [key]: { ...IDLE_JOB, ...s.jobs[key], ...patch } } }));

/**
 * Загрузка прогрева (свой звук или видео). По умолчанию пишет в классический
 * hooks.configs.warmup; шаг FX в режиме вариантов передаёт конфиг своего варианта, onPatch
 * и uploadKey (id варианта) — ключ, под которым живёт его загрузка.
 */
export function WarmupInput({ value, onPatch, uploadKey = 'classic' }: { value?: HookConfig; onPatch?: (patch: Partial<HookConfig>) => void; uploadKey?: string } = {}) {
  const { t } = useTranslation();
  const storeConfig = useWizardStore(s => s.hooks.configs.warmup);
  const storeSetHooks = useWizardStore(s => s.setHooks);
  const config = (onPatch ? value : storeConfig) ?? {};
  const setHooks = ({ config: patch }: { kind: 'warmup'; config: Partial<HookConfig> }) => (onPatch ? onPatch(patch) : storeSetHooks({ kind: 'warmup', config: patch }));
  const input = useRef<HTMLInputElement>(null);
  const job = useWarmupUploads(s => s.jobs[uploadKey]);
  const busy = Boolean(job?.busy);
  const error = job?.error ?? '';
  const setError = (message: string) => patchJob(uploadKey, { error: message });
  const [selectedKind, setSelectedKind] = useState<WarmupKind | null>(() => (job?.busy ? job.kind : config.sound ? (config.warmupKind === 'video' ? 'video' : 'audio') : null));
  // «Назад» стирает загруженный файл — сначала спрашиваем
  const [confirmBack, setConfirmBack] = useState(false);
  const video = selectedKind === 'video';

  useEffect(() => {
    if (config.sound) setSelectedKind(config.warmupKind === 'video' ? 'video' : 'audio');
  }, [config.sound, config.warmupKind]);

  const clear = (kind: WarmupKind) => setHooks({ kind: 'warmup', config: {
    warmupKind: kind, sound: undefined, soundUrl: undefined, soundPlaybackUrl: undefined,
    soundDuration: undefined, videoUrl: undefined, videoWidth: undefined, videoHeight: undefined,
    videoDuration: undefined, videoHasAudio: undefined
  } });

  const choose = (kind: WarmupKind) => {
    clear(kind);
    setSelectedKind(kind);
    setError('');
  };

  const back = () => {
    if (selectedKind) clear(selectedKind);
    setSelectedKind(null);
    setConfirmBack(false);
    setError('');
  };
  const goBack = () => { if (config.sound) setConfirmBack(true); else back(); };

  const upload = async (file?: File) => {
    if (!file || !selectedKind || useWarmupUploads.getState().jobs[uploadKey]?.busy) return;
    if (video ? !isVideoFile(file) : !isAudioFile(file)) {
      setError(t(video ? 'wizard.warmup.videoOnly' : 'wizard.warmup.audioOnly'));
      return;
    }
    if (file.size > 200 * 1024 * 1024) { setError(t('wizard.warmup.tooLarge')); return; }
    const kind = selectedKind;
    patchJob(uploadKey, { kind, busy: true, error: '' });
    try {
      const result = await (video ? api.uploadHookVideo(file) : api.uploadHookSound(file));
      setHooks({ kind: 'warmup', config: {
        sound: result.name,
        warmupKind: kind,
        soundPlaybackUrl: result.playbackUrl,
        ...(video
          ? { videoUrl: result.url, videoWidth: result.width, videoHeight: result.height, videoDuration: result.duration, videoHasAudio: result.hasAudio }
          : { soundUrl: result.url, soundDuration: result.duration })
      } });
      patchJob(uploadKey, { busy: false });
    } catch (e) {
      const detail = e instanceof ApiError ? (e.detail as { detail?: unknown })?.detail : null;
      patchJob(uploadKey, { busy: false, error: typeof detail === 'string' ? detail : t(video ? 'wizard.warmup.videoUploadFailed' : 'wizard.fx.soundUploadFailed') });
    }
  };

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    void upload(event.dataTransfer.files?.[0]);
  };

  if (!selectedKind) return (
    <div className="w12-warm-kinds" role="group" aria-label={t('wizard.warmup.title')}>
      {(['audio', 'video'] as const).map(kind => (
        <button key={kind} type="button" className="w12-small-btn" onClick={() => choose(kind)}><span className="w12-l">{t(`wizard.warmup.${kind}`)}</span></button>
      ))}
    </div>
  );

  return (
    <div className="w12-warm">
      <div className="w12-warm-head">
        <span className="w12-chip"><span className="w12-l">{t(`wizard.warmup.${selectedKind}`)}</span></span>
        {confirmBack ? (
          <span className="w12-warm-confirm" role="group" aria-label={t('wizard.warmup.backConfirm')}>
            <span>{t('wizard.warmup.backConfirm')}</span>
            <button type="button" className="w12-link" onClick={back}>{t('wizard.warmup.backConfirmYes')}</button>
            <button type="button" className="w12-link" onClick={() => setConfirmBack(false)}>{t('wizard.warmup.cancel')}</button>
          </span>
        ) : (
          <button type="button" disabled={busy} className="w12-link" onClick={goBack}>{t('wizard.warmup.back')}</button>
        )}
      </div>
      <input ref={input} className="sr-only" type="file" accept={video ? VIDEO_FILE_ACCEPT : AUDIO_FILE_ACCEPT} disabled={busy}
        onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void upload(file); }} />
      <button type="button" className="w12-drop w12-drop-sm" disabled={busy}
        onClick={() => input.current?.click()} onDragOver={event => event.preventDefault()} onDrop={onDrop}>
        <span className="w12-plus">{busy ? <span className="spinner" aria-hidden="true" /> : <Svg>{W12.upload}</Svg>}</span>
        <span><b>{busy ? t('wizard.warmup.processing') : config.sound || t('wizard.warmup.drop')}</b></span>
      </button>
      {config.soundPlaybackUrl && (video
        ? <video className="w12-warm-media" src={config.soundPlaybackUrl} controls playsInline />
        : <audio className="w12-warm-audio" src={config.soundPlaybackUrl} controls />)}
      {config.sound && <button type="button" disabled={busy} className="w12-link w12-warm-del" onClick={() => clear(selectedKind)}>{t(video ? 'wizard.warmup.deleteVideo' : 'wizard.fx.deleteSound')}</button>}
      {error && <p role="alert" className="w12-miss">{error}</p>}
    </div>
  );
}
