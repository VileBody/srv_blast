import { DragEvent, useEffect, useRef, useState } from 'react';
import { Svg, W12 } from './WizardFrame';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { HookConfig, useWizardStore } from '../../stores/wizardStore';
import { AUDIO_FILE_ACCEPT, isAudioFile, isVideoFile, VIDEO_FILE_ACCEPT } from '../../lib/mediaFiles';

type WarmupKind = 'audio' | 'video';

/**
 * Загрузка прогрева (свой звук или видео). По умолчанию пишет в классический
 * hooks.configs.warmup; шаг FX в режиме вариантов передаёт конфиг своего варианта и onPatch.
 */
export function WarmupInput({ value, onPatch }: { value?: HookConfig; onPatch?: (patch: Partial<HookConfig>) => void } = {}) {
  const { t } = useTranslation();
  const storeConfig = useWizardStore(s => s.hooks.configs.warmup);
  const storeSetHooks = useWizardStore(s => s.setHooks);
  const config = (onPatch ? value : storeConfig) ?? {};
  const setHooks = ({ config: patch }: { kind: 'warmup'; config: Partial<HookConfig> }) => (onPatch ? onPatch(patch) : storeSetHooks({ kind: 'warmup', config: patch }));
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selectedKind, setSelectedKind] = useState<WarmupKind | null>(() => config.sound ? (config.warmupKind === 'video' ? 'video' : 'audio') : null);
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

  const goBack = () => {
    if (selectedKind) clear(selectedKind);
    setSelectedKind(null);
    setError('');
  };

  const upload = async (file?: File) => {
    if (!file || !selectedKind) return;
    if (video ? !isVideoFile(file) : !isAudioFile(file)) {
      setError(t(video ? 'wizard.warmup.videoOnly' : 'wizard.warmup.audioOnly'));
      return;
    }
    if (file.size > 200 * 1024 * 1024) { setError(t('wizard.warmup.tooLarge')); return; }
    setBusy(true);
    setError('');
    try {
      const result = await (video ? api.uploadHookVideo(file) : api.uploadHookSound(file));
      setHooks({ kind: 'warmup', config: {
        sound: result.name,
        warmupKind: selectedKind,
        soundPlaybackUrl: result.playbackUrl,
        ...(video
          ? { videoUrl: result.url, videoWidth: result.width, videoHeight: result.height, videoDuration: result.duration, videoHasAudio: result.hasAudio }
          : { soundUrl: result.url, soundDuration: result.duration })
      } });
    } catch (e) {
      const detail = e instanceof ApiError ? (e.detail as { detail?: unknown })?.detail : null;
      setError(typeof detail === 'string' ? detail : t('wizard.fx.soundUploadFailed'));
    } finally {
      setBusy(false);
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
        <button type="button" disabled={busy} className="w12-link" onClick={goBack}>{t('wizard.warmup.back')}</button>
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
      {config.sound && <button type="button" disabled={busy} className="w12-link w12-warm-del" onClick={() => clear(selectedKind)}>{t('wizard.fx.deleteSound')}</button>}
      {error && <p role="alert" className="w12-miss">{error}</p>}
    </div>
  );
}
