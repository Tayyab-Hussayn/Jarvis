"use client";

/**
 * Models pane: which model chain each agent role uses, and what it costs.
 *
 * No optimistic state: a row shows the chain the server last answered, and an
 * edit is a local draft until a PUT returns 200 (then the list is re-read).
 * `busy` is cleared in `finally`, so a lost reply cannot lock a row. Probing
 * costs money and stays a CLI action; the pane only says which command to run.
 */

import { useCallback, useEffect, useState } from "react";

import {
  DAY_RANGES,
  formatCost,
  formatTokens,
  inheritLabel,
  moveEntry,
  parseModels,
  parseUsage,
  pickerOptions,
  putRequest,
  removeEntry,
  sameChain,
  setEntry,
  totalCost,
  type ModelsState,
  type SpendRow,
} from "@/lib/models";
import { api } from "@/lib/session";
import { Button, Chip, Empty, Panel } from "./ui";

const EMPTY: ModelsState = {
  catalog: [], roles: [], agentRoles: [], chains: {}, inUse: {}, warnings: [],
};

export function ModelsView() {
  const [state, setState] = useState<ModelsState | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setState(parseModels(await api<unknown>("/api/models")));
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const s = state ?? EMPTY;

  return (
    <div className="flex flex-col gap-4" data-models="">
      {error && (
        <p className="text-[length:var(--text-sm)]" style={{ color: "var(--critical)" }} role="alert">
          {error}
        </p>
      )}
      {s.warnings.length > 0 && (
        <div
          className="rounded-[var(--radius-lg)] border border-[var(--moderate)] bg-[var(--moderate-dim)] p-3 text-[length:var(--text-sm)]"
          role="status"
          data-models-warnings=""
        >
          <strong>Model warnings</strong>
          <ul className="mt-1 list-disc pl-5">
            {s.warnings.map((w) => (
              <li key={w} className="break-words">{w}</li>
            ))}
          </ul>
        </div>
      )}
      <Panel title="Model per role" actions={<Button onClick={() => void load()}>Reload</Button>}>
        <p className="mb-3 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          A change applies from the next turn, with no restart. An agent role&apos;s first model
          must be verified; the later ones are failover, tried in order.
        </p>
        {state === null ? (
          <Empty>{error ? "Could not read the models." : "Loading…"}</Empty>
        ) : s.roles.length === 0 ? (
          <Empty>No roles known.</Empty>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {s.roles.map((role) => (
              <RoleRow key={role} role={role} state={s} onSaved={load} />
            ))}
          </ul>
        )}
      </Panel>
      <SpendPanel />
    </div>
  );
}

function RoleRow({
  role, state, onSaved,
}: {
  role: string;
  state: ModelsState;
  onSaved: () => Promise<void>;
}) {
  const saved = state.chains[role] ?? [];
  const [draft, setDraft] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);

  const save = async (chain: string[]) => {
    setBusy(true);
    setProblems([]);
    try {
      const { path, init } = putRequest(role, chain);
      await api<unknown>(path, init);
      setDraft(null);
      await onSaved();
    } catch (exc) {
      // The server's reasons ("... run `kravein models probe …`") are the message.
      setProblems([exc instanceof Error ? exc.message : String(exc)]);
    } finally {
      setBusy(false);
    }
  };

  const editing = draft !== null;
  const chain = draft ?? saved;
  const dirty = editing && !sameChain(draft ?? [], saved);

  return (
    <li className="py-3" data-model-role={role}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">{role}</code>
        {!editing && (
          <Button variant="ghost" onClick={() => setDraft(saved.length ? saved : [""])}>
            Edit
          </Button>
        )}
      </div>
      {!editing &&
        (saved.length === 0 ? (
          <p className="mt-1 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            {inheritLabel(role)}
          </p>
        ) : (
          <ol className="mt-1 list-decimal pl-5 text-[length:var(--text-xs)] text-[var(--text-muted)]">
            {saved.map((k, i) => (
              <li key={`${k}-${i}`} className="break-all">{k}</li>
            ))}
          </ol>
        ))}
      {editing && (
        <div className="mt-2 flex flex-col gap-2">
          {chain.map((entry, slot) => (
            <div key={slot} className="flex flex-wrap items-center gap-2">
              <span className="w-5 text-[length:var(--text-xs)] text-[var(--text-faint)]">{slot + 1}.</span>
              <select
                aria-label={`${role} model ${slot + 1}`}
                value={entry}
                disabled={busy}
                onChange={(e) => setDraft(setEntry(chain, slot, e.target.value))}
                className="glass min-w-0 flex-1 rounded-[var(--radius-md)] border border-[var(--glass-border)] px-2 py-1.5 text-[length:var(--text-sm)] max-sm:min-h-10"
              >
                <option value="">Choose a model…</option>
                {entry && !state.catalog.some((c) => c.key === entry) && (
                  <option value={entry}>{entry} (not in catalog)</option>
                )}
                {pickerOptions(role, state.catalog, chain, slot).map((o) => (
                  <option key={o.key} value={o.key} disabled={o.disabled}>
                    {o.key}
                    {o.reason ? ` — ${o.reason}` : ""}
                  </option>
                ))}
              </select>
              <Button variant="ghost" disabled={busy || slot === 0} title="Earlier (tried first)"
                onClick={() => setDraft(moveEntry(chain, slot, slot - 1))}>↑</Button>
              <Button variant="ghost" disabled={busy || slot === chain.length - 1} title="Later (failover)"
                onClick={() => setDraft(moveEntry(chain, slot, slot + 1))}>↓</Button>
              <Button variant="ghost" disabled={busy} title="Remove from chain"
                onClick={() => setDraft(removeEntry(chain, slot))}>Remove</Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={() => setDraft([...chain, ""])}>Add failover</Button>
            <Button
              variant="primary"
              disabled={busy || !dirty || chain.length === 0 || chain.some((e) => !e)}
              onClick={() => void save(chain)}
            >
              Save
            </Button>
            {saved.length > 0 && (
              <Button variant="danger" disabled={busy} onClick={() => void save([])}
                title="Remove this role's chain so it inherits again">
                Use inherited
              </Button>
            )}
            <Button variant="ghost" disabled={busy} onClick={() => { setDraft(null); setProblems([]); }}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {problems.length > 0 && (
        <p className="mt-2 break-words text-[length:var(--text-sm)]" style={{ color: "var(--critical)" }} role="alert">
          {problems.join(" · ")}
        </p>
      )}
    </li>
  );
}

function SpendPanel() {
  const [days, setDays] = useState<number>(7);
  const [rows, setRows] = useState<SpendRow[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async (n: number) => {
    try {
      const raw = await api<unknown>(`/api/usage?days=${n}`);
      setRows(parseUsage(raw));
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    // A stale answer for an older range must not overwrite the current one.
    let live = true;
    setRows(null);
    api<unknown>(`/api/usage?days=${days}`)
      .then((raw) => { if (live) { setRows(parseUsage(raw)); setError(""); } })
      .catch((exc) => { if (live) setError(exc instanceof Error ? exc.message : String(exc)); });
    return () => { live = false; };
  }, [days]);

  const total = rows ? totalCost(rows) : null;
  return (
    <Panel
      title={`Spend, last ${days} day${days === 1 ? "" : "s"}`}
      actions={
        <div className="flex items-center gap-1.5">
          {DAY_RANGES.map((n) => (
            <Chip key={n} active={days === n} onClick={() => setDays(n)}>{n}d</Chip>
          ))}
          <Button onClick={() => void load(days)}>Reload</Button>
        </div>
      }
    >
      {error && (
        <p className="mb-2 text-[length:var(--text-sm)]" style={{ color: "var(--critical)" }} role="alert">{error}</p>
      )}
      {rows === null ? (
        <Empty>{error ? "Could not read usage." : "Loading…"}</Empty>
      ) : rows.length === 0 ? (
        <Empty>No model calls recorded in this range.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[length:var(--text-xs)]" data-spend="">
            <thead className="text-[var(--text-faint)]">
              <tr>
                <th className="py-1 pr-3 font-medium">Role</th>
                <th className="py-1 pr-3 font-medium">Model</th>
                <th className="py-1 pr-3 text-right font-medium">Calls</th>
                <th className="py-1 pr-3 text-right font-medium">Failed</th>
                <th className="py-1 pr-3 text-right font-medium">Input</th>
                <th className="py-1 pr-3 text-right font-medium">Cached</th>
                <th className="py-1 pr-3 text-right font-medium">Output</th>
                <th className="py-1 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.role}|${r.model}`} className="border-t border-[var(--border)]">
                  <td className="py-1.5 pr-3 font-[family-name:var(--font-mono)]">{r.role}</td>
                  <td className="break-all py-1.5 pr-3 font-[family-name:var(--font-mono)]">{r.model}</td>
                  <td className="py-1.5 pr-3 text-right">{r.calls}</td>
                  <td className="py-1.5 pr-3 text-right">{r.failures}</td>
                  <td className="py-1.5 pr-3 text-right">{formatTokens(r.input)}</td>
                  <td className="py-1.5 pr-3 text-right">{formatTokens(r.cached)}</td>
                  <td className="py-1.5 pr-3 text-right" title={r.unreported ? `${r.unreported} call(s) reported no usage` : undefined}>
                    {formatTokens(r.output)}{r.unreported ? "*" : ""}
                  </td>
                  <td className="py-1.5 text-right">{formatCost(r.cost)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-[var(--border)] font-semibold">
                <td className="py-1.5" colSpan={7}>Total (known costs only)</td>
                <td className="py-1.5 text-right">{formatCost(total)}</td>
              </tr>
            </tfoot>
          </table>
          <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            “—” means the price or token usage is unknown, not free. * some calls reported no usage.
          </p>
        </div>
      )}
    </Panel>
  );
}
