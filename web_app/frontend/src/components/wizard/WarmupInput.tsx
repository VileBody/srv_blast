import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { useWizardStore } from '../../stores/wizardStore';

export function WarmupInput() {
  const { t } = useTranslation();
  const config = useWizardStore(s => s.hooks.configs.warmup) ?? {};
  const setHooks = useWizardStore(s => s.setHooks);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const video = config.warmupKind === 'video';
  const clear = (kind: 'audio' | 'video') => setHooks({ kind: 'warmup', config: {
    warmupKind: kind, sound: undefined, soundUrl: undefined, soundPlaybackUrl: undefined,
    soundDuration: undefined, videoUrl: undefined, videoWidth: undefined, videoHeight: undefined,
    videoDuration: undefined, videoHasAudio: undefined
  } });
  return <div className="flex min-w-0 flex-col gap-2">
    <div className="flex gap-2" role="group" aria-label={t('wizard.warmup.title')}>
      {(['audio', 'video'] as const).map(kind => <button key={kind} type="button" disabled={busy} aria-pressed={kind === (video ? 'video' : 'audio')}
        className="rounded-lg bg-accent-20 px-4 py-2 text-sm aria-pressed:bg-accent" onClick={() => { clear(kind); setError(''); }}>{t(`wizard.warmup.${kind}`)}</button>)}
    </div>
    <input ref={input} className="sr-only" type="file" accept={video ? 'video/mp4,video/quicktime,video/webm' : 'audio/*'} disabled={busy}
      onChange={async event => {
        const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
        if (file.size > 200 * 1024 * 1024) { setError(t('wizard.warmup.tooLarge')); return; }
        setBusy(true); setError('');
        try {
          const result = await (video ? api.uploadHookVideo(file) : api.uploadHookSound(file));
          setHooks({ kind: 'warmup', config: { sound: result.name, warmupKind: video ? 'video' : 'audio', soundPlaybackUrl: result.playbackUrl,
            ...(video ? { videoUrl: result.url, videoWidth: result.width, videoHeight: result.height, videoDuration: result.duration, videoHasAudio: result.hasAudio }
              : { soundUrl: result.url, soundDuration: result.duration }) } });
        } catch (e) {
          const detail = e instanceof ApiError ? (e.detail as { detail?: unknown })?.detail : null;
          setError(typeof detail === 'string' ? detail : t('wizard.fx.soundUploadFailed'));
        } finally { setBusy(false); }
      }} />
    <button type="button" className="dash-panel-r10 min-h-[44px] truncate px-3 py-2 text-sm" disabled={busy} onClick={() => input.current?.click()}>{busy ? t('wizard.warmup.processing') : config.sound || t('wizard.warmup.upload')}</button>
    {config.soundPlaybackUrl && (video ? <video className="max-h-28 w-full" src={config.soundPlaybackUrl} controls playsInline /> : <audio className="h-8 w-full" src={config.soundPlaybackUrl} controls />)}
    {config.sound && <button type="button" disabled={busy} className="self-start text-xs underline" onClick={() => clear(video ? 'video' : 'audio')}>{t('wizard.fx.deleteSound')}</button>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
  </div>;
}
