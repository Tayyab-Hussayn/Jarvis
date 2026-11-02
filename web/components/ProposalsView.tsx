"use client";

/**
 * Proposals: what the Orchestrator suggests, for the owner to approve or
 * decline. Pending first, then recent ones with how they ended.
 *
 * The card's contents and the safe-default focus rule come from
 * `cardModel` (lib/proposals.ts), so they are tested without a DOM. Approving
 * only STARTS the task; every destructive step inside it still asks through
 * the usual confirmation card, so there is no second approval path here.
 *
 * Every click gets an answer: the busy flag is cleared in `finally`, so a
 * failed request shows its reason and the buttons come back instead of the
 * card waiting forever.
 */

import { useState } from "react";

import { cardModel, decideRequest, ordered, type Proposal } from "@/lib/proposals";
import { loadSessionId } from "@/lib/resume";
import { api } from "@/lib/session";
import { Screen } from "./Shell";
import { Button, Empty } from "./ui";

function ProposalCard({
  proposal,
  first,
  onMerge,
}: {
  proposal: Proposal;
  /** The first pending card takes keyboard focus on Decline. */
  first: boolean;
  onMerge: (rows: unknown) => void;
}) {
  const card = cardModel(proposal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const decide = async (decision: "approve" | "decline") => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const { path, init } = decideRequest(proposal.id, decision, loadSessionId());
      const answer = await api<{ proposal: unknown }>(path, init);
      onMerge([answer.proposal]);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const coral = card.accent === "coral";
  return (
    <article
      aria-label={card.title}
      className="glass-card rounded-[var(--radius-lg)] border-l-[3px] p-4"
      style={{
        borderLeftColor: coral ? "var(--coral)" : "var(--border-strong)",
        animation: "node-in var(--motion-base) var(--ease-out)",
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="rounded-[var(--radius-full)] border px-2 py-0.5 text-[length:var(--text-xs)] font-semibold uppercase"
          style={{
            letterSpacing: "var(--tracking-label)",
            color: coral ? "var(--coral)" : "var(--text-muted)",
            borderColor: "color-mix(in srgb, currentColor 45%, transparent)",
          }}
        >
          {card.riskLabel}
        </span>
        <span
          className="text-[length:var(--text-xs)]"
          style={{ color: card.canDecide ? "var(--coral)" : "var(--text-muted)" }}
        >
          {card.statusLabel}
        </span>
        {card.agent && (
          <span className="ml-auto font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
            {card.agent}
          </span>
        )}
      </div>

      <h3 className="mt-2 text-[length:var(--text-base)] font-semibold text-[var(--text-strong)]">
        {card.title}
      </h3>

      {card.reasons.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[length:var(--text-sm)] text-[var(--text-muted)]">
          {card.reasons.map((reason, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden className="text-[var(--text-faint)]">·</span>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}

      {card.task && (
        <p className="mt-2 rounded-[var(--radius-sm)] bg-[var(--surface-sunken)] px-2.5 py-1.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
          {card.task}
        </p>
      )}

      {card.canDecide && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {card.actions.map((action) => (
            <Button
              key={action.id}
              variant={action.primary ? "primary" : "default"}
              disabled={busy}
              // The safe answer takes focus, on the first pending card only.
              autoFocus={first && card.focusFirst === action.id}
              onClick={() => void decide(action.id)}
            >
              {busy && action.primary ? "Working…" : action.label}
            </Button>
          ))}
          <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
            Approving starts it; risky steps still ask you.
          </span>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-2 text-[length:var(--text-xs)]" style={{ color: "var(--critical)" }}>
          {error}
        </p>
      )}

      {card.result && (
        <pre
          className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] border-l-2 bg-[var(--surface-sunken)] px-3 py-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
          style={{
            borderLeftColor: card.resultTone === "bad" ? "var(--critical)" : "var(--steel)",
            color: card.resultTone === "bad" ? "var(--critical)" : "var(--text)",
          }}
        >
          {card.result}
        </pre>
      )}
    </article>
  );
}

export function ProposalsView({
  proposals,
  onMerge,
  onRefresh,
}: {
  proposals: Proposal[];
  onMerge: (rows: unknown) => void;
  onRefresh: () => void;
}) {
  const list = ordered(proposals);
  const firstPending = list.find((p) => p.status === "pending")?.id;

  return (
    <Screen
      subtitle="The Orchestrator suggests; you decide. Nothing runs until you approve."
      actions={<Button onClick={onRefresh}>Refresh</Button>}
    >
      {list.length === 0 ? (
        <Empty>No proposals yet. The daily review adds them when something needs you.</Empty>
      ) : (
        <div className="space-y-3">
          {list.map((p) => (
            <ProposalCard key={p.id} proposal={p} first={p.id === firstPending} onMerge={onMerge} />
          ))}
        </div>
      )}
    </Screen>
  );
}
