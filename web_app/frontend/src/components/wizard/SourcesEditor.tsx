import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import type { UserSource } from '../../lib/types';
import { useWizardStore } from '../../stores/wizardStore';
import type { SourceVideoPlan } from '../../stores/wizardStore';

type SourceFormat = SourceVideoPlan['format'];
const timeSeconds = (value: string) => {
  const parts = value.split(':').map(Number);
  return parts.length >= 2 && parts.every(Number.isFinite) ? parts[0] * 60 + parts[1] + (parts[2] ?? 0) / 100 : null;
};

export function SourcesModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const bg = useWizardStore(s => s.background);
  const projectId = useWizardStore(s => s.projectId);
  const setBackground = useWizardStore(s => s.setBackground);
  const setAllocation = useWizardStore(s => s.setAllocation);
  const timingFrom = useWizardStore(s => s.timingFrom);
  const timingTo = useWizardStore(s => s.timingTo);
  const input = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<'pc' | 'qr'>('pc');
  const [format, setFormat] = useState<SourceFormat>('9:16');
  const [activeId, setActiveId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ url: string; qrSvg: string; expiresAt: number }>();
  const [drag, setDrag] = useState<{ planId: string; sourceId: string }>();
  const sources = useQuery({ queryKey: ['sources', projectId], queryFn: () => api.sources(projectId!), enabled: open && Boolean(projectId), refetchInterval: open ? 3000 : false });
  const list = sources.data?.sources ?? [];
  const plans = bg.sourceVideos;
  const active = plans.find(plan => plan.id === activeId) ?? plans[0];

  useEffect(() => {
    if (!open) return;
    setTab('pc'); setError(''); setLink(undefined); setActiveId(bg.sourceVideos[0]?.id);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [open, onClose]);

  const report = (e: unknown) => {
    const detail = e instanceof ApiError ? (e.detail as { detail?: unknown })?.detail : null;
    setError(typeof detail === 'string' ? detail : e instanceof Error ? e.message : t('wizard.sources.uploadFail'));
  };
  const commit = (next: SourceVideoPlan[]) => {
    const nonempty = next.filter(plan => plan.sourceIds.length > 0);
    setBackground({ sourceVideos: nonempty, uploads: [...new Set(nonempty.flatMap(plan => plan.sourceIds))] });
    setAllocation({ seeded: false, background: {} });
  };
  const append = (source: UserSource, preferred = active) => {
    const livePlans = useWizardStore.getState().background.sourceVideos;
    const livePreferred = preferred && livePlans.find(plan => plan.id === preferred.id);
    const sourceFormat = source.format as SourceFormat;
    const target = livePreferred?.format === sourceFormat ? livePreferred : undefined;
    if (target) {
      commit(livePlans.map(plan => plan.id === target.id && !plan.sourceIds.includes(source.id)
        ? { ...plan, sourceIds: [...plan.sourceIds, source.id] } : plan));
      setActiveId(target.id);
      return target.id;
    }
    const created = { id: `source-video-${crypto.randomUUID()}`, format: sourceFormat, sourceIds: [source.id] };
    commit([...livePlans, created]); setActiveId(created.id); return created.id;
  };
  const updatePlan = (planId: string, sourceIds: string[]) => commit(plans.map(plan => plan.id === planId ? { ...plan, sourceIds } : plan));
  const split = (plan: SourceVideoPlan, sourceId: string) => {
    const created = { id: `source-video-${crypto.randomUUID()}`, format: plan.format, sourceIds: [sourceId] };
    commit([...plans.map(item => item.id === plan.id ? { ...item, sourceIds: item.sourceIds.filter(id => id !== sourceId) } : item), created]);
    setActiveId(created.id);
  };
  const movePlan = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= plans.length) return;
    const next = [...plans];
    [next[index], next[target]] = [next[target], next[index]];
    commit(next);
    setActiveId(next[target].id);
  };
  const upload = async (files: FileList | null) => {
    if (!projectId || busy) return;
    setBusy(true); setError('');
    try {
      let targetId = activeId;
      for (const file of Array.from(files ?? [])) {
        const result = await api.uploadSource(file, projectId, format);
        const currentPlans = useWizardStore.getState().background.sourceVideos;
        targetId = append(result.source, currentPlans.find(plan => plan.id === targetId));
        await sources.refetch();
      }
    } catch (e) { report(e); } finally { setBusy(false); }
  };
  const sourceById = (id: string) => list.find(source => source.id === id);
  const assigned = new Set(plans.flatMap(plan => plan.sourceIds));
  const totalDuration = (plan: SourceVideoPlan) => plan.sourceIds.reduce((sum, id) => sum + (sourceById(id)?.duration ?? 0), 0);
  const fromSeconds = timeSeconds(timingFrom);
  const toSeconds = timeSeconds(timingTo);
  const requiredDuration = fromSeconds !== null && toSeconds !== null ? Math.max(0, toSeconds - fromSeconds) : 0;

  if (!open) return null;
  return createPortal(<div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4" onMouseDown={onClose}>
    <section role="dialog" aria-modal="true" aria-label={t('wizard.sources.title')} className="max-h-[92vh] w-[820px] max-w-full overflow-auto rounded-r25 bg-[#21153d] p-6 text-text" onMouseDown={e => e.stopPropagation()}>
      <header className="mb-3 flex items-center justify-between"><h2 className="text-2xl">{t('wizard.sources.title')}</h2><button type="button" onClick={onClose} aria-label={t('wizard.sources.close')}>✕</button></header>
      <p className="mb-4 text-sm text-text-60">{t('wizard.sources.editorHint')}</p>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm text-text-60">{t('wizard.sources.chooseFormat')}</span>
        {(['9:16', '16:9'] as const).map(value => <button key={value} type="button" aria-pressed={format === value} className="rounded-lg bg-accent-20 px-4 py-2 aria-pressed:bg-accent" onClick={() => { setFormat(value); setLink(undefined); }}>{value}</button>)}
        <button type="button" className="ml-auto rounded-lg bg-accent-20 px-4 py-2" onClick={() => setTab('pc')}>{t('wizard.sources.fromPc')}</button>
        <button type="button" disabled={busy || !projectId} className="rounded-lg bg-accent-20 px-4 py-2" onClick={async () => {
          setTab('qr'); setBusy(true); setError('');
          try { setLink(await api.uploadLink(projectId!, format)); } catch (e) { report(e); } finally { setBusy(false); }
        }}>{t('wizard.sources.fromPhone')}</button>
      </div>
      {tab === 'pc' ? <>
        <input ref={input} type="file" accept="video/mp4,video/quicktime,video/webm" multiple className="sr-only" disabled={busy} onChange={e => { void upload(e.target.files); e.target.value = ''; }} />
        <button type="button" disabled={busy || !projectId} className="min-h-24 w-full rounded-xl border-2 border-dashed border-accent-light p-5" onClick={() => input.current?.click()}
          onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void upload(e.dataTransfer.files); }}>{busy ? t('wizard.warmup.processing') : t('wizard.sources.drop')}</button>
      </> : link ? <div className="flex flex-col items-center gap-3">
        <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(link.qrSvg)}`} alt={t('wizard.sources.qrAlt')} className="h-48 w-48 rounded-lg bg-white p-2" />
        <input readOnly value={link.url} className="w-full rounded-lg bg-black/20 p-2 text-xs" aria-label={t('wizard.sources.link')} onFocus={e => e.currentTarget.select()} />
        <button type="button" className="text-sm underline" onClick={() => navigator.clipboard.writeText(link.url).catch(report)}>{t('wizard.sources.copy')}</button>
        <p className="text-sm text-text-60">{t('wizard.sources.expires')}</p>
      </div> : <p>{t('wizard.warmup.processing')}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
      {sources.isError && <p role="alert" className="mt-3 text-sm text-red-300">{t('wizard.sources.listFailed')}</p>}

      <h3 className="mb-1 mt-5 text-lg">{t('wizard.sources.videoPlans')}</h3>
      <p className="mb-3 text-xs text-text-60">{t('wizard.sources.videoPlansHint')}</p>
      <div className="flex flex-col gap-3">
        {plans.map((plan, planIndex) => <section key={plan.id} className={`rounded-xl border p-3 ${active?.id === plan.id ? 'border-accent-light bg-accent-20' : 'border-border bg-black/10'}`} onClick={() => { setActiveId(plan.id); setFormat(plan.format); }}>
          <header className="mb-2 flex items-center justify-between gap-2">
            <strong>{t('wizard.sources.videoTitle', { n: planIndex + 1 })} · {plan.format}</strong>
            <div className="flex items-center gap-2">
              <span className={`text-xs ${requiredDuration > totalDuration(plan) ? 'text-red-300' : 'text-text-60'}`}>{requiredDuration > totalDuration(plan)
                ? t('wizard.sources.tooShort', { selected: totalDuration(plan).toFixed(1), required: requiredDuration.toFixed(1) })
                : t('wizard.sources.total', { seconds: totalDuration(plan).toFixed(1) })}</span>
              <button type="button" disabled={planIndex === 0} aria-label={t('wizard.sources.up')} onClick={e => { e.stopPropagation(); movePlan(planIndex, -1); }}>↑</button>
              <button type="button" disabled={planIndex === plans.length - 1} aria-label={t('wizard.sources.down')} onClick={e => { e.stopPropagation(); movePlan(planIndex, 1); }}>↓</button>
            </div>
          </header>
          <ol className="flex flex-col gap-2">
            {plan.sourceIds.map((sourceId, index) => {
              const source = sourceById(sourceId);
              if (!source) return null;
              return <li key={sourceId} draggable onDragStart={() => setDrag({ planId: plan.id, sourceId })} onDragOver={e => e.preventDefault()} onDrop={e => {
                e.preventDefault(); if (!drag || drag.planId !== plan.id || drag.sourceId === sourceId) return;
                const ids = plan.sourceIds.filter(id => id !== drag.sourceId); ids.splice(index, 0, drag.sourceId); updatePlan(plan.id, ids); setDrag(undefined);
              }} className="flex items-center gap-2 rounded-lg bg-black/20 p-2">
                {source.localUrl && <video src={source.localUrl} muted playsInline preload="metadata" className="h-12 w-20 rounded object-cover" />}
                <span className="min-w-0 flex-1 truncate text-sm">{index + 1}. {source.name} · {source.duration.toFixed(1)} s</span>
                <button type="button" disabled={index === 0} aria-label={t('wizard.sources.up')} onClick={e => { e.stopPropagation(); const ids = [...plan.sourceIds]; [ids[index-1], ids[index]] = [ids[index], ids[index-1]]; updatePlan(plan.id, ids); }}>↑</button>
                <button type="button" disabled={index === plan.sourceIds.length-1} aria-label={t('wizard.sources.down')} onClick={e => { e.stopPropagation(); const ids = [...plan.sourceIds]; [ids[index+1], ids[index]] = [ids[index], ids[index+1]]; updatePlan(plan.id, ids); }}>↓</button>
                {plan.sourceIds.length > 1 && <button type="button" className="rounded bg-accent-20 px-2 py-1 text-xs" onClick={e => { e.stopPropagation(); split(plan, sourceId); }}>{t('wizard.sources.split')}</button>}
                <button type="button" aria-label={t('wizard.sources.remove')} onClick={e => { e.stopPropagation(); updatePlan(plan.id, plan.sourceIds.filter(id => id !== sourceId)); }}>✕</button>
              </li>;
            })}
          </ol>
        </section>)}
        {!plans.length && <p className="rounded-xl bg-black/10 p-4 text-sm text-text-60">{t('wizard.sources.noPlans')}</p>}
      </div>

      <h3 className="mb-2 mt-5 text-lg">{t('wizard.sources.unused')}</h3>
      <div className="flex flex-col gap-2">
        {list.filter(source => !assigned.has(source.id)).map(source => <div key={source.id} className="flex items-center gap-2 text-sm">
          {source.localUrl && <video src={source.localUrl} muted playsInline preload="metadata" className="h-12 w-20 rounded object-cover" />}
          <button type="button" className="min-w-0 flex-1 truncate rounded-lg bg-black/20 p-2 text-left" onClick={() => append(source)}>＋ {source.name} · {source.format} · {source.duration.toFixed(1)} s</button>
          <button type="button" aria-label={t('wizard.sources.delete')} onClick={async () => { try { await api.deleteSource(source.id); await sources.refetch(); } catch (e) { report(e); } }}>✕</button>
        </div>)}
      </div>
      <button type="button" className="mt-5 w-full rounded-xl bg-accent px-4 py-3" onClick={onClose}>{t('wizard.sources.done')}</button>
    </section>
  </div>, document.body);
}
