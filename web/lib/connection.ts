import type { Message } from "./protocol";

export const LOST_RESULT =
  "Connection lost — result unknown. It may have run; check the device or the activity log.";

/**
 * Resolve everything a dead socket can no longer finish. The server may well
 * have completed a tool and sent `tool_result` into a socket that was already
 * gone, and a new socket is a new session with no replay — so a card left
 * "running" would stay that way forever. It is marked `lost` rather than
 * failed, because the call may have run. A pending confirmation is denied
 * (the server denies on a dropped socket, so "denied" is what really happened).
 * Settled messages come back as the same object.
 */
export function settleOnClose(messages: Message[]): Message[] {
  return messages.map((message) => {
    const openTool = message.tools?.some((tool) => !tool.done);
    const openConfirm = message.confirms?.some((c) => c.status === "pending");
    if (!message.pending && !openTool && !openConfirm) return message;
    return {
      ...message,
      pending: false,
      tools: message.tools?.map((tool) =>
        tool.done ? tool : { ...tool, done: true, lost: true, result: LOST_RESULT },
      ),
      confirms: message.confirms?.map((c) =>
        c.status === "pending" ? { ...c, status: "denied" as const } : c,
      ),
      // Only a turn that was waiting on a reply gets the note.
      interrupted: message.pending ? true : message.interrupted,
    };
  });
}

export const CLOSE_POLICY_VIOLATION = 1008;
/** Another socket resumed this session (a second tab); do not fight for it. */
export const CLOSE_REPLACED = 4001;

export interface RetryPlan {
  /** What the connection badge shows while waiting. */
  state: "reconnecting" | "offline" | "unauthorized" | "replaced";
  /** Ms until the next attempt; null = do not retry. */
  delay: number | null;
  attempts: number;
}

/**
 * Reconnect state machine. It keeps retrying at the capped delay even when
 * "offline" is shown, so a phone that comes back from the background recovers
 * the composer without a manual Reload. Only a rejected credential stops it.
 */
export function planRetry(
  attempts: number,
  closeCode: number,
  base = 500,
  max = 15_000,
): RetryPlan {
  if (closeCode === CLOSE_REPLACED) {
    return { state: "replaced", delay: null, attempts };
  }
  if (closeCode === CLOSE_POLICY_VIOLATION) {
    return { state: "unauthorized", delay: null, attempts };
  }
  const next = attempts + 1;
  return {
    state: next > 3 ? "offline" : "reconnecting",
    delay: Math.min(base * 2 ** (next - 1), max),
    attempts: next,
  };
}
