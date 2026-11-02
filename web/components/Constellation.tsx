"use client";

/**
 * The Command Constellation: the Orchestrator as a chrome core, its sub-agents
 * orbiting it on SVG connectors.
 *
 * Rendering budget: the whole thing is SVG + CSS transforms. The slow drift is
 * one rotate on the orbit wrapper (with a counter-rotate on each node so labels
 * stay upright), pulses are stroke-dashoffset animations, and React re-renders
 * only when an agent's state actually changes, never per frame.
 *
 * Reachability: every node is a real button (Tab / arrows), has an ARIA label
 * that says the state in words, and the drift pauses while the pointer or
 * focus is on the stage, so a node never moves out from under a click.
 */

import { useMemo, type KeyboardEvent } from "react";

import {
  ORCHESTRATOR,
  STATE_LABEL,
  needsAttention,
  type AgentStateName,
  type AgentView,
  type AgentsModel,
} from "@/lib/agents";
import { roveIndex } from "@/lib/panes";
import {
  STATE_COLOUR,
  STATE_TEXT,
  StateGlyph,
  StatePill,
  StateRing,
} from "./agentVisual";

const ORBIT_RADIUS = 34; // % of the stage side
const DRIFT_SECONDS = 180;

function polar(index: number, count: number): { x: number; y: number } {
  const angle = (-90 + (360 / Math.max(count, 1)) * index) * (Math.PI / 180);
  return { x: 50 + ORBIT_RADIUS * Math.cos(angle), y: 50 + ORBIT_RADIUS * Math.sin(angle) };
}

function ariaFor(view: AgentView): string {
  const bits = [`${view.name}, ${STATE_LABEL[view.state]}`];
  if (view.state === "working" && view.tool) bits.push(`running ${view.tool}`);
  if (view.task && view.state !== "idle") bits.push(view.task);
  return `${bits.join(". ")}. Open details`;
}

/* -- one sub-agent node ----------------------------------------------- */

function AgentNode({
  view,
  onOpen,
  onReview,
}: {
  view: AgentView;
  onOpen: (name: string) => void;
  onReview: () => void;
}) {
  const size = 64;
  const state = view.state;
  const hot = state === "working";

  return (
    <div className="relative flex w-28 flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        {/* Ambient light behind the active agent: a radial gradient, not a
            blur filter, so it costs nothing on a phone. */}
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-8 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(212,21,20,0.5) 0%, rgba(212,21,20,0.12) 45%, transparent 70%)",
            opacity: hot ? 1 : 0,
            transition: "opacity var(--motion-slow) var(--ease)",
          }}
        />
        {state === "idle" && (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-2 rounded-full"
            style={{
              background: "radial-gradient(circle, rgba(205,203,202,0.16), transparent 70%)",
              animation: "breathe var(--loop-breathe) ease-in-out infinite",
            }}
          />
        )}
        {state === "done" && (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-1 rounded-full"
            style={{ animation: "flash-done 900ms var(--ease-out) 1" }}
          />
        )}
        <button
          type="button"
          data-agent-node={view.name}
          aria-label={ariaFor(view)}
          onClick={() => onOpen(view.name)}
          className="absolute inset-0 flex items-center justify-center rounded-full"
          style={{
            color: STATE_TEXT[state],
            background: hot
              ? "radial-gradient(circle at 50% 40%, #6e0d0c 0%, #2a0505 62%, #120203 100%)"
              : "radial-gradient(circle at 35% 30%, #2a2b33 0%, #121217 70%)",
            boxShadow: hot ? "var(--glow)" : "inset 0 1px 0 var(--glass-highlight)",
            transition: "background var(--motion-slow) var(--ease), box-shadow var(--motion-slow) var(--ease)",
          }}
        >
          <StateRing state={state} size={size} />
          <StateGlyph state={state} size={22} />
        </button>
        {state === "waiting" && (
          <button
            type="button"
            aria-label={`Review the confirmation ${view.name} is waiting on`}
            onClick={onReview}
            className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full text-[length:var(--text-sm)] font-bold"
            style={{
              background: "var(--coral)",
              color: "#1a0602",
              animation: "halo 1.6s ease-in-out infinite",
              minWidth: 0,
              minHeight: 0,
            }}
          >
            !
          </button>
        )}
      </div>

      <div className="mt-2 text-center leading-tight">
        <div
          className="text-[length:var(--text-xs)] font-semibold uppercase"
          style={{ letterSpacing: "var(--tracking-label)", color: "var(--text)" }}
        >
          {view.name}
        </div>
        <div
          className="mt-0.5 max-w-28 truncate text-[length:var(--text-xs)]"
          style={{ color: STATE_TEXT[state] }}
        >
          {state === "working" && view.tool ? view.tool : STATE_LABEL[state]}
        </div>
      </div>
    </div>
  );
}

/* -- the core --------------------------------------------------------- */

function Core({
  view,
  onOpen,
  onReview,
}: {
  view: AgentView;
  onOpen: (name: string) => void;
  onReview: () => void;
}) {
  const size = 112;
  const state = view.state;
  // The inner laser glow scales with how live the core is.
  const glow = state === "working" ? 1 : state === "thinking" ? 0.55 : state === "waiting" ? 0.7 : 0.18;

  return (
    <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-10 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgba(212,21,20,0.55) 0%, rgba(212,21,20,0.14) 42%, transparent 70%)",
            opacity: state === "working" ? 1 : state === "thinking" ? 0.45 : 0.15,
            transition: "opacity var(--motion-slow) var(--ease)",
            animation: state === "idle" ? "breathe var(--loop-breathe) ease-in-out infinite" : undefined,
          }}
        />
        <button
          type="button"
          data-agent-node={ORCHESTRATOR}
          aria-label={ariaFor(view)}
          onClick={() => onOpen(ORCHESTRATOR)}
          className="absolute inset-0 rounded-full"
          style={{
            // Liquid chrome sphere.
            background:
              "radial-gradient(circle at 34% 26%, #ffffff 0%, #cdcbca 22%, #7b8088 52%, #2a2c33 80%, #101114 100%)",
            boxShadow:
              "inset 0 -6px 14px rgba(0,0,0,0.55), inset 0 2px 3px rgba(255,255,255,0.7), 0 10px 30px rgba(0,0,0,0.6)",
            color: "var(--obsidian)",
          }}
        >
          {/* The laser heart shows through a lens in the chrome. */}
          <span
            aria-hidden
            className="absolute left-1/2 top-1/2 h-[46%] w-[46%] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              background:
                "radial-gradient(circle, #ff6a5f 0%, #d41514 38%, #5a0707 78%, #1a0202 100%)",
              opacity: 0.25 + glow * 0.75,
              boxShadow: `0 0 ${10 + glow * 22}px rgba(212,21,20,${0.3 + glow * 0.6})`,
              transition: "opacity var(--motion-slow) var(--ease), box-shadow var(--motion-slow) var(--ease)",
            }}
          />
          <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ color: "#fff" }}>
            <StateGlyph state={state} size={20} />
          </span>
          <StateRing state={state} size={size} />
        </button>
        {state === "waiting" && (
          <button
            type="button"
            aria-label="Review the confirmation Kravein is waiting on"
            onClick={onReview}
            className="absolute -right-1 top-0 flex h-8 w-8 items-center justify-center rounded-full text-[length:var(--text-base)] font-bold"
            style={{
              background: "var(--coral)",
              color: "#1a0602",
              animation: "halo 1.6s ease-in-out infinite",
              minWidth: 0,
              minHeight: 0,
            }}
          >
            !
          </button>
        )}
      </div>
      <div className="mt-3 text-center">
        <div className="wordmark text-[length:var(--text-sm)] font-semibold uppercase">Orchestrator</div>
        <div className="mt-0.5 text-[length:var(--text-xs)]" style={{ color: STATE_TEXT[state] }}>
          {state === "working" && view.tool ? view.tool : STATE_LABEL[state]}
        </div>
      </div>
    </div>
  );
}

/* -- connectors ------------------------------------------------------- */

function connectorStyle(state: AgentStateName): {
  stroke: string;
  width: number;
  opacity: number;
  dash?: string;
} {
  switch (state) {
    case "working":
      return { stroke: "var(--laser)", width: 0.6, opacity: 0.95 };
    case "thinking":
      return { stroke: "var(--chrome)", width: 0.45, opacity: 0.6 };
    case "waiting":
      return { stroke: "var(--coral)", width: 0.5, opacity: 0.9, dash: "1.2 1.2" };
    case "done":
      return { stroke: "var(--state-done)", width: 0.5, opacity: 0.9 };
    case "failed":
    case "declined":
      return { stroke: "var(--state-failed)", width: 0.5, opacity: 0.9, dash: "0.6 1.6" };
    default:
      return { stroke: "var(--steel)", width: 0.3, opacity: 0.35 };
  }
}

function Connector({ view, x, y }: { view: AgentView; x: number; y: number }) {
  const style = connectorStyle(view.state);
  const live = view.state === "working" || view.state === "thinking";
  const line = { x1: 50, y1: 50, x2: x, y2: y };
  return (
    <g>
      <line
        {...line}
        stroke={style.stroke}
        strokeWidth={style.width}
        strokeOpacity={style.opacity}
        strokeDasharray={style.dash}
        strokeLinecap="round"
      />
      {live && (
        // Continuous light moving out while the agent works.
        <line
          {...line}
          pathLength={100}
          stroke={view.state === "working" ? "var(--accent)" : "var(--chrome)"}
          strokeWidth={style.width + 0.5}
          strokeLinecap="round"
          strokeDasharray="10 204"
          style={{ animation: "pulse-out var(--loop-pulse) linear infinite" }}
        />
      )}
      {view.outSeq > 0 && (
        // The hand-over: a bright pulse out to the agent, once per delegation.
        <line
          key={`out-${view.outSeq}`}
          {...line}
          pathLength={100}
          stroke="#ffffff"
          strokeWidth={1}
          strokeLinecap="round"
          strokeDasharray="14 200"
          style={{ animation: "pulse-out 900ms var(--ease) 1 both" }}
        />
      )}
      {view.backSeq > 0 && (
        // The report: a pulse travelling back to the core.
        <line
          key={`back-${view.backSeq}`}
          {...line}
          pathLength={100}
          stroke={view.state === "failed" || view.state === "declined" ? "var(--state-failed)" : "#ffffff"}
          strokeWidth={1}
          strokeLinecap="round"
          strokeDasharray="14 200"
          style={{ animation: "pulse-back 900ms var(--ease) 1 both" }}
        />
      )}
    </g>
  );
}

/* -- the stage -------------------------------------------------------- */

export function Constellation({
  model,
  voiceThinking,
  onOpenAgent,
  onReview,
}: {
  model: AgentsModel;
  /** Live voice is mid-answer; the Orchestrator shows it even with no frames. */
  voiceThinking?: boolean;
  onOpenAgent: (name: string) => void;
  onReview: () => void;
}) {
  const subs = useMemo(
    () => model.order.filter((n) => n !== ORCHESTRATOR).map((n) => model.agents[n]),
    [model.order, model.agents],
  );
  const core0 = model.agents[ORCHESTRATOR];
  const core: AgentView =
    voiceThinking && core0.state === "idle" ? { ...core0, state: "thinking" } : core0;

  const allIdle = core.state === "idle" && subs.every((s) => s.state === "idle");
  const play = allIdle ? "running" : "paused";

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const nodes = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>("[data-agent-node]"),
    );
    const at = nodes.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    const next = roveIndex(at, event.key, nodes.length);
    if (next === null) return;
    event.preventDefault();
    nodes[next].focus();
  };

  const lastReport = useMemo(() => {
    const withReport = subs.filter((s) => s.report);
    return withReport.sort((a, b) => b.since - a.since)[0];
  }, [subs]);

  return (
    <section
      aria-label="Agents"
      className="relative hidden shrink-0 items-center justify-center gap-6 px-6 sm:flex"
      style={{ height: "clamp(15rem, 36dvh, 21rem)" }}
    >
      {/* Left readout: what the Orchestrator is doing right now. */}
      <div className="hidden min-w-0 flex-1 justify-end lg:flex">
        <div className="w-64 text-right">
          <div className="eyebrow">Now</div>
          <div className="mt-1">
            <StatePill state={core.state} />
          </div>
          <p
            className="mt-2 line-clamp-3 text-[length:var(--text-sm)]"
            style={{ color: "var(--text)" }}
          >
            {core.task && core.state !== "idle" ? core.task : "Standing by."}
          </p>
          {core.state === "working" && core.tool && (
            <p className="mt-1 truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--accent)]">
              {core.tool}
            </p>
          )}
        </div>
      </div>

      <div
        className="constellation relative aspect-square h-full shrink-0"
        onKeyDown={onKeyDown}
      >
        <div
          className="drift absolute inset-0"
          style={{ animation: `orbit-drift ${DRIFT_SECONDS}s linear infinite`, animationPlayState: play }}
        >
          <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden>
            {subs.map((view, i) => {
              const { x, y } = polar(i, subs.length);
              return <Connector key={view.name} view={view} x={x} y={y} />;
            })}
          </svg>
          {subs.map((view, i) => {
            const { x, y } = polar(i, subs.length);
            return (
              <div
                key={view.name}
                className="absolute -translate-x-1/2 -translate-y-[calc(50%+0.75rem)]"
                style={{ left: `${x}%`, top: `${y}%` }}
              >
                <div
                  className="drift"
                  style={{
                    animation: `orbit-counter ${DRIFT_SECONDS}s linear infinite`,
                    animationPlayState: play,
                  }}
                >
                  <AgentNode view={view} onOpen={onOpenAgent} onReview={onReview} />
                </div>
              </div>
            );
          })}
        </div>
        <Core view={core} onOpen={onOpenAgent} onReview={onReview} />
      </div>

      {/* Right readout: the latest report back from a sub-agent. */}
      <div className="hidden min-w-0 flex-1 lg:flex">
        <div className="w-64">
          <div className="eyebrow">Last report</div>
          {lastReport?.report ? (
            <>
              <div className="mt-1 flex items-center gap-2">
                <span
                  className="font-semibold uppercase text-[length:var(--text-xs)]"
                  style={{ letterSpacing: "var(--tracking-label)" }}
                >
                  {lastReport.name}
                </span>
                <span
                  className="rounded-[var(--radius-full)] border px-2 text-[length:var(--text-xs)]"
                  style={{
                    color: STATE_TEXT[lastReport.state === "idle" ? "done" : lastReport.state],
                    borderColor: "color-mix(in srgb, currentColor 40%, transparent)",
                  }}
                >
                  {lastReport.report.outcome}
                </span>
              </div>
              <p className="mt-2 line-clamp-3 text-[length:var(--text-sm)] text-[var(--text-muted)]">
                {lastReport.report.summary}
              </p>
            </>
          ) : (
            <p className="mt-2 text-[length:var(--text-sm)] text-[var(--text-muted)]">
              No sub-agent has reported yet.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

/* -- phone: a compact strip ------------------------------------------- */

/**
 * At phone width the constellation collapses to one row of agents: same
 * buttons, same states, no orbit. Four short columns fit 360px without a
 * sideways scroll.
 */
export function AgentStrip({
  model,
  voiceThinking,
  onOpenAgent,
  onReview,
}: {
  model: AgentsModel;
  voiceThinking?: boolean;
  onOpenAgent: (name: string) => void;
  onReview: () => void;
}) {
  const views = model.order.map((n) => model.agents[n]);
  return (
    <section
      aria-label="Agents"
      className="grid shrink-0 gap-1 px-2 py-2 sm:hidden"
      style={{ gridTemplateColumns: `repeat(${views.length}, minmax(0, 1fr))` }}
    >
      {views.map((raw) => {
        const view =
          raw.name === ORCHESTRATOR && voiceThinking && raw.state === "idle"
            ? { ...raw, state: "thinking" as const }
            : raw;
        const hot = view.state === "working";
        return (
          <div key={view.name} className="relative">
            <button
              type="button"
              data-agent-node={view.name}
              aria-label={ariaFor(view)}
              onClick={() => onOpenAgent(view.name)}
              className="flex w-full flex-col items-center gap-1 rounded-[var(--radius)] px-1 py-1.5"
              style={{
                color: STATE_TEXT[view.state],
                background: hot ? "var(--accent-dim)" : "transparent",
                boxShadow: hot ? "var(--glow-soft)" : undefined,
              }}
            >
              <span
                className="relative flex h-9 w-9 items-center justify-center rounded-full"
                style={{ border: `1.5px solid ${STATE_COLOUR[view.state]}` }}
              >
                <StateGlyph state={view.state} size={16} />
              </span>
              <span
                className="max-w-full truncate text-[length:var(--text-xs)] font-semibold uppercase"
                style={{ letterSpacing: "0.06em", color: "var(--text)" }}
              >
                {view.name === ORCHESTRATOR ? "Core" : view.name}
              </span>
              <span className="max-w-full truncate text-[length:var(--text-xs)]">
                {STATE_LABEL[view.state]}
              </span>
            </button>
            {needsAttention(view) && view.state === "waiting" && (
              <button
                type="button"
                aria-label={`Review the confirmation ${view.name} is waiting on`}
                onClick={onReview}
                className="absolute right-1 top-0 flex h-6 w-6 items-center justify-center rounded-full text-[length:var(--text-xs)] font-bold"
                style={{ background: "var(--coral)", color: "#1a0602", minWidth: 0, minHeight: 0 }}
              >
                !
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}
