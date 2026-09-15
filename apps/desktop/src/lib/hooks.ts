import { useCallback, useEffect, useRef, useState } from "react";
import { call } from "./ipc.ts";
import { isActive } from "./format.ts";
import type { Job } from "./types.ts";

export function useService<T>(command: string | null, payload: Record<string, unknown> = {}, pollMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const key = JSON.stringify(payload);
  const reload = useCallback(async () => {
    if (!command) return;
    setLoading(true);
    try {
      setData(await call<T>(command, JSON.parse(key)));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }, [command, key]);

  useEffect(() => {
    setData(null);
    void reload();
    if (!pollMs) return;
    const timer = window.setInterval(() => void reload(), pollMs);
    return () => window.clearInterval(timer);
  }, [reload, pollMs]);

  return { data, error, loading, loaded, reload, setData };
}

/** Follows one job through durable backend events until it reaches a terminal state. */
export function useJob(jobId: string | null, onDone?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    setJob(null);
    if (!jobId) return;
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await call<Job>("job.get", { job_id: jobId });
        if (cancelled) return;
        setJob(next);
        if (isActive(next.state)) {
          timer = window.setTimeout(tick, 600);
        } else {
          done.current?.(next);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(tick, 1500);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  return job;
}

export function useKeyboardShortcuts(handlers: Record<string, () => void>, enabled: boolean) {
  const ref = useRef(handlers);
  ref.current = handlers;
  useEffect(() => {
    if (!enabled) return;
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey || target?.closest("input, textarea, select, [contenteditable]")) return;
      const handler = ref.current[event.key.toLowerCase()];
      if (handler) {
        event.preventDefault();
        handler();
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [enabled]);
}
