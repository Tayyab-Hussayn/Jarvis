"use client";

import { useEffect, useRef, useState } from "react";

import {
  capLines, formatDuration, historyStep, newHistory, parseResult, pushHistory,
  RUN_TIMEOUT_SECONDS, type OutputLine,
} from "@/lib/console";
import type { RunSkill } from "@/lib/useApi";
import { homeOf, shortCwd } from "@/lib/windows";

interface Entry {
  id: number;
  command: string;
  lines: OutputLine[];
  exit: number | null;
  ms: number | null;     // null while running
  expanded: boolean;
}

/**
 * Run a command in a terminal window's working directory.
 *
 * This is not the terminal itself: foot (and most terminals) offer no API to
 * type into or read from a window, so the command runs as a separate process
 * in the same folder through `run_command`. That keeps it audited and keeps
 * DESTRUCTIVE commands behind the normal confirmation policy.
 */
export function TerminalConsole({
  app,
  workspace,
  cwd,
  run,
  onClose,
}: {
  app: string;
  workspace: string;
  cwd: string;
  run: RunSkill;
  onClose: () => void;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [value, setValue] = useState("");
  const [history, setHistory] = useState(newHistory);
  const [elapsed, setElapsed] = useState(0);
  const [running, setRunning] = useState(false);
  const nextId = useRef(1);
  const alive = useRef(true);
  const input = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    alive.current = true;
    input.current?.focus();
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [entries]);

  useEffect(() => {
    if (!running) input.current?.focus();
  }, [running]);

  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Date.now() - start), 250);
    return () => clearInterval(timer);
  }, [running]);

  const patch = (id: number, change: Partial<Entry>) =>
    setEntries((list) => list.map((e) => (e.id === id ? { ...e, ...change } : e)));

  const submit = async () => {
    const command = value.trim();
    if (!command || running) return;
    const id = nextId.current++;
    const started = Date.now();
    setEntries((list) => [
      ...list.slice(-49),
      { id, command, lines: [], exit: null, ms: null, expanded: false },
    ]);
    setHistory((h) => pushHistory(h, command));
    setValue("");
    setRunning(true);
    try {
      // A dropped frame must not leave the input locked: give up a little after
      // the command's own timeout (a confirmation can add its own wait).
      let giveUp: ReturnType<typeof setTimeout> | undefined;
      const raw = await Promise.race([
        run("run_command", {
          command, working_directory: cwd, timeout_seconds: RUN_TIMEOUT_SECONDS,
        }),
        new Promise<string>((_, reject) => {
          giveUp = setTimeout(
            () => reject(new Error("No answer from the server.")),
            (RUN_TIMEOUT_SECONDS + 150) * 1000,
          );
        }),
      ]).finally(() => clearTimeout(giveUp));
      if (!alive.current) return;
      const parsed = parseResult(raw, command);
      patch(id, { lines: parsed.lines, exit: parsed.exit, ms: Date.now() - started });
    } catch (exc) {
      if (!alive.current) return;
      const text = exc instanceof Error ? exc.message : String(exc);
      patch(id, { lines: [{ stream: "note", text }], ms: Date.now() - started });
    } finally {
      if (alive.current) setRunning(false);
    }
  };

  const onKey = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void submit();
    } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      const step = historyStep(history, event.key === "ArrowUp" ? "up" : "down", value);
      setHistory(step.history);
      setValue(step.value);
    } else if (event.key === "l" && event.ctrlKey) {
      event.preventDefault();
      setEntries((list) => list.filter((e) => e.ms === null));
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  const colour = (stream: OutputLine["stream"]) =>
    stream === "stderr" ? "var(--destructive)" : stream === "note" ? "var(--text-muted)" : "var(--text)";

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6"
      onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <div
        role="dialog"
        aria-label="Run command"
        className="glass-strong flex h-[85dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-[var(--radius-xl)] sm:h-[70vh] sm:rounded-[var(--radius-xl)]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--glass-border)] px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-[length:var(--text-sm)] font-semibold">
              {app} · workspace {workspace || "?"} ·{" "}
              <span className="font-[family-name:var(--font-mono)] font-normal text-[var(--text-muted)]" title={cwd}>
                {shortCwd(cwd, homeOf(cwd))}
              </span>
            </div>
            <p className="mt-0.5 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              Commands run in this folder, not typed into the terminal window — its
              output cannot be read back. Stops after {RUN_TIMEOUT_SECONDS} s.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 cursor-pointer rounded-[var(--radius-sm)] px-2 py-1 text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            ✕
          </button>
        </header>

        <div
          ref={scroller}
          className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-[var(--surface-sunken)] px-4 py-3 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
        >
          {entries.length === 0 && (
            <p className="text-[var(--text-faint)]">
              Type a command below. Up/Down: history · Ctrl+L: clear · Esc: close.
            </p>
          )}
          {entries.map((entry) => {
            const { shown, hidden } = capLines(entry.lines, entry.expanded);
            const failed = entry.exit !== null && entry.exit !== 0;
            return (
              <div key={entry.id}>
                <div className="text-[var(--accent)]">$ {entry.command}</div>
                {shown.map((line, i) => (
                  <div key={i} className="break-all whitespace-pre-wrap" style={{ color: colour(line.stream) }}>
                    {line.text}
                  </div>
                ))}
                {hidden > 0 && (
                  <button
                    type="button"
                    onClick={() => patch(entry.id, { expanded: true })}
                    className="cursor-pointer text-[var(--accent)] underline"
                  >
                    show {hidden} more lines
                  </button>
                )}
                <div className="text-[var(--text-faint)]">
                  {entry.ms === null
                    ? `running… ${formatDuration(elapsed)} of ${RUN_TIMEOUT_SECONDS} s`
                    : `${entry.exit === null ? "no exit code" : `exit ${entry.exit}`} · ${formatDuration(entry.ms)}`}
                  {failed && " ✗"}
                </div>
              </div>
            );
          })}
        </div>

        <footer className="flex items-center gap-2 border-t border-[var(--glass-border)] px-3 py-2.5">
          <span className="font-[family-name:var(--font-mono)] text-[var(--accent)]">$</span>
          <input
            ref={input}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKey}
            disabled={running}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            placeholder={running ? "running…" : "command"}
            className="min-w-0 flex-1 bg-transparent font-[family-name:var(--font-mono)] text-[length:var(--text-sm)] outline-none placeholder:text-[var(--text-faint)]"
          />
          {running && (
            <span
              aria-hidden
              className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent"
            />
          )}
        </footer>
      </div>
    </div>
  );
}
