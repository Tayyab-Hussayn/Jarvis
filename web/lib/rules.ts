/**
 * "Always-approved commands": the rules a confirmation card's "Always allow"
 * created (engine #199). The server owns them; this page lists and revokes.
 * There is deliberately no way to add one here — a rule only ever comes from
 * a card the owner actually read.
 */

export interface SafetyRule {
  id: string;
  signature: string;
  created_at?: number | string | null;
  last_used?: number | string | null;
  uses?: number;
}

/** Keep only well-formed rules: an id to revoke by and a signature to show. */
export function parseRules(body: unknown): SafetyRule[] {
  const list = (body as { rules?: unknown } | null)?.rules;
  if (!Array.isArray(list)) return [];
  return list.filter(
    (r): r is SafetyRule =>
      !!r && typeof r.id === "string" && r.id !== "" && typeof r.signature === "string",
  );
}

/** A rule gone from the list once the server said the delete worked. */
export function withoutRule(rules: SafetyRule[], id: string): SafetyRule[] {
  return rules.filter((r) => r.id !== id);
}

/**
 * Epoch seconds, epoch milliseconds or an ISO string -> a short local time.
 * `never` for a missing value: a rule that was never used says so rather than
 * showing 1970.
 */
export function formatWhen(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "" || value === 0) return "never";
  const ms = typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : Date.parse(value);
  if (!Number.isFinite(ms)) return "unknown";
  return new Date(ms).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function usesLabel(uses: number | undefined): string {
  const n = typeof uses === "number" && uses > 0 ? uses : 0;
  return n === 1 ? "used once" : `used ${n} times`;
}
