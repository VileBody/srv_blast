import { DragEvent, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { useWizardStore } from '../../stores/wizardStore';

type WarmupKind = 'audio' | 'video';

export function WarmupInput() {
  const { t } = useTranslation();
  const config = useWizardStore(s => s.hooks.configs.warmup) ?? {};
  const setHooks = useWizardStore(s => s.setHooks);
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
    <div className="grid min-w-0 grid-cols-2 gap-3" role="group" aria-label={t('wizard.warmup.title')}>
      {(['audio', 'video'] as const).map(kind => (
        <button key={kind} type="button"
          className="flex min-h-[52px] items-center justify-center rounded-r10 border border-[rgba(246,245,253,0.16)] bg-grad-soft-10 px-4 text-[16px] text-text-60 transition hover:border-accent-light hover:text-text"
          onClick={() => choose(kind)}>{t(`wizard.warmup.${kind}`)}</button>
      ))}
    </div>
  );

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <span className="rounded-r10 bg-grad-soft-20 px-3 py-2 text-[15px] text-text-80">{t(`wizard.warmup.${selectedKind}`)}</span>
        <button type="button" disabled={busy} className="text-[14px] text-text-60 underline underline-offset-4 transition hover:text-text disabled:opacity-40" onClick={goBack}>{t('wizard.warmup.back')}</button>
      </div>
      <input ref={input} className="sr-only" type="file" accept={video ? 'video/mp4,video/quicktime,video/webm' : 'audio/*'} disabled={busy}
        onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; void upload(file); }} />
      <button type="button" className="dash-panel-r10 flex min-h-[58px] items-center justify-center truncate px-3 py-2 text-sm transition hover:brightness-125" disabled={busy}
        onClick={() => input.current?.click()} onDragOver={event => event.preventDefault()} onDrop={onDrop}>
        {busy ? t('wizard.warmup.processing') : config.sound || t('wizard.warmup.drop')}
      </button>
      {config.soundPlaybackUrl && (video
        ? <video className="max-h-28 w-full" src={config.soundPlaybackUrl} controls playsInline />
        : <audio className="h-8 w-full" src={config.soundPlaybackUrl} controls />)}
      {config.sound && <button type="button" disabled={busy} className="self-start text-xs underline" onClick={() => clear(selectedKind)}>{t('wizard.fx.deleteSound')}</button>}
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    </div>
  );
}
