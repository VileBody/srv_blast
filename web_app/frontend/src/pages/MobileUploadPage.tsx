import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

export function MobileUploadPage() {
  const { t } = useTranslation();
  const token = window.location.hash.slice(1);
  const [info, setInfo] = useState<{ format: string; remaining: number }>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const send = async (file?: File) => {
    const body = file ? new FormData() : undefined; if (body && file) body.append('file', file);
    const response = await fetch('/api/mobile-upload', { method: file ? 'POST' : 'GET', body, credentials: 'omit', headers: { 'X-Upload-Token': token } });
    const data = await response.json(); if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('wizard.sources.uploadFail')); return data;
  };
  useEffect(() => { void send().then(setInfo).catch(e => setError(String(e.message))); }, [token]);
  return <main className="min-h-screen bg-[#100820] p-6 text-text"><div className="mx-auto flex max-w-lg flex-col gap-5">
    <h1 className="text-2xl">{t('wizard.sources.title')}</h1>
    {info && <>
      <p className="text-sm text-text-60">{t('wizard.sources.rules', { format: info.format })}</p>
      <label className="rounded-xl border-2 border-dashed border-accent-light p-8 text-center">{busy ? t('wizard.warmup.processing') : t('wizard.warmup.upload')}
        <input type="file" accept="video/*" multiple disabled={busy || info.remaining <= 0} className="mt-4 block w-full text-sm" onChange={async e => {
          const files = Array.from(e.target.files ?? []); e.target.value = ''; setBusy(true); setError('');
          try { for (const file of files) { const result = await send(file); setUploaded(names => [...names, result.name]); } setInfo(await send()); }
          catch (e) { setError(e instanceof Error ? e.message : t('wizard.sources.uploadFail')); } finally { setBusy(false); }
        }} />
      </label>
    </>}
    {error && <p role="alert" className="text-red-300">{error}</p>}
    <ul>{uploaded.map((name,index) => <li key={index}>✓ {name}</li>)}</ul>
    {uploaded.length > 0 && <p>{t('wizard.sources.phoneDone')}</p>}
  </div></main>;
}
