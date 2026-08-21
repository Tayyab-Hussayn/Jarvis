"use client";

/**
 * The audit log.
 *
 * Per CLAUDE.md this is the first thing to read when debugging "why did it do
 * that" — it records tool calls, arguments, results, confirmations and
 * provider failovers. Key-like fields are already redacted server-side by
 * `audit.py`; nothing here un-redacts them.
 */

import { useEffect, useMemo, useState } from "react";

import type { AuditEntry } from "@/lib/protocol";
import { api } from "@/lib/session";
import { clockTime } from "@/lib/format";
import { Button, Empty, Panel } from "./ui";

const FILTERS = [
  { label: "Everything", prefix: "" },
  { label: "Requests", prefix: "agent." },
  { label: "Skills", prefix: "skill." },
  { label: "Safety", prefix: "safety." },
  { label: "Confirmations", prefix: "web.confirm" },
  { label: "Providers", prefix: "llm." },
] as const;

function eventTone(event: string): string {
  if (event.includes("blocked") || event.includes("denied") || event.includes("failed")) {
    return "var(--critical)";
  }
  if (event.startsWith("safety.") || event.includes("confirm")) return "var(--moderate)";
  if (event.startsWith("llm.")) return "var(--accent)";
  return "var(--text-muted)";
}

function Row({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const { ts, session, event, ...rest } = entry;
  void session;
  const summary = Object.entries(rest)
    .slice(0, 3)
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join("  ");

  return (
    <li className="border-b border-[var(--border)] last:border-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-baseline gap-3 px-1 py-1.5 text-left font-[family-name:var(--font-mono)] text-[var(--text-xs)]"
        aria-expanded={open}
      >
        <span className="shrink-0 text-[var(--text-faint)]">{clockTime(ts)}</span>
        <span className="shrink-0" style={{ color: eventTone(event) }}>
          {event}
        </span>
        <span className="min-w-0 flex-1 truncate text-[var(--text-faint)]">{summary}</span>
      </button>
      {open && (
        <pre className="overflow-x-auto whitespace-pre-wrap break-words px-1 pb-2 font-[family-name:var(--font-mono)] text-[var(--text-xs)] text-[var(--text-muted)]">
          {JSON.stringify(rest, null, 2)}
        </pre>
      )}
    </li>
  );
}

export function ActivityView() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [prefix, setPrefix] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  const load = useMemo(
    () => async (activePrefix: string) => {
      try {
        const data = await api<{ entries: AuditEntry[] }>(
          `/api/audit?n=300&prefix=${encodeURIComponent(activePrefix)}`,
        );
        setEntries(data.entries.slice().reverse());
        setError("");
      } catch (exc) {
        setError(String(exc));
      }
    },
    [],
  );

  useEffect(() => {
    void load(prefix);
  }, [load, prefix]);

  const visible = search
    ? entries.filter((entry) =>
        JSON.stringify(entry).toLowerCase().includes(search.toLowerCase()),
      )
    : entries;

  return (
    <Panel
      title="Audit log"
      actions={<Button onClick={() => void load(prefix)}>Refresh</Button>}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((filter) => (
          <button
            key={filter.label}
            type="button"
            onClick={() => setPrefix(filter.prefix)}
            className="rounded-[var(--radius-full)] border px-2.5 py-1 text-[var(--text-xs)] transition-colors"
            style={{
              transitionDuration: "var(--motion-fast)",
              borderColor: prefix === filter.prefix ? "var(--accent)" : "var(--border)",
              color: prefix === filter.prefix ? "var(--accent)" : "var(--text-muted)",
              background: prefix === filter.prefix ? "var(--accent-dim)" : "transparent",
            }}
          >
            {filter.label}
          </button>
        ))}
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Filter…"
          className="ml-auto w-40 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-2 py-1 text-[var(--text-xs)] outline-none"
        />
      </div>

      {error && <Empty>Could not read the audit log: {error}</Empty>}
      {!error && visible.length === 0 && <Empty>Nothing recorded yet.</Empty>}
      {visible.length > 0 && (
        <ul className="max-h-[65vh] overflow-y-auto">
          {visible.map((entry, index) => (
            <Row key={`${entry.ts}-${index}`} entry={entry} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
