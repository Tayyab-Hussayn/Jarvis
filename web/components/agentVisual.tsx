"use client";

/**
 * How an agent state LOOKS, in one place.
 *
 * Every state differs by shape and label as well as colour (the brief: never
 * colour alone), and every state still reads in reduced motion, where all
 * loops are stopped by the global rule: the glyph, the badge and the text
 * label carry the meaning, the animation only adds life.
 *
 * All motion is CSS (transform/opacity/stroke-dashoffset); nothing here
 * re-renders per frame.
 */

import { STATE_LABEL, type AgentStateName } from "@/lib/agents";

export const STATE_COLOUR: Record<AgentStateName, string> = {
  idle: "var(--state-idle)",
  thinking: "var(--state-thinking)",
  working: "var(--state-working)",
  waiting: "var(--state-waiting)",
  done: "var(--state-done)",
  failed: "var(--state-failed)",
  declined: "var(--state-failed)",
};

/** Text-safe colour for a label next to a state (>= 4.5:1, see check_contrast). */
export const STATE_TEXT: Record<AgentStateName, string> = {
  idle: "var(--text-muted)",
  thinking: "var(--text)",
  working: "var(--state-working-text)",
  waiting: "var(--coral)",
  done: "var(--state-done)",
  failed: "var(--state-failed)",
  declined: "var(--state-failed)",
};

/** A small, distinct mark per state, drawn on a 24-unit grid. */
export function StateGlyph({
  state,
  size = 20,
}: {
  state: AgentStateName;
  size?: number;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (state) {
    case "idle":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
        </svg>
      );
    case "thinking":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="18" cy="12" r="1.5" fill="currentColor" stroke="none" />
        </svg>
      );
    case "working":
      return (
        <svg {...common}>
          <path d="M13 3 5 13.5h6L10 21l9-11h-6z" fill="currentColor" />
        </svg>
      );
    case "waiting":
      return (
        <svg {...common}>
          <path d="M12 6v7" />
          <circle cx="12" cy="17.5" r="0.6" fill="currentColor" />
        </svg>
      );
    case "done":
      return (
        <svg {...common}>
          <path d="m6 12.5 4 4 8-9" />
        </svg>
      );
    case "failed":
      return (
        <svg {...common}>
          <path d="m7 7 10 10M17 7 7 17" />
        </svg>
      );
    case "declined":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="7" />
          <path d="m7.2 16.8 9.6-9.6" />
        </svg>
      );
  }
}

/**
 * The ring around a node. A base circle always; the animated arc differs by
 * state (steel orbit when thinking, laser sweep when working, dashed when
 * waiting). The arc's speed comes from --loop-* tokens.
 */
export function StateRing({
  state,
  size,
}: {
  state: AgentStateName;
  size: number | string;
}) {
  const colour = STATE_COLOUR[state];
  const r = 28;
  const circ = 2 * Math.PI * r;
  const arc =
    state === "working"
      ? { len: circ * 0.38, anim: "spin var(--loop-sweep) linear infinite", width: 3 }
      : state === "thinking"
        ? { len: circ * 0.16, anim: "spin var(--loop-orbit) linear infinite", width: 2.4 }
        : null;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className="pointer-events-none absolute inset-0"
      aria-hidden
    >
      <circle
        cx="32"
        cy="32"
        r={r}
        fill="none"
        stroke={colour}
        strokeWidth={state === "idle" ? 1.4 : 2}
        strokeOpacity={state === "idle" ? 0.55 : 0.9}
        // Waiting is dashed and failed/declined doubled: a second cue besides
        // colour for anyone who cannot tell coral from red.
        strokeDasharray={state === "waiting" ? "4 5" : undefined}
      />
      {(state === "failed" || state === "declined") && (
        <circle cx="32" cy="32" r={r - 4} fill="none" stroke={colour} strokeWidth="1" strokeOpacity="0.6" />
      )}
      {arc && (
        <g style={{ transformOrigin: "32px 32px", animation: arc.anim }}>
          <circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke={colour}
            strokeWidth={arc.width}
            strokeLinecap="round"
            strokeDasharray={`${arc.len} ${circ}`}
            style={{ filter: state === "working" ? "drop-shadow(0 0 4px var(--laser))" : undefined }}
          />
        </g>
      )}
    </svg>
  );
}

/** The word for a state, as a pill, so state is never colour alone. */
export function StatePill({
  state,
  compact = false,
}: {
  state: AgentStateName;
  compact?: boolean;
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-[var(--radius-full)] border px-2 py-0.5 text-[length:var(--text-xs)] font-medium"
      style={{
        color: STATE_TEXT[state],
        borderColor: "color-mix(in srgb, currentColor 40%, transparent)",
      }}
    >
      <StateGlyph state={state} size={compact ? 11 : 13} />
      {STATE_LABEL[state]}
    </span>
  );
}
