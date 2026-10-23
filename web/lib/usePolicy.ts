"use client";

/**
 * The confirmation policy as the server last confirmed it.
 *
 * State only ever comes from a well-formed server answer: there is no
 * optimistic write, so the selector cannot show a mode the server did not
 * confirm. A light poll keeps a change made in another tab, an expiry, or a
 * restart from going unnoticed; the server clock decides, never the browser's.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { parsePolicy, type Anchored, type PolicyMode } from "./policy";
import { api } from "./session";

const POLL_MS = 5000;

export interface PolicyState {
  anchored: Anchored | null;
  /** Last failure, empty when the last request worked. */
  error: string;
  /** A change is in flight; the controls disable instead of spinning forever. */
  busy: boolean;
  refresh: () => Promise<void>;
  set: (mode: PolicyMode, seconds?: number) => Promise<boolean>;
  turnOff: () => Promise<boolean>;
}

export function usePolicy(): PolicyState {
  const [anchored, setAnchored] = useState<Anchored | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Only the newest answer may land: a slow poll must not overwrite the
  // result of a change made after it started.
  const ticket = useRef(0);
  // A poll that starts mid-change could be answered before the change is
  // applied and then overwrite it with the old mode, so polls wait.
  const changing = useRef(false);

  // A change's own answer always lands (it is the newest fact about what
  // the owner just did); only polls can be superseded.
  const accept = useCallback((raw: unknown, mine: number, force = false): boolean => {
    if (!force && mine !== ticket.current) return true;
    const policy = parsePolicy(raw);
    if (!policy) {
      setError("Unexpected answer from the server");
      return false;
    }
    setAnchored({ policy, at: Date.now() });
    setError("");
    return true;
  }, []);

  const refresh = useCallback(async () => {
    if (changing.current) return;
    const mine = ++ticket.current;
    try {
      accept(await api<unknown>("/api/safety/policy"), mine);
    } catch (exc) {
      if (mine === ticket.current) setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [accept]);

  const change = useCallback(
    async (path: string, body?: unknown): Promise<boolean> => {
      const mine = ++ticket.current;
      changing.current = true;
      setBusy(true);
      try {
        const raw = await api<unknown>(path, {
          method: "POST",
          ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        });
        return accept(raw, mine, true);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
        return false;
      } finally {
        changing.current = false;
        setBusy(false);
      }
    },
    [accept],
  );

  const set = useCallback(
    (mode: PolicyMode, seconds?: number) =>
      change("/api/safety/policy", seconds === undefined ? { mode } : { mode, seconds }),
    [change],
  );
  const turnOff = useCallback(() => change("/api/safety/auto/off"), [change]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  return { anchored, error, busy, refresh, set, turnOff };
}
