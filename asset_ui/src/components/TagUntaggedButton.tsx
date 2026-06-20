import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTagUntaggedStatus, startTagUntagged, type TaggingStatus } from '../api';

const POLL_MS = 3000;

/** Toolbar control: start server-side Groq auto-tagging of untagged clips and
 *  poll progress. Resumes showing progress if a run is already in flight. */
export function TagUntaggedButton({ onDone }: { onDone?: () => void }) {
  const [status, setStatus] = useState<TaggingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
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
      await startTagUntagged(0);
      wasRunning.current = true;
      startPolling();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [startPolling]);

  const running = status?.state === 'running' || status?.state === 'queued';
  const label = running
    ? `Разметка… ${status?.done ?? 0}/${status?.total ?? '?'}`
    : '🏷 Разметить без тегов';

  return (
    <>
      <button className="toolbar-btn" onClick={handleStart} disabled={running}>
        {label}
      </button>
      {status?.state === 'done' && status.written != null && (
        <span className="toolbar-counter">✓ размечено: {status.written}</span>
      )}
      {(error || status?.state === 'failed') && (
        <span className="toolbar-counter" style={{ color: '#e06060' }}>
          ⚠ {error || status?.error || 'ошибка'}
        </span>
      )}
    </>
  );
}
