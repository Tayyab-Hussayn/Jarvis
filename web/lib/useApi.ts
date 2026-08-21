"use client";

/** Shared data-loading helpers for the panel screens. */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./session";

export interface Resource<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch a REST resource, optionally polling.
 *
 * Guards against setting state after unmount and against a slow response from
 * a previous path overwriting a newer one — both show up as "the panel shows
 * the wrong thing after you click around quickly".
 */
export function useResource<T>(path: string, pollMs = 0): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const requestRef = useRef(0);

  const load = useCallback(async () => {
    const ticket = ++requestRef.current;
    try {
      const value = await api<T>(path);
      if (ticket !== requestRef.current) return;   // superseded
      setData(value);
      setError("");
    } catch (exc) {
      if (ticket !== requestRef.current) return;
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (ticket === requestRef.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(true);
    void load();
    if (!pollMs) return;
    const timer = setInterval(load, pollMs);
    return () => clearInterval(timer);
  }, [load, pollMs]);

  useEffect(() => () => { requestRef.current += 1; }, []);

  return { data, error, loading, reload: load };
}

export type RunSkill = (skill: string, args?: Record<string, unknown>) => Promise<string>;

export interface SkillState {
  output: string;
  error: string;
  running: string;
  call: (skill: string, args?: Record<string, unknown>) => Promise<string>;
  clear: () => void;
}

/**
 * Wraps the websocket `run` action with the state a panel needs: which skill
 * is in flight, what it returned, what went wrong.
 *
 * A rejected run is surfaced, never swallowed — a panel button that silently
 * does nothing is worse than one that reports the failure.
 */
export function useSkill(run: RunSkill): SkillState {
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [running, setRunning] = useState("");

  const call = useCallback(
    async (skill: string, args: Record<string, unknown> = {}) => {
      setRunning(skill);
      setError("");
      try {
        const result = await run(skill, args);
        setOutput(result);
        return result;
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : String(exc);
        setError(message);
        return "";
      } finally {
        setRunning("");
      }
    },
    [run],
  );

  const clear = useCallback(() => {
    setOutput("");
    setError("");
  }, []);

  return { output, error, running, call, clear };
}
