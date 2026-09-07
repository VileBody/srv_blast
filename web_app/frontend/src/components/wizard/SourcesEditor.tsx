import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import type { UserSource } from '../../lib/types';
import { useWizardStore } from '../../stores/wizardStore';
import type { SourceVideoPlan } from '../../stores/wizardStore';

type SourceFormat = SourceVideoPlan['format'];
/** Строка очереди загрузки: живёт до закрытия окна, чтобы был виден факт загрузки. */
type QueueRow = { id: string; name: string; percent: number; state: 'uploading' | 'done' | 'error' };

const timeSeconds = (value: string) => {
  const parts = value.split(':').map(Number);
  return parts.length >= 2 && parts.every(Number.isFinite) ? parts[0] * 60 + parts[1] + (parts[2] ?? 0) / 100 : null;
};

const ICON_BTN = 'flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[9px] bg-accent-20 text-[14px] leading-none text-text-80 transition hover:text-text disabled:opacity-25';
const PILL = 'flex h-[38px] items-center justify-center rounded-r15 px-[18px] text-[15px] transition';

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
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ url: string; qrSvg: string; expiresAt: number }>();
  const [drag, setDrag] = useState<{ planId: string; sourceId: string }>();
  const sources = useQuery({ queryKey: ['sources', projectId], queryFn: () => api.sources(projectId!), enabled: open && Boolean(projectId), refetchInterval: open ? 3000 : false });
  const list = sources.data?.sources ?? [];
  const plans = bg.sourceVideos;
  const active = plans.find(plan => plan.id === activeId) ?? plans[0];

  useEffect(() => {
    if (!open) return;
    setTab('pc'); setError(''); setLink(undefined); setQueue([]); setActiveId(bg.sourceVideos[0]?.id);
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
  const patchRow = (id: string, patch: Partial<QueueRow>) => setQueue(current => current.map(row => row.id === id ? { ...row, ...patch } : row));
  /*
   * Очередь загрузки видна целиком: строка на файл с процентами и итоговым статусом.
   * Один сбойный файл больше не обрывает пачку — он помечается ошибкой, остальные едут дальше.
   */
  const upload = async (files: FileList | null) => {
    if (!projectId || busy) return;
    const picked = Array.from(files ?? []);
    if (!picked.length) return;
    const rows: QueueRow[] = picked.map(file => ({ id: crypto.randomUUID(), name: file.name, percent: 0, state: 'uploading' }));
    setQueue(current => [...current, ...rows]);
    setBusy(true); setError('');
    let targetId = activeId;
    for (const [index, file] of picked.entries()) {
      const row = rows[index];
      try {
        const result = await api.uploadSource(file, projectId, format, percent => patchRow(row.id, { percent }));
        const currentPlans = useWizardStore.getState().background.sourceVideos;
        targetId = append(result.source, currentPlans.find(plan => plan.id === targetId));
        patchRow(row.id, { percent: 100, state: 'done' });
        await sources.refetch();
      } catch (e) {
        report(e);
        patchRow(row.id, { state: 'error' });
      }
    }
    setBusy(false);
  };
  const sourceById = (id: string) => list.find(source => source.id === id);
  const assigned = new Set(plans.flatMap(plan => plan.sourceIds));
  const totalDuration = (plan: SourceVideoPlan) => plan.sourceIds.reduce((sum, id) => sum + (sourceById(id)?.duration ?? 0), 0);
  const fromSeconds = timeSeconds(timingFrom);
  const toSeconds = timeSeconds(timingTo);
  const requiredDuration = fromSeconds !== null && toSeconds !== null ? Math.max(0, toSeconds - fromSeconds) : 0;
  const unused = list.filter(source => !assigned.has(source.id));

  if (!open) return null;
  return createPortal(<div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4" onMouseDown={onClose}>
    <section role="dialog" aria-modal="true" aria-label={t('wizard.sources.title')} className="flex max-h-[calc(var(--app-layout-h,100vh)*.92)] w-[1000px] max-w-full flex-col overflow-hidden rounded-r25 bg-[#21153d] text-text" onMouseDown={e => e.stopPropagation()}>
      <header className="flex shrink-0 items-start justify-between gap-4 px-[28px] pb-[14px] pt-[26px]">
        <div>
          <h2 className="text-[24px] leading-tight">{t('wizard.sources.title')}</h2>
          <p className="mt-[8px] max-w-[640px] text-[14px] leading-[1.45] text-text-60">{t('wizard.sources.editorHint')}</p>
        </div>
        <button type="button" className={ICON_BTN} onClick={onClose} aria-label={t('wizard.sources.close')}>✕</button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-[20px] overflow-auto px-[28px] pb-[20px] lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* Левая колонка — откуда приходят файлы и что с ними происходит прямо сейчас */}
        <div className="flex min-w-0 flex-col gap-[14px]">
          <div className="flex flex-wrap items-center gap-[8px]">
            <span className="mr-[4px] text-[14px] text-text-60">{t('wizard.sources.chooseFormat')}</span>
            {(['9:16', '16:9'] as const).map(value => (
              <button key={value} type="button" aria-pressed={format === value}
                className={cn(PILL, 'border', format === value ? 'border-accent-light bg-grad-soft-20 text-text' : 'border-transparent bg-accent-20 text-text-60 hover:text-text')}
                onClick={() => { setFormat(value); setLink(undefined); }}>{value}</button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-[8px]">
            {([['pc', t('wizard.sources.fromPc')], ['qr', t('wizard.sources.fromPhone')]] as const).map(([value, label]) => (
              <button key={value} type="button" aria-pressed={tab === value} disabled={busy || !projectId}
                className={cn(PILL, 'border', tab === value ? 'border-accent-light bg-grad-soft-20 text-text' : 'border-transparent bg-accent-20 text-text-60 hover:text-text')}
                onClick={async () => {
                  if (value === 'pc') { setTab('pc'); return; }
                  setTab('qr'); setBusy(true); setError('');
                  try { setLink(await api.uploadLink(projectId!, format)); } catch (e) { report(e); } finally { setBusy(false); }
                }}>{label}</button>
            ))}
          </div>

          {tab === 'pc' ? <>
            <input ref={input} type="file" accept="video/mp4,video/quicktime,video/webm" multiple className="sr-only" disabled={busy} onChange={e => { void upload(e.target.files); e.target.value = ''; }} />
            <button type="button" disabled={busy || !projectId}
              className="flex min-h-[132px] w-full flex-col items-center justify-center gap-[10px] rounded-r15 border-2 border-dashed border-accent-light bg-grad-soft-10 px-[20px] text-center transition hover:brightness-110 disabled:opacity-60"
              onClick={() => input.current?.click()}
              onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); void upload(e.dataTransfer.files); }}>
              <span aria-hidden="true" className="flex h-[44px] w-[44px] items-center justify-center rounded-r15 bg-text text-[26px] leading-none text-accent">+</span>
              <span className="text-[15px]">{busy ? t('wizard.warmup.processing') : t('wizard.sources.drop')}</span>
              <span className="text-[12px] text-text-60">{t('wizard.sources.rules', { format })}</span>
            </button>
          </> : link ? <div className="flex flex-col items-center gap-[10px] rounded-r15 bg-grad-soft-10 p-[18px]">
            <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(link.qrSvg)}`} alt={t('wizard.sources.qrAlt')} className="h-48 w-48 rounded-lg bg-white p-2" />
            <input readOnly value={link.url} className="w-full rounded-lg bg-black/20 p-2 text-xs" aria-label={t('wizard.sources.link')} onFocus={e => e.currentTarget.select()} />
            <button type="button" className="text-sm underline" onClick={() => navigator.clipboard.writeText(link.url).catch(report)}>{t('wizard.sources.copy')}</button>
            <p className="text-center text-[13px] leading-[1.4] text-text-60">{t('wizard.sources.expires')}</p>
          </div> : <p className="rounded-r15 bg-grad-soft-10 p-[18px] text-sm text-text-60">{t('wizard.warmup.processing')}</p>}

          {queue.length > 0 && <section className="flex flex-col gap-[12px] rounded-r15 bg-grad-soft-10 p-[16px]">
            <h3 className="text-[14px] text-text-60">{t('wizard.sources.queueTitle', { done: queue.filter(row => row.state === 'done').length, total: queue.length })}</h3>
            {queue.map(row => <div key={row.id} className="min-w-0">
              <div className="mb-[6px] flex items-center justify-between gap-[10px] text-[13px]">
                <span className="min-w-0 truncate">{row.name}</span>
                <span className={cn('shrink-0', row.state === 'error' ? 'text-red-300' : 'text-text-60')}>
                  {row.state === 'done' ? t('wizard.sources.queueDone') : row.state === 'error' ? t('wizard.sources.queueError') : `${row.percent}%`}
                </span>
              </div>
              <div className="h-[6px] overflow-hidden rounded-full bg-accent-20">
                <span className={cn('block h-full rounded-full transition-[width]', row.state === 'error' ? 'bg-red-400' : 'bg-grad-main')} style={{ width: `${row.state === 'error' ? 100 : row.percent}%` }} />
              </div>
            </div>)}
          </section>}

          {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
          {sources.isError && <p role="alert" className="text-sm text-red-300">{t('wizard.sources.listFailed')}</p>}

          {unused.length > 0 && <section className="flex flex-col gap-[8px]">
            <h3 className="text-[14px] text-text-60">{t('wizard.sources.unused')}</h3>
            {unused.map(source => <div key={source.id} className="flex items-center gap-[8px] text-sm">
              {source.localUrl && <video src={source.localUrl} muted playsInline preload="metadata" className="h-12 w-20 shrink-0 rounded-[10px] object-cover" />}
              <button type="button" className="min-w-0 flex-1 truncate rounded-r15 bg-black/20 p-[10px] text-left transition hover:bg-black/30" onClick={() => append(source)}>＋ {source.name} · {source.format} · {source.duration.toFixed(1)} s</button>
              <button type="button" className={ICON_BTN} aria-label={t('wizard.sources.delete')} onClick={async () => { try { await api.deleteSource(source.id); await sources.refetch(); } catch (e) { report(e); } }}>✕</button>
            </div>)}
          </section>}
        </div>

        {/* Правая колонка — что из этого станет роликами в Пуле */}
        <div className="flex min-w-0 flex-col gap-[10px]">
          <h3 className="text-[18px]">{t('wizard.sources.videoPlans')}</h3>
          <p className="text-[12px] leading-[1.4] text-text-60">{t('wizard.sources.videoPlansHint')}</p>
          <div className="flex flex-col gap-[10px]">
            {plans.map((plan, planIndex) => <section key={plan.id}
              className={cn('rounded-r15 border p-[14px] transition', active?.id === plan.id ? 'border-accent-light bg-grad-soft-20' : 'border-transparent bg-black/20')}
              onClick={() => { setActiveId(plan.id); setFormat(plan.format); }}>
              <header className="mb-[10px] flex items-center justify-between gap-[8px]">
                <strong className="text-[15px] font-normal">{t('wizard.sources.videoTitle', { n: planIndex + 1 })} · {plan.format}</strong>
                <div className="flex items-center gap-[6px]">
                  <span className={cn('text-[12px]', requiredDuration > totalDuration(plan) ? 'text-red-300' : 'text-text-60')}>{requiredDuration > totalDuration(plan)
                    ? t('wizard.sources.tooShort', { selected: totalDuration(plan).toFixed(1), required: requiredDuration.toFixed(1) })
                    : t('wizard.sources.total', { seconds: totalDuration(plan).toFixed(1) })}</span>
                  <button type="button" className={ICON_BTN} disabled={planIndex === 0} aria-label={t('wizard.sources.up')} onClick={e => { e.stopPropagation(); movePlan(planIndex, -1); }}>↑</button>
                  <button type="button" className={ICON_BTN} disabled={planIndex === plans.length - 1} aria-label={t('wizard.sources.down')} onClick={e => { e.stopPropagation(); movePlan(planIndex, 1); }}>↓</button>
                </div>
              </header>
              <ol className="flex flex-col gap-[8px]">
                {plan.sourceIds.map((sourceId, index) => {
                  const source = sourceById(sourceId);
                  if (!source) return null;
                  return <li key={sourceId} draggable onDragStart={() => setDrag({ planId: plan.id, sourceId })} onDragOver={e => e.preventDefault()} onDrop={e => {
                    e.preventDefault(); if (!drag || drag.planId !== plan.id || drag.sourceId === sourceId) return;
                    const ids = plan.sourceIds.filter(id => id !== drag.sourceId); ids.splice(index, 0, drag.sourceId); updatePlan(plan.id, ids); setDrag(undefined);
                  }} className="flex items-center gap-[8px] rounded-[12px] bg-black/25 p-[8px]">
                    {source.localUrl && <video src={source.localUrl} muted playsInline preload="metadata" className="h-12 w-20 shrink-0 rounded-[10px] object-cover" />}
                    <span className="min-w-0 flex-1 truncate text-[13px]">{index + 1}. {source.name} · {source.duration.toFixed(1)} s</span>
                    <button type="button" className={ICON_BTN} disabled={index === 0} aria-label={t('wizard.sources.up')} onClick={e => { e.stopPropagation(); const ids = [...plan.sourceIds]; [ids[index-1], ids[index]] = [ids[index], ids[index-1]]; updatePlan(plan.id, ids); }}>↑</button>
                    <button type="button" className={ICON_BTN} disabled={index === plan.sourceIds.length-1} aria-label={t('wizard.sources.down')} onClick={e => { e.stopPropagation(); const ids = [...plan.sourceIds]; [ids[index+1], ids[index]] = [ids[index], ids[index+1]]; updatePlan(plan.id, ids); }}>↓</button>
                    {plan.sourceIds.length > 1 && <button type="button" className="h-[26px] shrink-0 rounded-[9px] bg-accent-20 px-[10px] text-[12px] transition hover:brightness-125" onClick={e => { e.stopPropagation(); split(plan, sourceId); }}>{t('wizard.sources.split')}</button>}
                    <button type="button" className={ICON_BTN} aria-label={t('wizard.sources.remove')} onClick={e => { e.stopPropagation(); updatePlan(plan.id, plan.sourceIds.filter(id => id !== sourceId)); }}>✕</button>
                  </li>;
                })}
              </ol>
            </section>)}
            {!plans.length && <p className="rounded-r15 bg-black/20 p-[16px] text-[13px] leading-[1.45] text-text-60">{t('wizard.sources.noPlans')}</p>}
          </div>
        </div>
      </div>

      {/* Итог: сколько роликов реально уедет в Пул — ответ на «куда попали мои футажи» */}
      <footer className="flex shrink-0 items-center justify-between gap-[16px] border-t border-[rgba(246,245,253,.08)] px-[28px] py-[18px]">
        <span className="text-[14px] text-text-60">{t('wizard.sources.summary', { count: plans.length })}</span>
        <button type="button" className="h-[46px] rounded-r15 bg-accent px-[34px] text-[15px] transition hover:brightness-125" onClick={onClose}>{t('wizard.sources.done')}</button>
      </footer>
    </section>
  </div>, document.body);
}
