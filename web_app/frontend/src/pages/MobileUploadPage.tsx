import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

type UploadInfo = { format: string; remaining: number };
type UploadRow = { name: string; percent: number; state: 'uploading' | 'done' | 'error' };

export function MobileUploadPage() {
  const { t } = useTranslation();
  const token = window.location.hash.slice(1);
  const input = useRef<HTMLInputElement>(null);
  const [info, setInfo] = useState<UploadInfo>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [rows, setRows] = useState<UploadRow[]>([]);

  const requestInfo = async (): Promise<UploadInfo> => {
    const response = await fetch('/api/mobile-upload', { credentials: 'omit', headers: { 'X-Upload-Token': token } });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : t('wizard.sources.uploadFail'));
    return data;
  };
  const upload = (file: File, rowIndex: number) => new Promise<void>((resolve, reject) => {
    const body = new FormData(); body.append('file', file);
    const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/mobile-upload'); xhr.setRequestHeader('X-Upload-Token', token);
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round(event.loaded / event.total * 100);
      setRows(current => current.map((row, index) => index === rowIndex ? { ...row, percent } : row));
    };
    xhr.onerror = () => reject(new Error(t('wizard.sources.uploadFail')));
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { setRows(current => current.map((row, index) => index === rowIndex ? { ...row, percent: 100, state: 'done' } : row)); resolve(); }
      else { const detail = (() => { try { return JSON.parse(xhr.responseText)?.detail; } catch { return null; } })(); reject(new Error(typeof detail === 'string' ? detail : t('wizard.sources.uploadFail'))); }
    };
    xhr.send(body);
  });

  useEffect(() => { void requestInfo().then(setInfo).catch(e => setError(String(e.message))); }, [token]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = async (files: FileList | null) => {
    const queue = Array.from(files ?? []); if (!queue.length || busy) return;
    setBusy(true); setError('');
    const start = rows.length;
    setRows(current => [...current, ...queue.map(file => ({ name: file.name, percent: 0, state: 'uploading' as const }))]);
    try {
      for (const [index, file] of queue.entries()) await upload(file, start + index);
      setInfo(await requestInfo());
    } catch (e) {
      setRows(current => current.map(row => row.state === 'uploading' ? { ...row, state: 'error' } : row));
      setError(e instanceof Error ? e.message : t('wizard.sources.uploadFail'));
    } finally { setBusy(false); }
  };

  return <main className="min-h-[100dvh] bg-[#100820] px-5 py-8 text-text">
    <div className="mx-auto flex max-w-lg flex-col gap-5">
      <div><h1 className="text-[30px] leading-tight">{t('wizard.sources.title')}</h1>{info && <p className="mt-3 text-[15px] leading-6 text-text-60">{t('wizard.sources.rules', { format: info.format })}</p>}</div>
      {info && <>
        <input ref={input} type="file" accept="video/*" multiple disabled={busy || info.remaining <= 0} className="sr-only" onChange={e => { void pick(e.target.files); e.target.value = ''; }} />
        <button type="button" disabled={busy || info.remaining <= 0} onClick={() => input.current?.click()} className="flex min-h-36 flex-col items-center justify-center gap-3 rounded-r15 border-2 border-dashed border-accent-light bg-grad-soft-10 px-6 text-center disabled:opacity-50">
          <span className="flex h-12 w-12 items-center justify-center rounded-r15 bg-text text-[28px] leading-none text-accent">+</span>
          <span>{busy ? t('wizard.warmup.processing') : t('wizard.sources.drop')}</span>
          <span className="text-xs text-text-60">{t('wizard.sources.remaining', { count: info.remaining })}</span>
        </button>
      </>}
      {rows.length > 0 && <section className="flex flex-col gap-3 rounded-r15 bg-grad-soft-10 p-4">
        {rows.map((row, index) => <div key={`${row.name}-${index}`} className="min-w-0">
          <div className="mb-2 flex items-center justify-between gap-3 text-sm"><span className="truncate">{row.state === 'done' ? '✓' : row.state === 'error' ? '!' : '↑'} {row.name}</span><span className="shrink-0 text-text-60">{row.percent}%</span></div>
          <div className="h-2 overflow-hidden rounded-full bg-accent-20"><span className="block h-full rounded-full bg-grad-main transition-[width]" style={{ width: `${row.percent}%` }} /></div>
        </div>)}
      </section>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {rows.some(row => row.state === 'done') && !busy && <p className="rounded-r15 border border-accent-light bg-grad-soft-10 p-4 leading-6">{t('wizard.sources.phoneDone')}</p>}
    </div>
  </main>;
}
