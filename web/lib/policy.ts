/**
 * Pure logic for the confirmation-policy selector and the auto-approve
 * banner, free of React so `node --test` can cover it.
 *
 * The server is the only source of truth. The browser clock is used for one
 * thing: ticking the countdown down between server answers, re-anchored on
 * every answer.
 */

export type PolicyMode = "ask" | "decline" | "auto";

export interface Policy {
  mode: PolicyMode;
  seconds_left: number;
  default_seconds: number;
  max_seconds: number;
}

/** A policy as the server last said it, and when we heard it (client ms). */
export interface Anchored {
  policy: Policy;
  at: number;
}

export const DURATIONS: { seconds: number; label: string }[] = [
  { seconds: 15 * 60, label: "15 min" },
  { seconds: 60 * 60, label: "1 hour" },
  { seconds: 4 * 60 * 60, label: "4 hours" },
];
export const DEFAULT_DURATION = 60 * 60;

const MODES: PolicyMode[] = ["ask", "decline", "auto"];

/**
 * Accept only a well-formed server answer. Anything else is `null`, so the
 * selector keeps showing the last mode the server did confirm rather than
 * one invented from a malformed reply.
 */
export function parsePolicy(raw: unknown): Policy | null {
  if (raw === null || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!MODES.includes(r.mode as PolicyMode)) return null;
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const left = num(r.seconds_left);
  const dflt = num(r.default_seconds);
  const max = num(r.max_seconds);
  if (left === null || dflt === null || max === null) return null;
  return {
    mode: r.mode as PolicyMode,
    seconds_left: Math.max(0, left),
    default_seconds: dflt,
    max_seconds: max,
  };
}

/**
 * Whole seconds of auto-approve left, counted down from the last answer.
 * A tick timestamp older than the answer counts as no time elapsed, so the
 * countdown never starts above what the server said.
 */
export function secondsRemaining(a: Anchored | null, nowMs: number): number {
  if (!a || a.policy.mode !== "auto") return 0;
  return Math.max(0, Math.ceil(a.policy.seconds_left - Math.max(0, nowMs - a.at) / 1000));
}

/** The banner shows only while the server says auto and time is left. */
export function bannerVisible(a: Anchored | null, nowMs: number): boolean {
  return secondsRemaining(a, nowMs) > 0;
}

/** `mm:ss`, or `h:mm:ss` from an hour up (a 4 h grant is not "240:00"). */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(sec)}` : `${two(m)}:${two(sec)}`;
}

/** One plain sentence per mode, for the selector. */
export const MODE_TEXT: Record<PolicyMode, { label: string; hint: string }> = {
  ask: {
    label: "Ask every time",
    hint: "Risky actions wait for your yes or no. This is the normal setting.",
  },
  decline: {
    label: "Decline",
    hint: "Risky actions are refused without asking. Safe actions still run.",
  },
  auto: {
    label: "Auto-approve",
    hint: "Risky actions you ask for yourself run without asking, for a limited time.",
  },
};

/** What the in-page confirmation tells the owner before turning auto on. */
export const AUTO_COVERS: string[] = [
  "Applies only to requests you make from the web, voice or terminal.",
  "Jobs, routines and WhatsApp requests still ask.",
  "Shutdown, reboot and sudo still ask.",
  "Protected paths and other hard refusals stay blocked.",
  "Turns off by itself after the time you choose, and when Kravein restarts.",
];
