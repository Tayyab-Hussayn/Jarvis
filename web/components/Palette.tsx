"use client";

/**
 * Command palette — Ctrl/Cmd+K, fuzzy over all 119 skills, with an argument
 * form built from each skill's JSON Schema.
 *
 * Hand-rolled rather than pulled from a kit, but it does implement the parts
 * that matter: focus moves into the input on open and returns to the trigger
 * on close, Escape and backdrop both dismiss, arrows move a roving selection,
 * and focus is trapped inside the dialog while it is open.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { SkillInfo } from "@/lib/protocol";
import { Button, DangerBadge, Empty } from "./ui";

interface SchemaProperty {
  type?: string;
  description?: string;
}

/** Subsequence match — "lsw" finds "list_windows". */
function fuzzyScore(needle: string, haystack: string): number {
  if (!needle) return 1;
  let index = 0;
  let score = 0;
  for (const char of haystack) {
    if (index < needle.length && char === needle[index]) {
      index += 1;
      score += 1;
    }
  }
  return index === needle.length ? score / haystack.length : 0;
}

export function Palette({
  open,
  skills,
  onClose,
  onRun,
}: {
  open: boolean;
  skills: SkillInfo[];
  onClose: () => void;
  onRun: (skill: string, args: Record<string, unknown>) => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [chosen, setChosen] = useState<SkillInfo | null>(null);
  const [args, setArgs] = useState<Record<string, string>>({});

  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return skills
      .map((skill) => ({
        skill,
        score:
          fuzzyScore(needle, skill.name) * 2 +
          fuzzyScore(needle, skill.description.toLowerCase()),
      }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 40)
      .map((row) => row.skill);
  }, [skills, query]);

  const reset = useCallback(() => {
    setQuery("");
    setCursor(0);
    setChosen(null);
    setArgs({});
  }, []);

  const close = useCallback(() => {
    reset();
    onClose();
    returnFocusRef.current?.focus();
  }, [onClose, reset]);

  useEffect(() => {
    if (open) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
      // Focus after paint, or the browser may not have rendered the input yet.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  if (!open) return null;

  const properties: Record<string, SchemaProperty> =
    (chosen?.parameters?.properties as Record<string, SchemaProperty>) ?? {};
  const required: string[] = (chosen?.parameters?.required as string[]) ?? [];
  const missing = required.filter((key) => !args[key]?.trim());

  /** Coerce form strings back to the types the schema declares. */
  const buildArgs = () => {
    const out: Record<string, unknown> = {};
    for (const [key, raw] of Object.entries(args)) {
      if (!raw.trim()) continue;
      const type = properties[key]?.type;
      if (type === "integer" || type === "number") {
        const parsed = Number(raw);
        out[key] = Number.isNaN(parsed) ? raw : parsed;
      } else if (type === "boolean") {
        out[key] = raw === "true";
      } else if (type === "array" || type === "object") {
        try {
          out[key] = JSON.parse(raw);
        } catch {
          out[key] = raw;
        }
      } else {
        out[key] = raw;
      }
    }
    return out;
  };

  const submit = () => {
    if (!chosen || missing.length > 0) return;
    onRun(chosen.name, buildArgs());
    close();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (chosen) {
        setChosen(null);
        setArgs({});
      } else {
        close();
      }
      return;
    }

    // Trap focus: Tab must not escape to the page behind the dialog.
    if (event.key === "Tab") {
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
        'input, button, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }

    if (chosen) {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const picked = matches[cursor];
      if (!picked) return;
      const props = (picked.parameters?.properties ?? {}) as Record<string, unknown>;
      if (Object.keys(props).length === 0) {
        onRun(picked.name, {});
        close();
      } else {
        setChosen(picked);
      }
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: "rgba(0,0,0,0.55)" }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Run a skill"
        onKeyDown={onKeyDown}
        className="w-full max-w-xl overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--surface)]"
        style={{ boxShadow: "var(--shadow-panel)" }}
      >
        {!chosen ? (
          <>
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Run a skill…"
              className="w-full border-b border-[var(--border)] bg-transparent px-4 py-3.5 text-[length:var(--text-base)] outline-none placeholder:text-[var(--text-faint)]"
            />
            <div className="max-h-80 overflow-y-auto py-1">
              {matches.length === 0 ? (
                <Empty>No skill matches “{query}”.</Empty>
              ) : (
                matches.map((skill, index) => (
                  <button
                    key={skill.name}
                    type="button"
                    onMouseEnter={() => setCursor(index)}
                    onClick={() => {
                      const props = (skill.parameters?.properties ?? {}) as Record<
                        string,
                        unknown
                      >;
                      if (Object.keys(props).length === 0) {
                        onRun(skill.name, {});
                        close();
                      } else {
                        setChosen(skill);
                      }
                    }}
                    className="flex w-full items-baseline gap-2 px-4 py-2 text-left"
                    style={{
                      background: index === cursor ? "var(--accent-dim)" : "transparent",
                    }}
                  >
                    <span
                      className="shrink-0 font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]"
                      style={{
                        color: skill.available ? "var(--text)" : "var(--text-faint)",
                      }}
                    >
                      {skill.name}
                    </span>
                    {skill.danger !== "SAFE" && <DangerBadge danger={skill.danger} />}
                    <span className="min-w-0 flex-1 truncate text-[length:var(--text-xs)] text-[var(--text-muted)]">
                      {skill.available
                        ? skill.description
                        : skill.unavailable_reason || "unavailable"}
                    </span>
                  </button>
                ))
              )}
            </div>
            <div className="border-t border-[var(--border)] px-4 py-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              ↑↓ to move · Enter to run · Esc to close
            </div>
          </>
        ) : (
          <div className="p-4">
            <div className="mb-1 flex items-center gap-2">
              <span className="font-[family-name:var(--font-mono)] text-[length:var(--text-base)]">
                {chosen.name}
              </span>
              {chosen.danger !== "SAFE" && <DangerBadge danger={chosen.danger} />}
            </div>
            <p className="mb-4 text-[length:var(--text-sm)] text-[var(--text-muted)]">
              {chosen.description}
            </p>

            <div className="space-y-3">
              {Object.entries(properties).map(([key, schema]) => (
                <label key={key} className="block">
                  <div className="mb-1 flex items-baseline gap-2">
                    <span className="font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
                      {key}
                    </span>
                    {required.includes(key) && (
                      <span
                        className="text-[length:var(--text-xs)]"
                        style={{ color: "var(--destructive)" }}
                      >
                        required
                      </span>
                    )}
                    <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                      {schema.type}
                    </span>
                  </div>
                  {schema.type === "boolean" ? (
                    <select
                      value={args[key] ?? ""}
                      onChange={(event) =>
                        setArgs((current) => ({ ...current, [key]: event.target.value }))
                      }
                      className="w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[length:var(--text-sm)] outline-none"
                    >
                      <option value="">default</option>
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : (
                    <input
                      value={args[key] ?? ""}
                      onChange={(event) =>
                        setArgs((current) => ({ ...current, [key]: event.target.value }))
                      }
                      placeholder={schema.description ?? ""}
                      className="w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[length:var(--text-sm)] outline-none focus:border-[var(--accent)]"
                    />
                  )}
                </label>
              ))}
            </div>

            <div className="mt-4 flex items-center gap-2">
              <Button
                variant="primary"
                disabled={missing.length > 0}
                onClick={submit}
              >
                Run
              </Button>
              <Button
                onClick={() => {
                  setChosen(null);
                  setArgs({});
                }}
              >
                Back
              </Button>
              {missing.length > 0 && (
                <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  needs {missing.join(", ")}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
