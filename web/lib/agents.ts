/**
 * The agent-state store behind the constellation.
 *
 * One pure reducer. Today the state is DERIVED from frames the server already
 * sends (turn start/end, tool frames, delegate calls and their reports,
 * confirmations). Engine #146 adds an authoritative `agent_state` frame and
 * `GET /api/agents{,/state}`; when one arrives it wins for that agent and the
 * derivation stops touching its state (steps and delegations are still
 * recorded from tool frames, which `agent_state` does not carry).
 *
 * No React in here, so frame sequences are testable as plain data.
 */

export type AgentStateName =
  | "idle"
  | "thinking"
  | "working"
  | "waiting"
  | "done"
  | "failed"
  | "declined";

export const ORCHESTRATOR = "orchestrator";

export interface Report {
  outcome: string;
  summary: string;
}

export interface Step {
  id: string;
  tool: string;
  /** Short, already-trimmed argument preview; never the raw arguments. */
  args: string;
  status: "running" | "ok" | "failed" | "lost";
  startedAt: number;
  durationMs?: number;
}

export interface Delegation {
  id: string;
  task: string;
  startedAt: number;
  endedAt?: number;
  outcome?: string;
}

export interface AgentView {
  name: string;
  role: "orchestrator" | "sub";
  purpose: string;
  model: string;
  /** Tool count from /api/agents; null until that endpoint answers. */
  tools: number | null;
  state: AgentStateName;
  task: string;
  tool: string;
  delegation: string;
  since: number;
  report: Report | null;
  steps: Step[];
  delegations: Delegation[];
  /** Bumped when the Orchestrator hands this agent a task / receives its
   *  report, so the connector can restart its travelling pulse. */
  outSeq: number;
  backSeq: number;
  /** State to return to when a confirmation is answered. */
  resume: AgentStateName;
}

export interface AgentsModel {
  agents: Record<string, AgentView>;
  /** Display order: the Orchestrator, then sub-agents as first seen. */
  order: string[];
  /** An `agent_state` frame has been seen: derivation stops setting state. */
  authoritative: boolean;
  /** `orchestrator_enabled` from /api/agents; null until it answers. */
  orchestratorEnabled: boolean | null;
}

/** The profiles that ship today, shown until /api/agents answers. */
export const FALLBACK_PROFILES: { name: string; purpose: string }[] = [
  { name: "system-ops", purpose: "Deep system work: shell, processes, services, packages, logs, files." },
  { name: "research", purpose: "Research, comparison, planning and documents. Never changes the machine." },
  { name: "reviewer", purpose: "Reviews a finished change and reports what is wrong with it." },
];

const MAX_STEPS = 40;
const MAX_DELEGATIONS = 8;
const TASK_MAX = 200;

function blank(name: string, role: "orchestrator" | "sub", purpose = ""): AgentView {
  return {
    name, role, purpose, model: "", tools: null,
    state: "idle", task: "", tool: "", delegation: "", since: 0,
    report: null, steps: [], delegations: [],
    outSeq: 0, backSeq: 0, resume: "thinking",
  };
}

export function initialModel(): AgentsModel {
  const agents: Record<string, AgentView> = {
    [ORCHESTRATOR]: blank(
      ORCHESTRATOR, "orchestrator",
      "Talks to you, plans, and hands longer work to the specialists.",
    ),
  };
  const order = [ORCHESTRATOR];
  for (const p of FALLBACK_PROFILES) {
    agents[p.name] = blank(p.name, "sub", p.purpose);
    order.push(p.name);
  }
  return { agents, order, authoritative: false, orchestratorEnabled: null };
}

/* -- events ---------------------------------------------------------------
 * Frames from the socket, plus the few things only the page knows. */

export interface FrameLike {
  t: string;
  [key: string]: unknown;
}

export type AgentEvent =
  | FrameLike
  | { t: "ask_sent"; text: string }
  | { t: "settle" }
  | { t: "disconnected" }
  | { t: "profiles"; agents: ProfileInfo[]; orchestratorEnabled?: boolean }
  | { t: "snapshot"; states: AgentStateFrame[] };

export interface ProfileInfo {
  name: string;
  purpose?: string;
  model?: string;
  tools?: number;
  role?: "orchestrator" | "sub";
}

export interface AgentStateFrame {
  agent: string;
  state: AgentStateName;
  task?: string;
  tool?: string;
  delegation?: string;
  since?: number;
  report?: Report;
}

const STATES: ReadonlySet<string> = new Set([
  "idle", "thinking", "working", "waiting", "done", "failed", "declined",
]);

const clip = (text: unknown, max = TASK_MAX): string => {
  const s = typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
};

function argsPreview(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  return clip(
    Object.entries(args as Record<string, unknown>)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(" "),
    80,
  );
}

function ensure(model: AgentsModel, name: string): AgentsModel {
  if (model.agents[name]) return model;
  return {
    ...model,
    agents: { ...model.agents, [name]: blank(name, "sub") },
    order: [...model.order, name],
  };
}

function patch(
  model: AgentsModel,
  name: string,
  change: Partial<AgentView> | ((view: AgentView) => Partial<AgentView>),
): AgentsModel {
  const next = ensure(model, name);
  const view = next.agents[name];
  const delta = typeof change === "function" ? change(view) : change;
  return { ...next, agents: { ...next.agents, [name]: { ...view, ...delta } } };
}

/** Set the derived state, unless the server is the authority. */
function derive(
  model: AgentsModel,
  name: string,
  state: AgentStateName,
  now: number,
  extra: Partial<AgentView> = {},
): AgentsModel {
  // The server owns state, tool, report and pulses once it speaks; only our own
  // bookkeeping (what to resume to) is still ours.
  if (model.authoritative) {
    return "resume" in extra ? patch(model, name, { resume: extra.resume }) : model;
  }
  return patch(model, name, { state, since: now, ...extra });
}

/** `[system-ops report #ab12cd34] outcome: done` + `Summary: ...` */
export function parseDelegateReport(
  text: string,
): { agent: string; id: string; outcome: string; summary: string } | null {
  const head = /\[([a-z][a-z0-9_-]*) report #([0-9a-z]+)\] outcome: ([a-z-]+)/.exec(text);
  if (!head) return null;
  const summary = /^Summary: (.*)$/m.exec(text)?.[1] ?? "";
  return { agent: head[1], id: head[2], outcome: head[3], summary: clip(summary, 600) };
}

export function outcomeState(outcome: string): AgentStateName {
  switch (outcome) {
    case "done":
    case "partial":
      return "done";
    case "declined":
    case "needs-approval":
      return "declined";
    default:
      return "failed";
  }
}

/** A confirmation's action is prefixed `name: ` when a sub-agent asked. */
function confirmOwner(model: AgentsModel, action: unknown): string {
  const m = /^([a-z][a-z0-9_-]*): /.exec(typeof action === "string" ? action : "");
  return m && model.agents[m[1]] ? m[1] : ORCHESTRATOR;
}

function endStep(steps: Step[], id: string, status: Step["status"], now: number): Step[] {
  return steps.map((s) =>
    s.id === id && s.status === "running"
      ? { ...s, status, durationMs: Math.max(0, now - s.startedAt) }
      : s,
  );
}

function isFailedResult(result: unknown): boolean {
  return typeof result === "string" && /^(error|failed|could not|refused)\b/i.test(result.trim());
}

export function reduce(model: AgentsModel, event: AgentEvent, now: number): AgentsModel {
  const e = event as FrameLike;
  switch (e.t) {
    case "profiles": {
      const ev = event as Extract<AgentEvent, { t: "profiles" }>;
      let next: AgentsModel = {
        ...model,
        orchestratorEnabled: ev.orchestratorEnabled ?? model.orchestratorEnabled,
      };
      for (const p of ev.agents) {
        const name = p.name === "default" ? ORCHESTRATOR : p.name;
        next = patch(next, name, (v) => ({
          role: p.role ?? v.role,
          purpose: p.purpose || v.purpose,
          model: p.model ?? v.model,
          tools: typeof p.tools === "number" ? p.tools : v.tools,
        }));
      }
      return next;
    }

    case "snapshot": {
      const ev = event as Extract<AgentEvent, { t: "snapshot" }>;
      // A snapshot corrects what we show now; it does not by itself prove the
      // server will keep sending `agent_state` frames, so it does not switch
      // the derivation off. The first live frame does.
      // The REST answer can land after live frames (it is a round trip), so it
      // may only RAISE an agent that is still idle and untouched; it never
      // overwrites what the socket already told us.
      const applied = ev.states.reduce((m, s) => {
        const mine = m.agents[s.agent];
        const untouched = !mine || (mine.state === "idle" && mine.steps.length === 0);
        return untouched && s.state !== "idle"
          ? reduce(m, { t: "agent_state", ...s }, now)
          : m;
      }, model);
      return { ...applied, authoritative: model.authoritative };
    }

    case "agent_state": {
      const name = typeof e.agent === "string" ? e.agent : "";
      const state = typeof e.state === "string" ? e.state : "";
      if (!name || !STATES.has(state)) return model; // never trust a malformed frame
      const report = e.report as Report | undefined;
      const task = clip(e.task);
      return patch({ ...model, authoritative: true }, name, (v) => ({
        // The server sends `done` and `idle` back to back, so honouring both
        // would never show the outcome. Hold the outcome; `settle` (a timer in
        // the hook) returns it to idle a few seconds later.
        state: (state === "idle" && isTransient(v.state) ? v.state : state) as AgentStateName,
        task: state === "idle" ? v.task : task || v.task,
        tool: state === "working" ? clip(e.tool, 60) : "",
        delegation: typeof e.delegation === "string" ? e.delegation : "",
        since: typeof e.since === "number" ? e.since * 1000 : now,
        report: report && typeof report.summary === "string" ? report : v.report,
        outSeq: state === "working" && v.state !== "working" && v.role === "sub" ? v.outSeq + 1 : v.outSeq,
        backSeq:
          (state === "done" || state === "failed" || state === "declined") && v.role === "sub"
            ? v.backSeq + 1
            : v.backSeq,
      }));
    }

    case "ask_sent": {
      const ev = event as Extract<AgentEvent, { t: "ask_sent" }>;
      return patch(model, ORCHESTRATOR, { task: clip(ev.text) });
    }

    case "turn_start":
      return derive(model, ORCHESTRATOR, "thinking", now, { tool: "", resume: "thinking" });

    case "thinking": {
      const who = typeof e.agent === "string" ? e.agent : ORCHESTRATOR;
      return derive(model, who, "thinking", now, { tool: "" });
    }

    case "tool_call": {
      const who = typeof e.agent === "string" && e.agent ? e.agent : ORCHESTRATOR;
      const name = String(e.name ?? "");
      const args = (e.args ?? {}) as Record<string, unknown>;
      const id = String(e.call_id ?? `${name}-${now}`);
      const step: Step = {
        id, tool: name, args: argsPreview(args), status: "running", startedAt: now,
      };
      let next = derive(model, who, "working", now, {
        tool: name,
        resume: "working",
      });
      next = patch(next, who, (v) => ({ steps: [...v.steps, step].slice(-MAX_STEPS) }));

      // The Orchestrator handing work over: the target lights up and the
      // connector pulse runs out to it.
      if (who === ORCHESTRATOR && name === "delegate" && typeof args.agent === "string") {
        const target = args.agent;
        const task = clip(args.task);
        next = patch(next, target, (v) => ({
          task,
          report: null,
          tool: "",
          outSeq: v.outSeq + 1,
          delegations: [
            { id, task, startedAt: now },
            ...v.delegations,
          ].slice(0, MAX_DELEGATIONS),
        }));
        next = derive(next, target, "thinking", now);
      }
      return next;
    }

    case "tool_result": {
      const who = typeof e.agent === "string" && e.agent ? e.agent : ORCHESTRATOR;
      const id = String(e.call_id ?? "");
      const name = String(e.name ?? "");
      const failed = isFailedResult(e.result);
      let next = patch(model, who, (v) => ({
        steps: endStep(v.steps, id, failed ? "failed" : "ok", now),
      }));

      if (who === ORCHESTRATOR && name === "delegate") {
        const report = parseDelegateReport(String(e.result ?? ""));
        if (report) {
          const result = outcomeState(report.outcome);
          next = ensure(next, report.agent);
          next = derive(next, report.agent, result, now, {
            tool: "",
            report: { outcome: report.outcome, summary: report.summary },
            backSeq: next.agents[report.agent].backSeq + 1,
            delegations: next.agents[report.agent].delegations.map((d) =>
              d.id === id ? { ...d, endedAt: now, outcome: report.outcome } : d,
            ),
          });
        }
        return derive(next, ORCHESTRATOR, "thinking", now, { tool: "" });
      }
      return derive(next, who, "thinking", now, { tool: "" });
    }

    case "confirm_request": {
      const who = confirmOwner(model, e.action);
      const prior = model.agents[who]?.state;
      return derive(model, who, "waiting", now, {
        resume: prior && prior !== "waiting" ? prior : "thinking",
      });
    }

    case "confirm_resolved":
    case "confirm_expired": {
      // Anyone still waiting goes back to what they were doing. A denial ends
      // the work through the normal turn/report frames that follow.
      let next = model;
      for (const name of model.order) {
        const v = next.agents[name];
        if (v?.state === "waiting") {
          next = derive(next, name, v.resume === "waiting" ? "thinking" : v.resume, now);
        }
      }
      return next;
    }

    case "turn_end": {
      let next = model;
      const failed = Boolean(e.error);
      const cancelled = Boolean(e.cancelled);
      // Anything still mid-flight when the turn ends was cut short.
      for (const name of model.order) {
        const v = next.agents[name];
        if (!v) continue;
        if (name === ORCHESTRATOR) continue;
        if (v.state === "thinking" || v.state === "working" || v.state === "waiting") {
          next = derive(next, name, "idle", now, { tool: "" });
        }
      }
      next = patch(next, ORCHESTRATOR, (v) => ({
        steps: v.steps.map((s) => (s.status === "running" ? { ...s, status: "lost" as const } : s)),
      }));
      return derive(next, ORCHESTRATOR, failed ? "failed" : cancelled ? "idle" : "done", now, {
        tool: "",
      });
    }

    case "error": {
      return derive(model, ORCHESTRATOR, "failed", now, { tool: "" });
    }

    case "cancelled":
      return model;

    case "settle": {
      let next = model;
      for (const name of model.order) {
        const v = next.agents[name];
        if (v && (v.state === "done" || v.state === "failed" || v.state === "declined")) {
          next = patch(next, name, { state: "idle", since: now });
        }
      }
      return next;
    }

    case "replay_start": {
      // The server is about to replay the whole conversation as frames, so
      // keep who the agents ARE (identity) and drop what they DID.
      const fresh = initialModel();
      let next: AgentsModel = { ...fresh, orchestratorEnabled: model.orchestratorEnabled };
      for (const name of model.order) {
        const old = model.agents[name];
        next = patch(next, name, {
          role: old.role, purpose: old.purpose, model: old.model, tools: old.tools,
        });
      }
      return next;
    }

    case "disconnected":
    case "reset_ok": {
      let next = model;
      for (const name of model.order) {
        const v = next.agents[name];
        if (v && v.state !== "idle") next = patch(next, name, { state: "idle", tool: "", since: now });
      }
      return { ...next, authoritative: false };
    }

    default:
      return model;
  }
}

/** True when the agent is in a state that wants the owner's eyes. */
export function needsAttention(view: AgentView): boolean {
  return view.state === "waiting" || view.state === "failed" || view.state === "declined";
}

/** Short words for each state, used in labels and never colour alone. */
export const STATE_LABEL: Record<AgentStateName, string> = {
  idle: "Idle",
  thinking: "Thinking",
  working: "Working",
  waiting: "Needs your OK",
  done: "Done",
  failed: "Failed",
  declined: "Declined",
};

/** True while a state should settle back to idle on a timer. */
export function isTransient(state: AgentStateName): boolean {
  return state === "done" || state === "failed" || state === "declined";
}
