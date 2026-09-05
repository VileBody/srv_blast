import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { useWizardStore } from '../../stores/wizardStore';

export function SourcesModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const bg = useWizardStore(s => s.background);
  const projectId = useWizardStore(s => s.projectId);
  const setBackground = useWizardStore(s => s.setBackground);
  const input = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<'pc' | 'qr'>('pc');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ url: string; qrSvg: string; expiresAt: number }>();
  const [drag, setDrag] = useState<string>();
  const format = bg.uploads.length ? bg.sourceFormat ?? '9:16' : bg.footageType === 'cine16x9' ? '16:9' : '9:16';
  const sources = useQuery({ queryKey: ['sources', projectId], queryFn: () => api.sources(projectId!), enabled: open && Boolean(projectId), refetchInterval: open ? 3000 : false });
  const list = sources.data?.sources ?? [];
  const ordered = bg.uploads.map(id => list.find(s => s.id === id)).filter(s => s !== undefined);
  useEffect(() => {
    if (!open) return;
    setTab('pc'); setError(''); setLink(undefined);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [open, onClose]);
  const report = (e: unknown) => {
    const detail = e instanceof ApiError ? (e.detail as { detail?: unknown })?.detail : null;
    setError(typeof detail === 'string' ? detail : t('wizard.sources.uploadFail'));
  };
  const select = (ids: string[]) => setBackground({ mode: 'footage', uploads: ids, sourceFormat: format, footage: [], photo: [], color: undefined });
  const upload = async (files: FileList | null) => {
    if (!projectId || busy) return;
    setBusy(true); setError('');
    try {
      for (const file of Array.from(files ?? [])) {
        const result = await api.uploadSource(file, projectId, format);
        const current = useWizardStore.getState().background.uploads;
        select([...current, result.source.id]);
        await sources.refetch();
      }
    } catch (e) { report(e); } finally { setBusy(false); }
  };
  if (!open) return null;
  return createPortal(<div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4" onMouseDown={onClose}>
    <section role="dialog" aria-modal="true" aria-label={t('wizard.sources.title')} className="max-h-[90vh] w-[760px] max-w-full overflow-auto rounded-r25 bg-[#21153d] p-6 text-text" onMouseDown={e => e.stopPropagation()}>
      <header className="mb-4 flex items-center justify-between"><h2 className="text-2xl">{t('wizard.sources.title')}</h2><button type="button" onClick={onClose} aria-label={t('wizard.sources.close')}>✕</button></header>
      <p className="mb-4 text-sm text-text-60">{t('wizard.sources.rules', { format })}</p>
      <div className="mb-4 flex gap-2">
        <button type="button" className="rounded-lg bg-accent-20 px-4 py-2" onClick={() => setTab('pc')}>{t('wizard.sources.fromPc')}</button>
        <button type="button" disabled={busy || !projectId} className="rounded-lg bg-accent-20 px-4 py-2" onClick={async () => {
          setTab('qr'); if (link && link.expiresAt * 1000 > Date.now()) return;
          setBusy(true); try { setLink(await api.uploadLink(projectId!, format)); } catch (e) { report(e); } finally { setBusy(false); }
        }}>{t('wizard.sources.fromPhone')}</button>
      </div>
      {tab === 'pc' ? <>
        <input ref={input} type="file" accept="video/mp4,video/quicktime,video/webm" multiple className="sr-only" disabled={busy} onChange={e => { void upload(e.target.files); e.target.value = ''; }} />
        <button type="button" disabled={busy || !projectId} className="min-h-28 w-full rounded-xl border-2 border-dashed border-accent-light p-6" onClick={() => input.current?.click()}
          onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void upload(e.dataTransfer.files); }}>{busy ? t('wizard.warmup.processing') : t('wizard.sources.drop')}</button>
      </> : link ? <div className="flex flex-col items-center gap-3">
        <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(link.qrSvg)}`} alt={t('wizard.sources.qrAlt')} className="h-56 w-56 rounded-lg bg-white p-2" />
        <input readOnly value={link.url} className="w-full rounded-lg bg-black/20 p-2 text-xs" aria-label={t('wizard.sources.link')} onFocus={e => e.currentTarget.select()} />
        <button type="button" className="text-sm underline" onClick={() => navigator.clipboard.writeText(link.url).catch(report)}>{t('wizard.sources.copy')}</button>
        <p className="text-sm text-text-60">{t('wizard.sources.expires')}</p>
      </div> : <p>{t('wizard.warmup.processing')}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
      {sources.isError && <p role="alert" className="mt-3 text-sm text-red-300">{t('wizard.sources.listFailed')}</p>}
      <h3 className="mb-2 mt-5 text-lg">{t('wizard.sources.order')}</h3>
      <p className="mb-3 text-xs text-text-60">{t('wizard.sources.selectionHint')}</p>
      <ol className="flex flex-col gap-2">
        {ordered.map((source, index) => <li key={source.id} draggable onDragStart={() => setDrag(source.id)} onDragOver={e => e.preventDefault()} onDrop={e => {
          e.preventDefault(); if (!drag || drag === source.id) return;
          const ids = bg.uploads.filter(id => id !== drag); ids.splice(index, 0, drag); select(ids); setDrag(undefined);
        }} className="flex items-center gap-2 rounded-lg bg-accent-20 p-2">
          <span className="text-xs">{index+1}.</span><span className="min-w-0 flex-1 truncate text-sm">{source.name} · {source.duration.toFixed(1)} s · {source.format}</span>
          <button type="button" disabled={index === 0} aria-label={t('wizard.sources.up')} onClick={() => { const ids = [...bg.uploads]; [ids[index-1], ids[index]] = [ids[index], ids[index-1]]; select(ids); }}>↑</button>
          <button type="button" disabled={index === ordered.length-1} aria-label={t('wizard.sources.down')} onClick={() => { const ids = [...bg.uploads]; [ids[index+1], ids[index]] = [ids[index], ids[index+1]]; select(ids); }}>↓</button>
          <button type="button" aria-label={t('wizard.sources.remove')} onClick={() => select(bg.uploads.filter(id => id !== source.id))}>✕</button>
        </li>)}
      </ol>
      <p className="my-2 text-xs text-text-60">{t('wizard.sources.total', { seconds: ordered.reduce((sum,s) => sum+s.duration, 0).toFixed(1) })}</p>
      <div className="flex flex-col gap-2">
        {list.filter(s => !bg.uploads.includes(s.id)).map(source => <div key={source.id} className="flex items-center gap-2 text-sm">
          <button type="button" disabled={source.format !== format} className="min-w-0 flex-1 truncate rounded-lg bg-black/20 p-2 text-left disabled:opacity-40" onClick={() => select([...bg.uploads, source.id])}>＋ {source.name} · {source.format}</button>
          <button type="button" aria-label={t('wizard.sources.delete')} onClick={async () => { try { await api.deleteSource(source.id); await sources.refetch(); } catch (e) { report(e); } }}>✕</button>
        </div>)}
      </div>
      <button type="button" className="mt-5 w-full rounded-xl bg-accent px-4 py-3" onClick={onClose}>{t('wizard.sources.done')}</button>
    </section>
  </div>, document.body);
}
