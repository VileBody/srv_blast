import { ReactNode, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import { isVideoFile, VIDEO_FILE_ACCEPT } from '../../lib/mediaFiles';
import type { UserSource } from '../../lib/types';
import { useWizardStore } from '../../stores/wizardStore';
import type { SourceVideoPlan } from '../../stores/wizardStore';
import { useModalCount } from '../ui/Modal';
import { Svg, W12 } from './WizardFrame';

type SourceFormat = SourceVideoPlan['format'];
/** Строка очереди загрузки: живёт до закрытия окна, чтобы был виден факт загрузки. */
type QueueRow = { id: string; name: string; percent: number; state: 'uploading' | 'done' | 'error' };

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
  const [tab, setTab] = useState<'pc' | 'qr'>('pc');
  const [format, setFormat] = useState<SourceFormat>('9:16');
  const [activeId, setActiveId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ url: string; qrSvg: string; expiresAt: number }>();
  const [drag, setDrag] = useState<{ planId: string; sourceId: string }>();
  /** «Удалить» у неиспользованного файла стирает его с сервера — второй клик подтверждает */
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  /*
   * Опрос — только пока что-то может прийти: идёт загрузка с компьютера или открыт QR для
   * телефона (файлы оттуда появляются сами). Раньше список дёргался каждые 3 с всегда, а
   * каждый ответ — новые presigned-ссылки, и превью видео перезагружались.
   */
  const awaitingFiles = busy || (tab === 'qr' && Boolean(link));
  const sources = useQuery({ queryKey: ['sources', projectId], queryFn: () => api.sources(projectId!), enabled: open && Boolean(projectId), refetchInterval: open && awaitingFiles ? 3000 : false });
  const list = sources.data?.sources ?? [];
  // Превью держит первую ссылку на файл, пока окно открыто: новая presigned-ссылка на тот же
  // файл при опросе иначе заново грузила бы <video>.
  const thumbUrls = useRef(new Map<string, string>());
  const thumbUrl = (source: UserSource) => {
    if (!source.localUrl) return undefined;
    if (!thumbUrls.current.has(source.id)) thumbUrls.current.set(source.id, source.localUrl);
    return thumbUrls.current.get(source.id);
  };
  const seconds = (value: number) => t('wizard.sources.seconds', { value: value.toFixed(1) });
  // onClose родитель передаёт инлайном: в зависимостях эффекта фокуса он перезапускал его на
  // каждый рендер родителя (после каждой загрузки фокус прыгал на заголовок)
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const plans = bg.sourceVideos;
  const active = plans.find(plan => plan.id === activeId) ?? plans[0];

  useEffect(() => {
    if (!open) return;
    setTab('pc'); setError(''); setLink(undefined); setQueue([]); setActiveId(bg.sourceVideos[0]?.id); setConfirmDelete(null);
    thumbUrls.current.clear();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const upload = async (picked: File[]) => {
    if (!projectId || busy) return;
    if (!picked.length) return;
    if (picked.some(file => !isVideoFile(file))) {
      setError(t('wizard.sources.videoOnly'));
      return;
    }
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

  // модалка считается в useModalCount: подсказки визарда при открытом окне молчат
  useEffect(() => {
    if (!open) return undefined;
    useModalCount.getState().inc();
    const returnTo = document.activeElement;
    titleRef.current?.focus({ preventScroll: true });
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onCloseRef.current(); };
    window.addEventListener('keydown', onKey);
    return () => {
      useModalCount.getState().dec();
      window.removeEventListener('keydown', onKey);
      if (returnTo instanceof HTMLElement) returnTo.focus({ preventScroll: true });
    };
  }, [open]);

  if (!open) return null;
  const vert = { transform: 'rotate(90deg)' };
  const mini = (label: string, icon: ReactNode, onClick: () => void, disabled = false) => (
    <button type="button" className="w12-mini" aria-label={label} disabled={disabled} onClick={(event) => { event.stopPropagation(); onClick(); }}>{icon}</button>
  );
  const pick = (files: FileList | null) => { const list = Array.from(files ?? []); if (list.length) void upload(list); };

  return createPortal(
    <div className="w12">
      <div className="w12-scrim" style={{ zIndex: 'var(--z-modal)' } as React.CSSProperties} onMouseDown={onClose}>
        <section className="w12-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} onMouseDown={(event) => event.stopPropagation()}>
          <header className="w12-m-head">
            <div>
              <h2 id={titleId} ref={titleRef} tabIndex={-1}>{t('wizard.sources.title')}</h2>
              <p>{t('wizard.sources.editorHint')}</p>
            </div>
            <button type="button" className="w12-icon-btn" aria-label={t('wizard.sources.close')} onClick={onClose}><Svg>{W12.close}</Svg></button>
          </header>
          <div className="w12-m-body">
            {/* левая колонка — откуда приходят файлы и что с ними происходит прямо сейчас */}
            <div className="w12-m-col">
              <div className="w12-m-row">
                <span>{t('wizard.sources.chooseFormat')}</span>
                <div className="w12-types">
                  {(['9:16', '16:9'] as const).map((value) => (
                    <button key={value} type="button" className="w12-type" aria-pressed={format === value} onClick={() => { setFormat(value); setLink(undefined); }}><span className="w12-l">{value}</span></button>
                  ))}
                </div>
              </div>
              <div className="w12-m-row max-md:hidden">
                <span>{t('wizard.sources.chooseSource')}</span>
                <div className="w12-types">
                  <button type="button" className="w12-type" aria-pressed={tab === 'pc'} onClick={() => setTab('pc')}><span className="w12-l">{t('wizard.sources.fromPc')}</span></button>
                  <button
                    type="button"
                    className="w12-type"
                    aria-pressed={tab === 'qr'}
                    disabled={busy || !projectId}
                    onClick={async () => {
                      setTab('qr'); setBusy(true); setError('');
                      try { setLink(await api.uploadLink(projectId!, format)); } catch (e) { report(e); } finally { setBusy(false); }
                    }}
                  >
                    <span className="w12-l">{t('wizard.sources.fromPhone')}</span>
                  </button>
                </div>
              </div>

              {tab === 'pc' ? (
                <>
                  <input ref={input} type="file" accept={VIDEO_FILE_ACCEPT} multiple className="sr-only" tabIndex={-1} onChange={(e) => { pick(e.target.files); e.target.value = ''; }} />
                  <button
                    type="button"
                    className="w12-drop"
                    disabled={busy || !projectId}
                    onClick={() => input.current?.click()}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files); }}
                  >
                    <span className="w12-plus">{busy ? <span className="spinner" aria-hidden="true" /> : <Svg>{W12.upload}</Svg>}</span>
                    <span><b>{busy ? t('wizard.warmup.processing') : t('wizard.sources.drop')}</b><span>{t('wizard.sources.rules', { format })}</span></span>
                  </button>
                </>
              ) : link ? (
                <div className="w12-qr">
                  <img src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(link.qrSvg)}`} alt={t('wizard.sources.qrAlt')} className="w12-qr-img" />
                  <div className="w12-qr-link">
                    <input readOnly value={link.url} aria-label={t('wizard.sources.link')} onFocus={(e) => e.currentTarget.select()} />
                    <button type="button" className="w12-small-btn" onClick={() => navigator.clipboard.writeText(link.url).catch(report)}><span className="w12-l">{t('wizard.sources.copy')}</span></button>
                  </div>
                  <span className="w12-m-hint" style={{ margin: 0 }}>{t('wizard.sources.expires')}</span>
                </div>
              ) : <div className="w12-empty-plan">{t('wizard.warmup.processing')}</div>}

              {queue.length > 0 && (
                <div className="w12-queue">
                  {queue.map((row) => (
                    <div key={row.id} className="w12-q-row">
                      <span>{row.name}</span>
                      <span className="w12-bar"><i style={{ '--p': row.state === 'error' ? 1 : row.percent / 100, background: row.state === 'error' ? 'var(--w12-warn)' : undefined } as React.CSSProperties} /></span>
                      <span className="w12-num" style={row.state === 'error' ? { color: 'var(--w12-warn)' } : undefined}>
                        {row.state === 'done' ? t('wizard.sources.queueDone') : row.state === 'error' ? t('wizard.sources.queueError') : `${row.percent}%`}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {error && <p role="alert" className="w12-miss">{error}</p>}
              {sources.isError && <p role="alert" className="w12-miss">{t('wizard.sources.listFailed')}</p>}

              {unused.length > 0 && (
                <div className="w12-m-col" style={{ gap: 6 }}>
                  <span className="w12-m-hint" style={{ margin: 0 }}>{t('wizard.sources.unused')}</span>
                  {unused.map((source) => (
                    <div key={source.id} className="w12-clip">
                      {source.localUrl ? <video src={thumbUrl(source)} muted playsInline preload="metadata" className="w12-th" /> : <span className="w12-th" />}
                      <button type="button" className="w12-nm" style={{ textAlign: 'left' }} onClick={() => append(source)}>+ {source.name} <span className="w12-num">· {source.format} · {seconds(source.duration)}</span></button>
                      {confirmDelete === source.id ? (
                        <>
                          <button
                            type="button"
                            className="w12-split w12-split-warn"
                            onClick={async () => { setConfirmDelete(null); try { await api.deleteSource(source.id); await sources.refetch(); } catch (e) { report(e); } }}
                          >
                            <span className="w12-l">{t('wizard.sources.deleteConfirm')}</span>
                          </button>
                          <button type="button" className="w12-split" onClick={() => setConfirmDelete(null)}><span className="w12-l">{t('wizard.sources.deleteCancel')}</span></button>
                        </>
                      ) : mini(t('wizard.sources.delete'), <Svg>{W12.close}</Svg>, () => setConfirmDelete(source.id))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* правая колонка — что из этого станет роликами в Пуле */}
            <div className="w12-m-col">
              <h3>{t('wizard.sources.videoPlans')}</h3>
              <p className="w12-m-hint">{t('wizard.sources.videoPlansHint')}</p>
              <div className="w12-m-col" style={{ gap: 10 }}>
                {plans.map((plan, planIndex) => {
                  const total = totalDuration(plan);
                  const short = requiredDuration > total;
                  const select = () => { setActiveId(plan.id); setFormat(plan.format); };
                  return (
                    <div
                      key={plan.id}
                      className="w12-plan"
                      style={active?.id === plan.id ? { borderColor: 'var(--w12-accent-line)' } : undefined}
                      // клик по карточке — для мыши; с клавиатуры ролик выбирает кнопка-заголовок
                      // (role=button на всю карточку нельзя: внутри свои кнопки)
                      onClick={select}
                    >
                      <div className="w12-plan-head">
                        <button type="button" className="w12-plan-pick" aria-pressed={active?.id === plan.id} onClick={(event) => { event.stopPropagation(); select(); }}>
                          {t('wizard.sources.videoTitle', { n: planIndex + 1 })} · {plan.format}
                        </button>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          <span className={cn('w12-tot w12-num', short && 'w12-short')}>
                            {short ? t('wizard.sources.tooShort', { selected: total.toFixed(1), required: requiredDuration.toFixed(1) }) : t('wizard.sources.total', { seconds: total.toFixed(1) })}
                          </span>
                          {mini(t('wizard.sources.up'), <Svg style={vert}>{W12.left}</Svg>, () => movePlan(planIndex, -1), planIndex === 0)}
                          {mini(t('wizard.sources.down'), <Svg style={vert}>{W12.right}</Svg>, () => movePlan(planIndex, 1), planIndex === plans.length - 1)}
                        </span>
                      </div>
                      {plan.sourceIds.map((sourceId, index) => {
                        const source = sourceById(sourceId);
                        if (!source) return null;
                        const swap = (to: number) => { const ids = [...plan.sourceIds]; [ids[to], ids[index]] = [ids[index], ids[to]]; updatePlan(plan.id, ids); };
                        return (
                          <div
                            key={sourceId}
                            className="w12-clip"
                            draggable
                            onDragStart={() => setDrag({ planId: plan.id, sourceId })}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={(e) => {
                              e.preventDefault(); if (!drag || drag.planId !== plan.id || drag.sourceId === sourceId) return;
                              const ids = plan.sourceIds.filter((id) => id !== drag.sourceId); ids.splice(index, 0, drag.sourceId); updatePlan(plan.id, ids); setDrag(undefined);
                            }}
                          >
                            {source.localUrl ? <video src={thumbUrl(source)} muted playsInline preload="metadata" className="w12-th" /> : <span className="w12-th" />}
                            <span className="w12-nm">{source.name} <span className="w12-num">· {seconds(source.duration)}</span></span>
                            {mini(t('wizard.sources.up'), <Svg style={vert}>{W12.left}</Svg>, () => swap(index - 1), index === 0)}
                            {mini(t('wizard.sources.down'), <Svg style={vert}>{W12.right}</Svg>, () => swap(index + 1), index === plan.sourceIds.length - 1)}
                            {plan.sourceIds.length > 1 && <button type="button" className="w12-split" onClick={(event) => { event.stopPropagation(); split(plan, sourceId); }}><span className="w12-l">{t('wizard.sources.split')}</span></button>}
                            {mini(t('wizard.sources.remove'), <Svg>{W12.close}</Svg>, () => updatePlan(plan.id, plan.sourceIds.filter((id) => id !== sourceId)))}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
                {!plans.length && <div className="w12-empty-plan">{t('wizard.sources.noPlans')}</div>}
              </div>
            </div>
          </div>
          {/* итог: сколько роликов реально уедет в Пул — ответ на «куда попали мои футажи» */}
          <footer className="w12-m-foot">
            <span>{t('wizard.sources.summary', { count: plans.length })}</span>
            <button type="button" className="w12-cta w12-ready" onClick={onClose}><span className="w12-l">{t('wizard.sources.done')}</span></button>
          </footer>
        </section>
      </div>
    </div>,
    document.body
  );
}
