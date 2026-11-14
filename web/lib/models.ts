/**
 * Models pane logic (v3 phase 0d), pure so it is testable without a DOM.
 *
 * The server (`/api/models`, `PUT /api/models/roles/<role>`, `/api/usage`) is
 * the only authority: the pane shows what the server last answered and a
 * chain is only "saved" after a 200. Everything here is defensive about shape
 * so an older or odd reply degrades to an empty list, never a crash.
 */

export type ToolState = "verified" | "failed" | "untested";

export interface CatalogEntry {
  key: string;
  tool: ToolState;
  price: { input?: number; cached?: number; output?: number } | null;
  asOf: string;
  probeError: string;
}

export interface ModelsState {
  catalog: CatalogEntry[];
  roles: string[];
  agentRoles: string[];
  chains: Record<string, string[]>;
  inUse: Record<string, string[]>;
  warnings: string[];
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

export function parseModels(raw: unknown): ModelsState {
  const r = isObj(raw) ? raw : {};
  const catalog: CatalogEntry[] = [];
  if (isObj(r.catalog)) {
    for (const [key, v] of Object.entries(r.catalog)) {
      const e = isObj(v) ? v : {};
      const t = e.tool_calling;
      const probe = isObj(e.probe) ? e.probe : {};
      catalog.push({
        key,
        tool: t === "verified" || t === "failed" ? t : "untested",
        price: isObj(e.price) ? (e.price as CatalogEntry["price"]) : null,
        asOf: typeof e.as_of === "string" ? e.as_of : "",
        probeError: typeof probe.error === "string" ? probe.error : "",
      });
    }
  }
  catalog.sort((a, b) => a.key.localeCompare(b.key));
  const chains: Record<string, string[]> = {};
  if (isObj(r.chains)) for (const [k, v] of Object.entries(r.chains)) chains[k] = strings(v);
  const inUse: Record<string, string[]> = {};
  if (isObj(r.in_use)) for (const [k, v] of Object.entries(r.in_use)) inUse[k] = strings(v);
  return {
    catalog,
    roles: strings(r.roles),
    agentRoles: strings(r.agent_roles),
    chains,
    inUse,
    warnings: strings(r.warnings),
  };
}

export const isAgentRole = (role: string): boolean =>
  role === "orchestrator" || role.startsWith("worker:");

/** What a row says when the role has no chain of its own. */
export function inheritLabel(role: string): string {
  return role === "orchestrator" ? "uses the global chain" : "inherits orchestrator";
}

export interface PickerOption {
  key: string;
  disabled: boolean;
  /** Why it is disabled; empty when it is selectable. */
  reason: string;
}

/**
 * Catalog models the picker offers for `role`. For an agent role only a
 * `verified` model may LEAD (the server enforces it too); later entries are
 * failover and may be anything, so only the first slot is restricted.
 */
export function pickerOptions(
  role: string,
  catalog: CatalogEntry[],
  chain: string[],
  slot: number,
): PickerOption[] {
  return catalog.map((c) => {
    if (chain.some((k, i) => k === c.key && i !== slot)) {
      return { key: c.key, disabled: true, reason: "already in this chain" };
    }
    if (isAgentRole(role) && slot === 0 && c.tool !== "verified") {
      const reason =
        c.tool === "failed"
          ? `tool calling failed its probe${c.probeError ? ` (${c.probeError})` : ""}`
          : `not probed: run \`kravein models probe ${c.key}\``;
      return { key: c.key, disabled: true, reason };
    }
    return { key: c.key, disabled: false, reason: "" };
  });
}

export function moveEntry(chain: string[], from: number, to: number): string[] {
  if (from < 0 || from >= chain.length || to < 0 || to >= chain.length || from === to) {
    return chain;
  }
  const next = chain.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export const removeEntry = (chain: string[], at: number): string[] =>
  chain.filter((_, i) => i !== at);

export function setEntry(chain: string[], at: number, key: string): string[] {
  const next = chain.slice();
  if (at >= next.length) next.push(key);
  else next[at] = key;
  return next;
}

export const sameChain = (a: string[], b: string[]): boolean =>
  a.length === b.length && a.every((x, i) => x === b[i]);

export function putRequest(role: string, chain: string[]): { path: string; init: RequestInit } {
  return {
    path: `/api/models/roles/${encodeURIComponent(role)}`,
    init: { method: "PUT", body: JSON.stringify({ chain }) },
  };
}

/* -- spend ------------------------------------------------------------ */

export interface SpendRow {
  role: string;
  model: string;
  calls: number;
  failures: number;
  input: number;
  cached: number;
  output: number;
  /** Calls that did not report token usage; the sums miss them. */
  unreported: number;
  cost: number | null;
}

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

export function parseUsage(raw: unknown): SpendRow[] {
  const rows = isObj(raw) && Array.isArray(raw.rows) ? raw.rows : [];
  const out: SpendRow[] = [];
  for (const v of rows) {
    if (!isObj(v)) continue;
    const provider = typeof v.provider === "string" ? v.provider : "";
    const model = typeof v.model === "string" ? v.model : "";
    const calls = num(v.calls);
    out.push({
      role: typeof v.role === "string" && v.role ? v.role : "default",
      model: model ? `${provider}:${model}` : provider || "unknown",
      calls,
      failures: num(v.failures),
      input: num(v.input),
      cached: num(v.cached),
      output: num(v.output),
      unreported: Math.max(0, calls - num(v.reported)),
      cost: typeof v.cost === "number" && Number.isFinite(v.cost) ? v.cost : null,
    });
  }
  return out;
}

export const formatCost = (cost: number | null): string =>
  cost === null ? "—" : cost > 0 && cost < 0.0001 ? "<$0.0001" : `$${cost.toFixed(4)}`;

export const formatTokens = (n: number): string =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M` : n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : String(n);

/** Total of the known costs; null when no row had one (shown as "—"). */
export function totalCost(rows: SpendRow[]): number | null {
  const known = rows.filter((r) => r.cost !== null);
  return known.length ? known.reduce((s, r) => s + (r.cost ?? 0), 0) : null;
}

export const DAY_RANGES = [1, 7, 30] as const;
