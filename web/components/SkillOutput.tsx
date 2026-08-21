"use client";

/**
 * The result pane every panel shares.
 *
 * Skills return prose written for the model to read. Rather than parse that
 * into a fake table — which would break the moment a message is reworded — the
 * panels show it verbatim. What you see here is exactly what the assistant
 * sees, which also makes this the honest place to debug from.
 */

import type { SkillState } from "@/lib/useApi";
import { Button, Spinner } from "./ui";

export function SkillOutput({ state }: { state: SkillState }) {
  if (state.running) {
    return (
      <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-sunken)] px-3 py-2.5">
        <Spinner label={`running ${state.running}…`} />
      </div>
    );
  }

  if (state.error) {
    return (
      <div
        className="rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
        style={{
          borderLeftColor: "var(--critical)",
          background: "var(--critical-dim)",
          color: "var(--critical)",
        }}
      >
        {state.error}
      </div>
    );
  }

  if (!state.output) return null;

  return (
    <div className="relative rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-sunken)]">
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words px-3 py-2.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
        {state.output}
      </pre>
      <div className="absolute right-2 top-2">
        <Button variant="ghost" onClick={state.clear} title="Clear output">
          ✕
        </Button>
      </div>
    </div>
  );
}

/** A labelled row of skill buttons — the common panel building block. */
export function ActionRow({
  label,
  children,
}: {
  label?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      {label && (
        <div className="text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
          {label}
        </div>
      )}
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}
