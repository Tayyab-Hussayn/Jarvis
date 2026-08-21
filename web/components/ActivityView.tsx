"use client";

/**
 * The audit log, as a master/detail split.
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
import { Button, Chip, Empty, Input, LightPanel, Panel } from "./ui";
import { Screen } from "./Shell";

const FILTERS = [
  { label: "Everything", prefix: "" },
  { label: "Requests", prefix: "agent." },
  { label: "Skills", prefix: "skill." },
  { label: "Safety", prefix: "safety." },
  { label: "Confirmations", prefix: "web.confirm" },
  { label: "Providers", prefix: "llm." },
] as const;

function eventTone(event: string): string {
  if (
    event.includes("blocked") ||
    event.includes("denied") ||
    event.includes("failed") ||
    event.includes("bypass")
  ) {
    return "var(--critical)";
  }
  if (event.startsWith("safety.") || event.includes("confirm")) return "var(--moderate)";
  if (event.startsWith("llm.")) return "var(--accent)";
  return "var(--text-muted)";
}

export function ActivityView() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [prefix, setPrefix] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<AuditEntry | null>(null);

  const load = useMemo(
    () => async (activePrefix: string) => {
      try {
        const data = await api<{ entries: AuditEntry[] }>(
          `/api/audit?n=400&prefix=${encodeURIComponent(activePrefix)}`,
        );
        setEntries(data.entries.slice().reverse());
        setError("");
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
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
    <Screen
      title="Activity"
      subtitle="Everything Brahma actually did — read this first when something looks wrong."
      actions={<Button onClick={() => void load(prefix)}>Refresh</Button>}
    >
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="no-scrollbar flex gap-2 overflow-x-auto">
            {FILTERS.map((filter) => (
              <Chip
                key={filter.label}
                active={prefix === filter.prefix}
                onClick={() => setPrefix(filter.prefix)}
              >
                {filter.label}
              </Chip>
            ))}
          </div>
          <Input
            value={search}
            onChange={setSearch}
            placeholder="Filter…"
            className="ml-auto w-48"
          />
        </div>

        {error && <Empty>Could not read the audit log: {error}</Empty>}

        {!error && (
          <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1fr_22rem]">
            <Panel padded={false} className="min-h-0 overflow-hidden">
              <div className="h-full max-h-[62vh] overflow-y-auto">
                {visible.length === 0 ? (
                  <Empty>Nothing recorded yet.</Empty>
                ) : (
                  <ul>
                    {visible.map((entry, index) => {
                      const { ts, event, session, ...rest } = entry;
                      void session;
                      const summary = Object.entries(rest)
                        .slice(0, 3)
                        .map(
                          ([key, value]) =>
                            `${key}=${
                              typeof value === "string" ? value : JSON.stringify(value)
                            }`,
                        )
                        .join("  ");
                      const active = selected === entry;
                      return (
                        <li key={`${ts}-${index}`}>
                          <button
                            type="button"
                            onClick={() => setSelected(entry)}
                            className="flex w-full items-baseline gap-3 border-b border-[var(--border)] px-4 py-1.5 text-left font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                            style={{
                              background: active ? "var(--accent-dim)" : "transparent",
                            }}
                          >
                            <span className="shrink-0 text-[var(--text-faint)]">
                              {clockTime(ts)}
                            </span>
                            <span className="shrink-0" style={{ color: eventTone(event) }}>
                              {event}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-[var(--text-faint)]">
                              {summary}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </Panel>

            {selected ? (
              <LightPanel title={selected.event}>
                <div className="p-4">
                  <p
                    className="mb-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                    style={{ color: "var(--panel-faint)" }}
                  >
                    {selected.ts}
                  </p>
                  <pre
                    className="max-h-[52vh] overflow-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                    style={{ color: "var(--panel-muted)" }}
                  >
                    {JSON.stringify(selected, null, 2)}
                  </pre>
                </div>
              </LightPanel>
            ) : (
              <Panel title="Entry">
                <Empty>Select an entry.</Empty>
              </Panel>
            )}
          </div>
        )}
      </div>
    </Screen>
  );
}
