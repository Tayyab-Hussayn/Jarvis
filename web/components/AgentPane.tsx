"use client";

/**
 * Agent detail: who this agent is, what it is doing, and what it has done.
 *
 * Everything shown is derived from the agent-state store: identity from
 * /api/agents when it answers (else the shipped profile text), the step
 * timeline from tool frames, the report from the delegate result or the
 * `agent_state` frame. Tool arguments appear only as the short preview the
 * store kept, never raw.
 */

import {
  ORCHESTRATOR,
  type AgentView,
  type AgentsModel,
  type Step,
} from "@/lib/agents";
import { formatDuration } from "@/lib/format";
import { StateGlyph, StatePill, StateRing, STATE_TEXT } from "./agentVisual";
import { Empty } from "./ui";

function StepIcon({ status }: { status: Step["status"] }) {
  const common = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (status) {
    case "ok":
      return <svg {...common}><path d="m6 12.5 4 4 8-9" /></svg>;
    case "failed":
      return <svg {...common}><path d="m7 7 10 10M17 7 7 17" /></svg>;
    case "lost":
      return <svg {...common}><path d="M12 7v6M12 17h.01" /></svg>;
    default:
      return <svg {...common} style={{ animation: "spin 1s linear infinite" }}><path d="M12 3a9 9 0 1 0 9 9" /></svg>;
  }
}

const STEP_LABEL: Record<Step["status"], string> = {
  running: "running",
  ok: "done",
  failed: "failed",
  lost: "result unknown",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h3 className="eyebrow mb-2" style={{ fontFamily: "var(--font-sans)", letterSpacing: "var(--tracking-label)" }}>
        {title}
      </h3>
      {children}
    </section>
  );
}

export function AgentPane({
  name,
  model,
  onReview,
}: {
  name: string;
  model: AgentsModel;
  onReview: () => void;
}) {
  const view: AgentView | undefined = model.agents[name];
  if (!view) {
    return <Empty>There is no agent called “{name}”.</Empty>;
  }

  const isCore = view.role === "orchestrator";
  // The Orchestrator's "recent delegations" are everyone's; a sub-agent's are
  // the ones it was handed.
  const delegations = isCore
    ? model.order
        .filter((n) => n !== ORCHESTRATOR)
        .flatMap((n) => model.agents[n].delegations.map((d) => ({ ...d, agent: n })))
        .sort((a, b) => b.startedAt - a.startedAt)
        .slice(0, 8)
    : view.delegations.map((d) => ({ ...d, agent: view.name }));
  const steps = [...view.steps].reverse();

  return (
    <div className="h-full min-h-0 overflow-y-auto pb-6">
      <div className="flex items-start gap-4">
        <div className="relative h-16 w-16 shrink-0" style={{ color: STATE_TEXT[view.state] }}>
          <span className="absolute inset-0 flex items-center justify-center">
            <StateGlyph state={view.state} size={22} />
          </span>
          <StateRing state={view.state} size={64} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[length:var(--text-lg)] font-semibold text-[var(--text-strong)]">
              {isCore ? "Orchestrator" : view.name}
            </span>
            <StatePill state={view.state} />
          </div>
          <p className="mt-1 text-[length:var(--text-sm)] text-[var(--text-muted)]">
            {view.purpose || "No description."}
          </p>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            <span>
              Model{" "}
              <span className="font-[family-name:var(--font-mono)] text-[var(--text-muted)]">
                {view.model || "default"}
              </span>
            </span>
            <span>
              Tools{" "}
              <span className="text-[var(--text-muted)]">
                {view.tools === null ? "—" : view.tools}
              </span>
            </span>
          </p>
        </div>
      </div>

      {view.state === "waiting" && (
        <button
          type="button"
          onClick={onReview}
          className="mt-4 w-full rounded-[var(--radius)] border px-3 py-2 text-left text-[length:var(--text-sm)] font-medium"
          style={{ borderColor: "var(--coral)", color: "var(--coral)", background: "var(--accent-dim)" }}
        >
          Waiting for your OK — review the confirmation
        </button>
      )}

      <Section title="Now">
        {view.state === "idle" ? (
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">Idle.</p>
        ) : (
          <div className="rounded-[var(--radius)] border border-[var(--border)] px-3 py-2">
            <p className="text-[length:var(--text-sm)]">{view.task || "Working on the current request."}</p>
            {view.tool && (
              <p className="mt-1 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--accent)]">
                {view.tool}
              </p>
            )}
          </div>
        )}
      </Section>

      <Section title="Live steps">
        {steps.length === 0 ? (
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">No tool calls yet.</p>
        ) : (
          <ol className="space-y-1" aria-label="Step timeline">
            {steps.map((step) => (
              <li
                key={step.id}
                className="flex items-start gap-3 rounded-[var(--radius-sm)] px-2 py-1.5"
                style={{ background: step.status === "running" ? "var(--accent-dim)" : undefined }}
              >
                <span
                  className="mt-0.5 shrink-0"
                  style={{
                    color:
                      step.status === "failed"
                        ? "var(--state-failed)"
                        : step.status === "running"
                          ? "var(--accent)"
                          : step.status === "lost"
                            ? "var(--moderate)"
                            : "var(--text-muted)",
                  }}
                >
                  <StepIcon status={step.status} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
                    {step.tool}
                  </span>
                  {step.args && (
                    <span className="block truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
                      {step.args}
                    </span>
                  )}
                </span>
                <span className="shrink-0 text-right text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  {STEP_LABEL[step.status]}
                  {step.durationMs !== undefined && (
                    <span className="block tabular-nums">{formatDuration(step.durationMs)}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Last report">
        {view.report ? (
          <div className="rounded-[var(--radius)] border border-[var(--border)] px-3 py-2">
            <p className="text-[length:var(--text-xs)] font-semibold uppercase" style={{ letterSpacing: "var(--tracking-label)" }}>
              Outcome: {view.report.outcome}
            </p>
            <p className="mt-1 whitespace-pre-wrap text-[length:var(--text-sm)] text-[var(--text)]">
              {view.report.summary || "(no summary)"}
            </p>
          </div>
        ) : (
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
            {isCore ? "The Orchestrator answers you directly; sub-agents report to it." : "No report yet."}
          </p>
        )}
      </Section>

      <Section title="Recent delegations">
        {delegations.length === 0 ? (
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">None this session.</p>
        ) : (
          <ul className="space-y-1">
            {delegations.map((d) => (
              <li key={`${d.agent}-${d.id}`} className="rounded-[var(--radius-sm)] px-2 py-1.5 text-[length:var(--text-sm)]">
                <span className="font-semibold">{d.agent}</span>
                <span className="text-[var(--text-faint)]"> · {d.outcome ?? "running"}</span>
                <span className="block truncate text-[length:var(--text-xs)] text-[var(--text-muted)]">{d.task}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
