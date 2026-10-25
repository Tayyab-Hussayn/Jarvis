/**
 * Resumable sessions (#110), the pure parts.
 *
 * The server hands every socket a `session_id` in `hello`. Keeping it in
 * localStorage (not sessionStorage) is the point: closing a tab or restarting
 * the app must still find the same conversation. Two tabs sharing it take the
 * session over from each other; the older one is closed with `CLOSE_REPLACED`
 * and stops retrying instead of fighting for it.
 */

import { CLOSE_REPLACED } from "./connection.ts";
import type { ServerFrame } from "./protocol.ts";

export const SESSION_KEY = "kravein.session";
export { CLOSE_REPLACED };

/** Minimal Storage so tests do not need a DOM. */
export interface KeyValue {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function browserStorage(): KeyValue | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // blocked storage: every session is a new one
  }
}

export function loadSessionId(store: KeyValue | null = browserStorage()): string {
  try {
    return store?.getItem(SESSION_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveSessionId(id: string, store: KeyValue | null = browserStorage()): void {
  try {
    if (id) store?.setItem(SESSION_KEY, id);
    else store?.removeItem(SESSION_KEY);
  } catch {
    // a per-viewer convenience only
  }
}

/**
 * Highest turn counter among replayed ids (`t7`, `p3`, `s2-user`...). The
 * hook numbers new turns from a counter that restarts at 0 on a page load, so
 * without this a reloaded page would reuse `t1` and patch the old message.
 */
export function maxTurnSeq(frames: ServerFrame[]): number {
  let max = 0;
  for (const frame of frames) {
    const id = "id" in frame && typeof frame.id === "string" ? frame.id : "";
    const match = /^[tps](\d+)/.exec(id);
    if (match) max = Math.max(max, Number(match[1]));
  }
  return max;
}

/** The conversation a server drops after it restarts or expires a session. */
export const SESSION_LOST_NOTE =
  "This conversation was not found on the server (it expired or the server restarted), so Kravein does not remember what is above.";
