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

/** A navigation or UI action offered above the skills: open a module, an agent. */
export interface PaletteCommand {
  id: string;
  label: string;
  hint: string;
  run: () => void;
}

type Entry =
  | { kind: "command"; command: PaletteCommand }
  | { kind: "skill"; skill: SkillInfo };

export function Palette({
  open,
  skills,
  commands = [],
  onClose,
  onRun,
}: {
  open: boolean;
  skills: SkillInfo[];
  commands?: PaletteCommand[];
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

  const matches = useMemo<Entry[]>(() => {
    const needle = query.trim().toLowerCase();
    const cmds: Entry[] = commands
      .map((command) => ({
        command,
        score:
          fuzzyScore(needle, command.label.toLowerCase()) * 2 +
          fuzzyScore(needle, command.hint.toLowerCase()),
      }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((row) => ({ kind: "command" as const, command: row.command }));
    const found: Entry[] = skills
      .map((skill) => ({
        skill,
        score:
          fuzzyScore(needle, skill.name) * 2 +
          fuzzyScore(needle, skill.description.toLowerCase()),
      }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((row) => ({ kind: "skill" as const, skill: row.skill }));
    // Commands first: opening a module is the common case and the list is short.
    return [...cmds, ...found].slice(0, 40);
  }, [skills, commands, query]);

  /** Run an entry: commands act at once, skills open their argument form. */
  const pick = (entry: Entry) => {
    if (entry.kind === "command") {
      entry.command.run();
      close();
      return;
    }
    const props = (entry.skill.parameters?.properties ?? {}) as Record<string, unknown>;
    if (Object.keys(props).length === 0) {
      onRun(entry.skill.name, {});
      close();
    } else {
      setChosen(entry.skill);
    }
  };

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
      if (picked) pick(picked);
    }
  };

  return (
    <div
      className="fixed inset-0 flex items-start justify-center px-4 pt-[12vh]"
      style={{ background: "var(--scrim)", zIndex: "var(--z-palette)" as unknown as number }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onKeyDown={onKeyDown}
        className="glass glass-strong w-full max-w-xl overflow-hidden rounded-[var(--radius-lg)]"
        style={{ boxShadow: "var(--shadow-panel), var(--glow-soft)", animation: "node-in var(--motion-base) var(--ease-out)" }}
      >
        {!chosen ? (
          <>
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Open a module, an agent, or run a skill…"
              className="w-full border-b border-[var(--border)] bg-transparent px-4 py-3.5 text-[length:var(--text-base)] outline-none placeholder:text-[var(--text-faint)]"
            />
            <div className="max-h-80 overflow-y-auto py-1">
              {matches.length === 0 ? (
                <Empty>Nothing matches “{query}”.</Empty>
              ) : (
                matches.map((entry, index) => {
                  const isCommand = entry.kind === "command";
                  const name = isCommand ? entry.command.label : entry.skill.name;
                  return (
                    <button
                      key={isCommand ? `cmd-${entry.command.id}` : entry.skill.name}
                      type="button"
                      onMouseEnter={() => setCursor(index)}
                      onClick={() => pick(entry)}
                      className="flex w-full items-baseline gap-2 px-4 py-2 text-left"
                      style={{
                        background: index === cursor ? "var(--accent-dim)" : "transparent",
                      }}
                    >
                      <span
                        className={
                          isCommand
                            ? "shrink-0 text-[length:var(--text-sm)] font-semibold"
                            : "shrink-0 font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]"
                        }
                        style={{
                          color:
                            !isCommand && !entry.skill.available
                              ? "var(--text-faint)"
                              : "var(--text)",
                        }}
                      >
                        {name}
                      </span>
                      {isCommand && (
                        <span className="eyebrow shrink-0">Go</span>
                      )}
                      {!isCommand && entry.skill.danger !== "SAFE" && (
                        <DangerBadge danger={entry.skill.danger} />
                      )}
                      <span className="min-w-0 flex-1 truncate text-[length:var(--text-xs)] text-[var(--text-muted)]">
                        {isCommand
                          ? entry.command.hint
                          : entry.skill.available
                            ? entry.skill.description
                            : entry.skill.unavailable_reason || "unavailable"}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
            <div className="border-t border-[var(--border)] px-4 py-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              ↑↓ to move · Enter to open or run · Esc to close
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
