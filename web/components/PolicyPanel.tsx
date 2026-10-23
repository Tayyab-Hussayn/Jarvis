"use client";

/**
 * Settings: how Kravein handles actions that need your yes or no.
 *
 * The highlighted mode is always the one the server last confirmed. Picking
 * a mode sends a request and waits; nothing is shown as selected until the
 * answer arrives, and a failure is printed next to the control.
 */

import { useState } from "react";

import {
  AUTO_COVERS,
  DEFAULT_DURATION,
  DURATIONS,
  MODE_TEXT,
  type PolicyMode,
} from "@/lib/policy";
import type { PolicyState } from "@/lib/usePolicy";
import { Button, Panel } from "./ui";

const ORDER: PolicyMode[] = ["ask", "decline", "auto"];

export function PolicyPanel({ policy }: { policy: PolicyState }) {
  const { anchored, error, busy, set } = policy;
  const [confirming, setConfirming] = useState(false);
  const [seconds, setSeconds] = useState(DEFAULT_DURATION);
  const current = anchored?.policy.mode ?? null;

  const pick = async (mode: PolicyMode) => {
    if (mode === "auto") {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    if (mode !== current) await set(mode);
  };

  const enable = async () => {
    // Leave the confirmation open on failure so the error sits beside it.
    if (await set("auto", seconds)) setConfirming(false);
  };

  return (
    <Panel title="Confirmations">
      <div className="space-y-2" role="radiogroup" aria-label="Confirmation policy">
        {ORDER.map((mode) => {
          const on = mode === current;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={busy || !anchored}
              onClick={() => void pick(mode)}
              className="block w-full rounded-[var(--radius)] border px-3 py-2 text-left disabled:opacity-60"
              style={{
                borderColor: on
                  ? mode === "auto"
                    ? "var(--critical)"
                    : "var(--accent)"
                  : "var(--border-strong)",
                background: on
                  ? mode === "auto"
                    ? "var(--critical-dim)"
                    : "var(--accent-dim)"
                  : "transparent",
              }}
            >
              <span className="block text-[length:var(--text-sm)] font-medium">
                {MODE_TEXT[mode].label}
              </span>
              <span className="block text-[length:var(--text-xs)] text-[var(--text-muted)]">
                {MODE_TEXT[mode].hint}
              </span>
            </button>
          );
        })}
      </div>

      {confirming && (
        <div
          className="mt-3 rounded-[var(--radius)] border-l-2 px-3 py-3"
          style={{ borderLeftColor: "var(--critical)", background: "var(--critical-dim)" }}
        >
          <p className="text-[length:var(--text-sm)] font-medium">
            Turn on auto-approve?
          </p>
          <ul className="mt-1 list-disc pl-5 text-[length:var(--text-xs)] text-[var(--text-muted)]">
            {AUTO_COVERS.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {DURATIONS.map((d) => (
              <button
                key={d.seconds}
                type="button"
                disabled={busy}
                onClick={() => setSeconds(d.seconds)}
                aria-pressed={seconds === d.seconds}
                className="rounded-[var(--radius-sm)] border px-2 py-0.5 text-[length:var(--text-xs)] disabled:opacity-50"
                style={{
                  borderColor: seconds === d.seconds ? "var(--critical)" : "var(--border-strong)",
                  color: seconds === d.seconds ? "var(--critical)" : "var(--text-muted)",
                }}
              >
                {d.label}
              </button>
            ))}
          </div>
          <div className="mt-3 flex gap-2">
            <Button variant="danger" disabled={busy} onClick={() => void enable()}>
              {busy ? "Turning on…" : "Turn on auto-approve"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-3 text-[length:var(--text-sm)]" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}
    </Panel>
  );
}
