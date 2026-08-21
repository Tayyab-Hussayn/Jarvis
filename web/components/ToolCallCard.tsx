"use client";

/**
 * One skill invocation, shown inline as it runs.
 *
 * This is what makes a 119-skill agent legible instead of a black box: you see
 * the name, the exact arguments and the raw result, rather than trusting the
 * model's prose summary of what it did. Collapsed by default so a ten-call
 * turn stays readable; the toggle persists per session.
 */

import { useState } from "react";

import type { ToolCallView } from "@/lib/protocol";
import { previewArgs } from "@/lib/format";

const EXPAND_KEY = "brahma.expandTools";

export function useExpandTools(): [boolean, (value: boolean) => void] {
  const [expand, setExpand] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return sessionStorage.getItem(EXPAND_KEY) === "1";
  });
  return [
    expand,
    (value: boolean) => {
      setExpand(value);
      if (typeof window !== "undefined") {
        sessionStorage.setItem(EXPAND_KEY, value ? "1" : "0");
      }
    },
  ];
}

export function ToolCallCard({
  tool,
  defaultOpen,
}: {
  tool: ToolCallView;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const args = previewArgs(tool.args);

  return (
    <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-sunken)]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: tool.done ? "var(--safe)" : "var(--accent)",
            animation: tool.done ? undefined : "pulse 1.2s ease-in-out infinite",
          }}
        />
        <span className="font-[family-name:var(--font-mono)] text-[var(--text-sm)]">
          {tool.name}
        </span>
        {args && (
          <span className="min-w-0 flex-1 truncate font-[family-name:var(--font-mono)] text-[var(--text-xs)] text-[var(--text-faint)]">
            {args}
          </span>
        )}
        <span className="ml-auto shrink-0 text-[var(--text-xs)] text-[var(--text-faint)]">
          {tool.done ? (open ? "hide" : "show") : "running…"}
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--border)] px-3 py-2">
          {Object.keys(tool.args).length > 0 && (
            <>
              <div className="text-[var(--text-xs)] uppercase tracking-wide text-[var(--text-faint)]">
                Arguments
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[var(--text-xs)] text-[var(--text-muted)]">
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </>
          )}
          {tool.done && (
            <>
              <div className="mt-2 text-[var(--text-xs)] uppercase tracking-wide text-[var(--text-faint)]">
                Result
              </div>
              <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                {tool.result || "(empty)"}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
