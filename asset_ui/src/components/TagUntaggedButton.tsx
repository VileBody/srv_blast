import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTagUntaggedStatus, startTagUntagged, type TaggingStatus } from '../api';

const POLL_MS = 3000;

/** Toolbar control: start server-side Groq auto-tagging of untagged clips with
 *  an optional limit, and show a minimal progress bar (tagged / total). */
export function TagUntaggedButton({ onDone }: { onDone?: () => void }) {
  const [status, setStatus] = useState<TaggingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState<number>(0);
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
      const s = await fetchTagUntaggedStatus();
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
      /* transient — keep polling */
    }
  }, [onDone, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    void poll();
    timer.current = window.setInterval(poll, POLL_MS);
  }, [poll, stopPolling]);

  // On mount, surface an already-running batch (e.g. page reload mid-run).
  useEffect(() => {
    void poll();
    return stopPolling;
  }, [poll, stopPolling]);

  const handleStart = useCallback(async () => {
    setError(null);
    try {
      await startTagUntagged(limit);
      wasRunning.current = true;
      startPolling();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [limit, startPolling]);

  const running = status?.state === 'running' || status?.state === 'queued';
  const done = status?.done ?? 0;
  const total = status?.total ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <span className="tag-untagged" style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <input
        type="number"
        min={0}
        value={limit || ''}
        placeholder="все"
        title="Лимит клипов за прогон (0 / пусто = все)"
        disabled={running}
        onChange={(e) => setLimit(Math.max(0, Number(e.target.value) || 0))}
        style={{ width: 56, padding: '4px 6px' }}
      />
      <button className="toolbar-btn" onClick={handleStart} disabled={running}>
        🏷 Разметить
      </button>

      {running && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              display: 'inline-block', width: 120, height: 8, borderRadius: 4,
              background: 'rgba(127,127,127,0.25)', overflow: 'hidden',
            }}
          >
            <span
              style={{
                display: 'block', height: '100%', width: `${pct}%`,
                background: '#4a9eff', transition: 'width 0.3s',
              }}
            />
          </span>
          <span className="toolbar-counter">размечено {done} / {total}</span>
        </span>
      )}

      {!running && status?.state === 'done' && status.written != null && (
        <span className="toolbar-counter">✓ размечено: {status.written}</span>
      )}
      {(error || status?.state === 'failed') && (
        <span className="toolbar-counter" style={{ color: '#e06060' }}>
          ⚠ {error || status?.error || 'ошибка'}
        </span>
      )}
    </span>
  );
}
