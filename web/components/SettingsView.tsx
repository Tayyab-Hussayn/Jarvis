"use client";

/**
 * Providers, models and skills.
 *
 * Key material never reaches this page — the server redacts it to `***`, and
 * `_deep_merge` drops that placeholder on write so saving cannot overwrite a
 * real key with the mask.
 */

import { useCallback, useEffect, useState } from "react";

import type { SkillInfo, StateSnapshot } from "@/lib/protocol";
import { api } from "@/lib/session";
import { Button, DangerBadge, Empty, Panel } from "./ui";

export function SettingsView() {
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [snapshot, list] = await Promise.all([
        api<StateSnapshot>("/api/state"),
        api<{ skills: SkillInfo[] }>("/api/skills"),
      ]);
      setState(snapshot);
      setSkills(list.skills);
      setError("");
    } catch (exc) {
      setError(String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <Empty>Could not read settings: {error}</Empty>;
  if (!state) return <Empty>Loading…</Empty>;

  const query = search.trim().toLowerCase();
  const visible = query
    ? skills.filter(
        (skill) =>
          skill.name.includes(query) ||
          skill.description.toLowerCase().includes(query) ||
          skill.category.toLowerCase().includes(query),
      )
    : skills;

  const byCategory = visible.reduce<Record<string, SkillInfo[]>>((acc, skill) => {
    (acc[skill.category || "other"] ??= []).push(skill);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      <Panel title="Language model" actions={<Button onClick={() => void load()}>Refresh</Button>}>
        <dl className="space-y-1.5 text-[var(--text-sm)]">
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--text-muted)]">Provider</dt>
            <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
              {state.provider}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--text-muted)]">Model</dt>
            <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
              {state.model}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--text-muted)]">Fallback</dt>
            <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
              {state.fallback_model || "—"}
            </dd>
          </div>
        </dl>

        <div className="mt-4 space-y-1.5">
          {state.providers.map((provider) => (
            <div
              key={provider.name}
              className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--border)] px-3 py-2"
            >
              <span className="font-[family-name:var(--font-mono)] text-[var(--text-sm)]">
                {provider.name}
              </span>
              <span
                className="text-[var(--text-xs)]"
                style={{
                  color: provider.cooling
                    ? "var(--critical)"
                    : provider.available
                      ? "var(--safe)"
                      : "var(--text-faint)",
                }}
              >
                {provider.cooling
                  ? "cooling down after failures"
                  : provider.available
                    ? "ready"
                    : "not configured"}
              </span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title={`Skills (${skills.length})`}>
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search skills…"
          className="mb-3 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[var(--text-sm)] outline-none"
        />
        {visible.length === 0 ? (
          <Empty>Nothing matches “{search}”.</Empty>
        ) : (
          <div className="max-h-[60vh] space-y-4 overflow-y-auto">
            {Object.entries(byCategory)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([category, list]) => (
                <div key={category}>
                  <h3 className="mb-1.5 text-[var(--text-xs)] uppercase tracking-wide text-[var(--text-faint)]">
                    {category}
                  </h3>
                  <ul className="space-y-1">
                    {list.map((skill) => (
                      <li
                        key={skill.name}
                        className="flex items-baseline gap-2 text-[var(--text-sm)]"
                      >
                        <span
                          className="shrink-0 font-[family-name:var(--font-mono)] text-[var(--text-xs)]"
                          style={{
                            color: skill.available
                              ? "var(--text)"
                              : "var(--text-faint)",
                          }}
                        >
                          {skill.name}
                        </span>
                        {skill.danger !== "SAFE" && <DangerBadge danger={skill.danger} />}
                        <span className="min-w-0 flex-1 truncate text-[var(--text-xs)] text-[var(--text-muted)]">
                          {skill.available
                            ? skill.description
                            : skill.unavailable_reason || "unavailable"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
