"use client";

/**
 * The Command Constellation: the Orchestrator as a liquid-chrome orb, its
 * sub-agents as bezelled glass nodes on curved connectors.
 *
 * Rendering budget: SVG + CSS only. The slow swing is one rotate on the orbit
 * group (each node counter-rotates so labels stay upright), pulses are
 * stroke-dashoffset animations, the idle flow is a dashed offset loop, and
 * React re-renders only when an agent's state changes, never per frame.
 *
 * Sizing: the wrapper is a size container, so the stage and every node scale
 * with the space the layout gives it (`--s`, `--node`, `--orb`), instead of
 * being a fixed 330px island in an empty canvas.
 *
 * Reachability: every node is a real button (Tab / arrows), has an ARIA label
 * that says the state in words, and the swing pauses while the pointer or
 * focus is on the stage, so a node never moves out from under a click.
 */

import { useMemo, type CSSProperties, type KeyboardEvent } from "react";

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

const ORBIT_RADIUS = 38; // % of the stage side
/** The core sits below the middle: the arc of agents fills the space above it. */
const CY = 56;
const SWING_SECONDS = 46;

/**
 * Sub-agents sit on the upper arc (-160 deg .. -20 deg), so the core's own
 * label has the lower half to itself. The swing is a few degrees each way,
 * not a full turn: it reads as an orbit but a node never crosses the label.
 */
function polar(index: number, count: number): { x: number; y: number } {
  const deg = count <= 1 ? -90 : -160 + (140 * index) / (count - 1);
  const angle = deg * (Math.PI / 180);
  return { x: 50 + ORBIT_RADIUS * Math.cos(angle), y: CY + ORBIT_RADIUS * Math.sin(angle) };
}

/** A shallow arc from the core to a node, bulging upward and outward. */
function arc(x: number, y: number): string {
  const dx = x - 50;
  const dy = y - CY;
  const mx = 50 + dx / 2;
  const my = CY + dy / 2;
  let px = -dy;
  let py = dx;
  if (py > 0) {
    px = -px;
    py = -py;
  }
  const len = Math.hypot(dx, dy) || 1;
  const bulge = 0.2;
  return `M 50 ${CY} Q ${mx + (px / len) * len * bulge} ${my + (py / len) * len * bulge} ${x} ${y}`;
}

function ariaFor(view: AgentView): string {
  const bits = [`${view.name}, ${STATE_LABEL[view.state]}`];
  if (view.state === "working" && view.tool) bits.push(`running ${view.tool}`);
  if (view.task && view.state !== "idle") bits.push(view.task);
  return `${bits.join(". ")}. Open details`;
}

/* -- one sub-agent node ----------------------------------------------- */

function WaitingBadge({ name, onReview, big }: { name: string; onReview: () => void; big?: boolean }) {
  return (
    <button
      type="button"
      aria-label={`Review the confirmation ${name} is waiting on`}
      onClick={onReview}
      className={`absolute -right-1.5 -top-1.5 flex items-center justify-center rounded-full font-bold ${
        big ? "h-8 w-8 text-[length:var(--text-base)]" : "h-7 w-7 text-[length:var(--text-sm)]"
      }`}
      style={{
        background: "var(--coral)",
        color: "var(--ink-on-coral)",
        animation: "halo 1.6s ease-in-out infinite",
        minWidth: 0,
        minHeight: 0,
      }}
    >
      !
    </button>
  );
}

function AgentNode({
  view,
  onOpen,
  onReview,
}: {
  view: AgentView;
  onOpen: (name: string) => void;
  onReview: () => void;
}) {
  const state = view.state;
  const hot = state === "working";

  return (
    <div className="relative flex flex-col items-center" style={{ width: "calc(var(--node) * 1.7)" }}>
      <div className="relative" style={{ width: "var(--node)", height: "var(--node)" }}>
        {/* Volumetric light behind the active agent: a radial gradient, not a
            blur filter, so it costs nothing on a phone. */}
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-10 rounded-full"
          style={{
            background: "var(--halo-hot)",
            opacity: hot ? 1 : 0,
            transition: "opacity var(--motion-slow) var(--ease)",
          }}
        />
        {state === "idle" && (
          <span
            aria-hidden
            className="pointer-events-none absolute -inset-3 rounded-full"
            style={{
              background: "var(--halo-idle)",
              animation: "breathe var(--loop-breathe) ease-in-out infinite",
            }}
          />
        )}
        {state === "done" && (
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-full"
            style={{ animation: "flash-done 900ms var(--ease-out) 1" }}
          />
        )}
        <button
          type="button"
          data-agent-node={view.name}
          aria-label={ariaFor(view)}
          onClick={() => onOpen(view.name)}
          className="absolute inset-0 rounded-full p-[4%]"
          style={{
            // Chrome bezel; it warms to laser while the agent works.
            background: hot ? "var(--bezel-hot)" : "var(--bezel-chrome)",
            boxShadow: hot ? "var(--glow), var(--node-shadow)" : "var(--node-shadow)",
            transition: "box-shadow var(--motion-slow) var(--ease)",
          }}
        >
          <span
            className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-full"
            style={{
              background: hot ? "var(--dome-hot)" : "var(--dome-glass)",
              color: STATE_TEXT[state],
              boxShadow: "inset 0 2px 6px rgb(0 0 0 / 0.7)",
            }}
          >
            {/* The gloss arc of a glass dome. */}
            <span
              aria-hidden
              className="pointer-events-none absolute left-[14%] right-[14%] top-[3%] h-[40%] rounded-full"
              style={{ background: "var(--dome-gloss)" }}
            />
            <StateGlyph state={state} size={22} />
          </span>
        </button>
        <span aria-hidden className="pointer-events-none absolute -inset-[5px]">
          <StateRing state={state} size="100%" />
        </span>
        {state === "waiting" && <WaitingBadge name={view.name} onReview={onReview} />}
      </div>

      <div className="mt-2.5 text-center leading-tight">
        <div
          className="text-[length:var(--text-xs)] font-semibold uppercase"
          style={{ letterSpacing: "var(--tracking-label)", color: "var(--text)" }}
        >
          {view.name}
        </div>
        <div
          className="mx-auto mt-0.5 truncate text-[length:var(--text-xs)]"
          style={{ color: STATE_TEXT[state], maxWidth: "calc(var(--node) * 1.7)" }}
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
  const state = view.state;
  // The laser heart scales with how live the core is.
  const glow = state === "working" ? 1 : state === "thinking" ? 0.55 : state === "waiting" ? 0.7 : 0.18;

  return (
    <div
      className="absolute left-1/2 flex -translate-x-1/2 flex-col items-center"
      // Centre the orb (not orb + label) on the connector hub.
      style={{ top: `${CY}%`, marginTop: "calc(var(--orb) / -2)" }}
    >
      <div className="relative" style={{ width: "var(--orb)", height: "var(--orb)" }}>
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-[42%] rounded-full"
          style={{
            background: "var(--halo-core)",
            opacity: state === "working" ? 1 : state === "thinking" ? 0.5 : 0.18,
            transition: "opacity var(--motion-slow) var(--ease)",
            animation: state === "idle" ? "breathe var(--loop-breathe) ease-in-out infinite" : undefined,
          }}
        />
        <button
          type="button"
          data-agent-node={ORCHESTRATOR}
          aria-label={ariaFor(view)}
          onClick={() => onOpen(ORCHESTRATOR)}
          className="absolute inset-0 overflow-hidden rounded-full"
          style={{ background: "var(--sphere-chrome)", boxShadow: "var(--orb-rim), 0 14px 34px rgb(0 0 0 / 0.65)" }}
        >
          {/* Reflected horizon, then the laser heart showing through a lens,
              then the specular highlight on top. */}
          <span aria-hidden className="absolute inset-0 rounded-full" style={{ background: "var(--orb-horizon)" }} />
          <span
            aria-hidden
            className="absolute left-1/2 top-1/2 h-[44%] w-[44%] -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              background: "var(--sphere-lens)",
              opacity: 0.3 + glow * 0.7,
              boxShadow: `0 0 ${10 + glow * 26}px rgb(var(--laser-rgb) / ${0.3 + glow * 0.6})`,
              transition: "opacity var(--motion-slow) var(--ease), box-shadow var(--motion-slow) var(--ease)",
            }}
          />
          <span aria-hidden className="absolute inset-0 rounded-full" style={{ background: "var(--orb-specular)" }} />
          <span
            className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ color: "var(--white)" }}
          >
            <StateGlyph state={state} size={22} />
          </span>
        </button>
        <span aria-hidden className="pointer-events-none absolute -inset-[5px]">
          <StateRing state={state} size="100%" />
        </span>
        {state === "waiting" && <WaitingBadge name="Kravein" onReview={onReview} big />}
        {/* Soft floor reflection: the orb's glow on the surface it sits above. */}
        <span
          aria-hidden
          className="pointer-events-none absolute left-1/2 top-[102%] h-[26%] w-[130%] -translate-x-1/2"
          style={{
            background: "var(--floor-glow)",
            opacity: state === "working" ? 1 : state === "thinking" ? 0.5 : 0.16,
            transition: "opacity var(--motion-slow) var(--ease)",
          }}
        />
      </div>
      <div className="relative mt-[calc(var(--orb)*0.2)] text-center">
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
      return { stroke: "var(--laser)", width: 0.7, opacity: 1 };
    case "thinking":
      return { stroke: "var(--chrome)", width: 0.5, opacity: 0.7 };
    case "waiting":
      return { stroke: "var(--coral)", width: 0.55, opacity: 0.95, dash: "1.4 1.2" };
    case "done":
      return { stroke: "var(--state-done)", width: 0.55, opacity: 0.95 };
    case "failed":
    case "declined":
      return { stroke: "var(--state-failed)", width: 0.55, opacity: 0.95, dash: "0.7 1.7" };
    default:
      // Idle: a faint steel dashed flow, so every link is visible at rest.
      return { stroke: "var(--steel)", width: 0.32, opacity: 0.55, dash: "1.1 1.7" };
  }
}

function Connector({ view, x, y }: { view: AgentView; x: number; y: number }) {
  const style = connectorStyle(view.state);
  const live = view.state === "working" || view.state === "thinking";
  const d = arc(x, y);
  return (
    <g>
      {view.state === "working" && (
        // A wide, faint stroke under the line: the laser's glow on the glass.
        <path d={d} fill="none" stroke="var(--laser)" strokeWidth={2.4} strokeOpacity={0.22} strokeLinecap="round" />
      )}
      <path
        d={d}
        fill="none"
        stroke={style.stroke}
        strokeWidth={style.width}
        strokeOpacity={style.opacity}
        strokeDasharray={style.dash}
        strokeLinecap="round"
        style={view.state === "idle" ? { animation: "dash-flow 18s linear infinite" } : undefined}
      />
      {live && (
        // Continuous light moving out while the agent works.
        <path
          d={d}
          fill="none"
          pathLength={100}
          stroke={view.state === "working" ? "var(--accent)" : "var(--chrome)"}
          strokeWidth={style.width + 0.6}
          strokeLinecap="round"
          strokeDasharray="10 204"
          style={{ animation: "pulse-out var(--loop-pulse) linear infinite" }}
        />
      )}
      {view.outSeq > 0 && (
        // The hand-over: a bright pulse out to the agent, once per delegation.
        <path
          key={`out-${view.outSeq}`}
          d={d}
          fill="none"
          pathLength={100}
          stroke="var(--white)"
          strokeWidth={1.1}
          strokeLinecap="round"
          strokeDasharray="14 200"
          style={{ animation: "pulse-out 900ms var(--ease) 1 both" }}
        />
      )}
      {view.backSeq > 0 && (
        // The report: a pulse travelling back to the core.
        <path
          key={`back-${view.backSeq}`}
          d={d}
          fill="none"
          pathLength={100}
          stroke={view.state === "failed" || view.state === "declined" ? "var(--state-failed)" : "var(--white)"}
          strokeWidth={1.1}
          strokeLinecap="round"
          strokeDasharray="14 200"
          style={{ animation: "pulse-back 900ms var(--ease) 1 both" }}
        />
      )}
    </g>
  );
}

/* -- glass cards ------------------------------------------------------ */

function GlassCard({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="glass-card min-w-0 flex-1 rounded-[var(--radius-lg)] px-4 py-3"
      style={{ maxWidth: "22rem" }}
    >
      {children}
    </div>
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
  const anyHot = core.state === "working" || subs.some((s) => s.state === "working");

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

  const stage: CSSProperties & Record<string, string> = {
    "--s": "min(100cqw, calc(100cqh - 6.75rem))",
    "--node": "clamp(64px, calc(var(--s) * 0.14), 96px)",
    "--orb": "clamp(112px, calc(var(--s) * 0.26), 176px)",
    width: "var(--s)",
    height: "var(--s)",
  };

  return (
    <section
      aria-label="Agents"
      className="relative hidden h-full min-h-0 w-full sm:block"
      style={{ containerType: "size" }}
    >
      <div className="absolute inset-x-0 top-0 flex justify-center">
        <div className="constellation relative" style={stage} onKeyDown={onKeyDown}>
          {/* Atmosphere: a forest-tinted pool for depth, and laser light that
              fills in behind whichever agent is working. */}
          <span aria-hidden className="pointer-events-none absolute inset-[-12%]" style={{ background: "var(--stage-haze)" }} />
          <span
            aria-hidden
            className="pointer-events-none absolute inset-[-8%]"
            style={{
              background: "var(--halo-core)",
              opacity: anyHot ? 0.55 : 0,
              transition: "opacity var(--motion-slow) var(--ease)",
            }}
          />
          {/* Orbit guides, static: where the swing goes. */}
          <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible" aria-hidden>
            <circle cx="50" cy={CY} r={ORBIT_RADIUS} fill="none" stroke="var(--steel)" strokeOpacity="0.18" strokeWidth="0.25" strokeDasharray="0.8 2.2" />
            <circle cx="50" cy={CY} r={ORBIT_RADIUS * 0.58} fill="none" stroke="var(--steel)" strokeOpacity="0.1" strokeWidth="0.2" strokeDasharray="0.6 2.6" />
          </svg>

          <div
            className="drift absolute inset-0"
            style={{
              transformOrigin: `50% ${CY}%`,
              animation: `orbit-swing ${SWING_SECONDS}s ease-in-out infinite alternate`,
              animationPlayState: play,
            }}
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
                  className="absolute -translate-x-1/2"
                  // The point is the node circle's centre, so the connector
                  // ends under the bezel, not under its label.
                  style={{ left: `${x}%`, top: `${y}%`, marginTop: "calc(var(--node) / -2)" }}
                >
                  <div
                    className="drift"
                    style={{
                      animation: `orbit-swing-back ${SWING_SECONDS}s ease-in-out infinite alternate`,
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
      </div>

      {/* Two glass cards under the stage: what the core is doing, and the last
          report back from a sub-agent. */}
      <div className="absolute inset-x-0 bottom-0 flex justify-center gap-3 px-3 pb-1">
        <GlassCard label="Now">
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">Now</span>
            <StatePill state={core.state} compact />
          </div>
          <p className="mt-1.5 line-clamp-2 text-[length:var(--text-sm)] text-[var(--text)]">
            {core.task && core.state !== "idle" ? core.task : "Standing by."}
          </p>
        </GlassCard>
        <GlassCard label="Last report">
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">Last report</span>
            {lastReport?.report && (
              <span className="truncate text-[length:var(--text-xs)] font-semibold uppercase" style={{ letterSpacing: "var(--tracking-label)" }}>
                {lastReport.name} · {lastReport.report.outcome}
              </span>
            )}
          </div>
          <p className="mt-1.5 line-clamp-2 text-[length:var(--text-sm)] text-[var(--text-muted)]">
            {lastReport?.report ? lastReport.report.summary : "No sub-agent has reported yet."}
          </p>
        </GlassCard>
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
                style={{ border: `1.5px solid ${STATE_COLOUR[view.state]}`, background: "var(--dome-glass)" }}
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
                style={{ background: "var(--coral)", color: "var(--ink-on-coral)", minWidth: 0, minHeight: 0 }}
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
