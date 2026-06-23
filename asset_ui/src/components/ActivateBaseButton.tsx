import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchActivateStatus, startActivate, type ActivationStatus } from '../api';

const POLL_MS = 3000;
const STALE_S = 180;

const PHASE_RU: Record<string, string> = {
  starting: 'старт',
  indexing: 'индекс',
  inventory: 'инвентарь',
  tagging: 'разметка',
  snapshot: 'снапшот',
};

/** Toolbar control: run the full ingest (rebuild index -> inventory -> tag ->
 *  snapshot) so freshly uploaded clips enter the picker pool. Shows phase +
 *  progress; re-enables on stale state. */
export function ActivateBaseButton({ onDone }: { onDone?: () => void }) {
  const [status, setStatus] = useState<ActivationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef<number | null>(null);
  const wasRunning = useRef(false);

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    try {
      const s = await fetchActivateStatus();
      setStatus(s);
      const active = s.state === 'running' || s.state === 'queued';
      if (active) {
        wasRunning.current = true;
      } else if (wasRunning.current && (s.state === 'done' || s.state === 'failed')) {
        wasRunning.current = false;
        stopPolling();
        if (s.state === 'done') onDone?.();
      }
    } catch {
      /* transient */
    }
  }, [onDone, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    void poll();
    timer.current = window.setInterval(poll, POLL_MS);
  }, [poll, stopPolling]);

  useEffect(() => {
    void poll();
    return stopPolling;
  }, [poll, stopPolling]);

  const handleStart = useCallback(async () => {
    setError(null);
    setStarting(true);
    try {
      await startActivate(0);
      wasRunning.current = true;
      startPolling();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }, [startPolling]);

  const active = status?.state === 'running' || status?.state === 'queued';
  const fresh = status?.updated_at ? Date.now() / 1000 - status.updated_at < STALE_S : false;
  const running = active && fresh;
  const phase = status?.phase ? PHASE_RU[status.phase] ?? status.phase : '';
  const done = status?.done ?? 0;
  const total = status?.total ?? 0;

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <button className="toolbar-btn" onClick={handleStart} disabled={running || starting}
        title="Пересобрать базу: индекс → инвентарь → разметка → снапшот. Новые клипы попадут в подбор.">
        {starting ? 'Запускаю…' : '⚙️ Активировать базу'}
      </button>
      {running && (
        <span className="toolbar-counter">
          {phase}{total > 0 ? ` ${done}/${total}` : '…'}
        </span>
      )}
      {!running && status?.state === 'done' && (
        <span className="toolbar-counter">✓ активировано (индекс: {status.indexed ?? '—'}, размечено: {status.written ?? 0})</span>
      )}
      {(error || status?.state === 'failed') && (
        <span className="toolbar-counter" style={{ color: '#e06060' }}>
          ⚠ {error || status?.error || 'ошибка'}
        </span>
      )}
    </span>
  );
}
