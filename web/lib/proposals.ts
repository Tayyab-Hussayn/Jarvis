/**
 * Proposals: what the Orchestrator suggests and the owner decides.
 *
 * Pure, so the rules are testable: parsing the server's rows defensively,
 * merging a REST list with live `proposal` frames (either may arrive first),
 * ordering, the dock badge, and the shape of one card (labels, risk accent and
 * which action takes focus). The server owns the state machine
 * (pending -> approved|declined|expired, approved -> running -> done|failed);
 * nothing here invents a transition.
 */

export type ProposalStatus =
  | "pending"
  | "approved"
  | "declined"
  | "expired"
  | "running"
  | "done"
  | "failed";

export interface Proposal {
  id: string;
  title: string;
  reasons: string[];
  kind: string;
  agent: string;
  task: string;
  risk: "low" | "major";
  status: ProposalStatus;
  createdBy: string;
  createdAt: number;
  decidedAt?: number;
  decidedBy?: string;
  result?: string;
}

const STATUSES: ReadonlySet<string> = new Set([
  "pending", "approved", "declined", "expired", "running", "done", "failed",
]);

/** How far along the state machine a status is. A late REST answer must never
 *  roll a card back past what a live frame already showed. */
const RANK: Record<ProposalStatus, number> = {
  pending: 0,
  approved: 1,
  running: 2,
  done: 3,
  failed: 3,
  declined: 3,
  expired: 3,
};

/** Recent (non-pending) proposals shown; older ones are noise. */
export const RECENT_CAP = 30;

const str = (v: unknown, max: number): string =>
  typeof v === "string" ? v.slice(0, max) : "";

/** Accept only a well-formed row; anything else is `null` and is skipped. */
export function parseProposal(raw: unknown): Proposal | null {
  if (raw === null || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id, 80);
  const status = typeof r.status === "string" ? r.status : "";
  if (!id || !STATUSES.has(status) || !str(r.title, 300)) return null;
  const reasons = Array.isArray(r.reasons)
    ? r.reasons.filter((x): x is string => typeof x === "string").map((x) => x.slice(0, 400)).slice(0, 12)
    : [];
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
  const out: Proposal = {
    id,
    title: str(r.title, 300),
    reasons,
    kind: str(r.kind, 20) || "note",
    agent: str(r.agent, 60),
    task: str(r.task, 2000),
    risk: r.risk === "major" ? "major" : "low",
    status: status as ProposalStatus,
    createdBy: str(r.created_by, 60),
    createdAt: num(r.created_at) ?? 0,
  };
  const decidedAt = num(r.decided_at);
  if (decidedAt !== undefined) out.decidedAt = decidedAt;
  if (typeof r.decided_by === "string") out.decidedBy = r.decided_by.slice(0, 60);
  if (typeof r.result === "string") out.result = r.result.slice(0, 2000);
  return out;
}

/** Insert or update one proposal; never move an existing one backwards. */
export function upsert(list: Proposal[], incoming: Proposal): Proposal[] {
  const at = list.findIndex((p) => p.id === incoming.id);
  if (at < 0) return [...list, incoming];
  if (RANK[incoming.status] < RANK[list[at].status]) return list;
  const next = list.slice();
  next[at] = incoming;
  return next;
}

/** Merge a REST listing in; same no-rollback rule per row. */
export function mergeList(list: Proposal[], rows: unknown): Proposal[] {
  if (!Array.isArray(rows)) return list;
  let next = list;
  for (const row of rows) {
    const p = parseProposal(row);
    if (p) next = upsert(next, p);
  }
  return next;
}

/** A live `{t:"proposal", proposal}` frame. */
export function applyFrame(list: Proposal[], frame: { proposal?: unknown }): Proposal[] {
  const p = parseProposal(frame.proposal);
  return p ? upsert(list, p) : list;
}

/** Pending first (newest first), then the recent rest (latest activity first). */
export function ordered(list: Proposal[]): Proposal[] {
  const pending = list
    .filter((p) => p.status === "pending")
    .sort((a, b) => b.createdAt - a.createdAt);
  const rest = list
    .filter((p) => p.status !== "pending")
    .sort((a, b) => (b.decidedAt ?? b.createdAt) - (a.decidedAt ?? a.createdAt))
    .slice(0, RECENT_CAP);
  return [...pending, ...rest];
}

/** The dock badge: only things waiting on the owner. */
export function pendingCount(list: Proposal[]): number {
  return list.filter((p) => p.status === "pending").length;
}

export const STATUS_LABEL: Record<ProposalStatus, string> = {
  pending: "Waiting for you",
  approved: "Approved",
  running: "Running",
  done: "Done",
  failed: "Failed",
  declined: "Declined",
  expired: "Expired",
};

export interface CardModel {
  id: string;
  title: string;
  reasons: string[];
  agent: string;
  task: string;
  riskLabel: string;
  /** Major risk gets the coral accent (the "attention" colour). */
  accent: "coral" | "none";
  statusLabel: string;
  canDecide: boolean;
  /** Which action takes keyboard focus first: the safe one. */
  focusFirst: "decline" | null;
  /** Approve is the primary (laser) button; decline is secondary. */
  actions: { id: "decline" | "approve"; label: string; primary: boolean }[];
  result: string;
  resultTone: "ok" | "bad" | "none";
}

export function cardModel(p: Proposal): CardModel {
  const canDecide = p.status === "pending";
  return {
    id: p.id,
    title: p.title,
    reasons: p.reasons,
    agent: p.agent,
    task: p.task,
    riskLabel: p.risk === "major" ? "Major risk" : "Low risk",
    accent: p.risk === "major" ? "coral" : "none",
    statusLabel: STATUS_LABEL[p.status],
    canDecide,
    focusFirst: canDecide ? "decline" : null,
    // Decline first in reading order, as on the confirmation card.
    actions: canDecide
      ? [
          { id: "decline", label: "Decline", primary: false },
          { id: "approve", label: "Approve", primary: true },
        ]
      : [],
    result: p.result ?? "",
    resultTone: p.status === "done" ? "ok" : p.status === "failed" ? "bad" : "none",
  };
}

/** The decide request: path and body, with the current session so the server
 *  starts an approved task in THIS session rather than any idle one. */
export function decideRequest(
  id: string,
  decision: "approve" | "decline",
  sessionId: string,
): { path: string; init: RequestInit } {
  return {
    path: `/api/proposals/${encodeURIComponent(id)}/decide`,
    init: {
      method: "POST",
      body: JSON.stringify({ decision, ...(sessionId ? { session_id: sessionId } : {}) }),
    },
  };
}
