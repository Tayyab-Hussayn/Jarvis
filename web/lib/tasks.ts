/**
 * Background sub-agent tasks (engine #159): the store behind the task chips,
 * the agent pane's task list, the constellation's "working" overlay and the
 * Stop notice.
 *
 * Pure, so frame sequences are testable as plain data. Three sources feed it
 * and they race: live `{t:"task"}` frames, the `GET /api/tasks` listing (read
 * on every hello, so a resume starts from the server's truth) and a cancel
 * answer. Whichever arrives last must never roll a task backwards, so every
 * merge goes through `rank`: a finished task stays finished and its step
 * counter never goes down.
 */

import type { AgentView, AgentsModel } from "./agents";

export type TaskStatus =
  | "queued"
  | "running"
  | "done"
  | "partial"
  | "failed"
  | "declined"
  | "needs-approval"
  | "cancelled";

export interface Task {
  id: string;
  agent: string;
  task: string;
  status: TaskStatus;
  step: number;
  tool: string;
  created: number;
  /** Seconds, as the server counted when it last told us. */
  elapsed: number;
  /** Local clock (ms) when `elapsed` was received: the live timer runs from it
   *  so a browser clock far from the server's cannot skew it. */
  at: number;
  outcome?: string;
  summary?: string;
}

const STATUSES: ReadonlySet<string> = new Set([
  "queued", "running", "done", "partial", "failed", "declined", "needs-approval", "cancelled",
]);

export function isActive(status: TaskStatus): boolean {
  return status === "queued" || status === "running";
}

const rank = (status: TaskStatus): number => (status === "queued" ? 0 : status === "running" ? 1 : 2);

export interface TasksModel {
  byId: Record<string, Task>;
  /** Newest first. */
  order: string[];
  /** Bumped when a task starts / ends for an agent: restarts the connector pulse. */
  startSeq: Record<string, number>;
  endSeq: Record<string, number>;
  /** Finished while we were watching and not yet opened: the chip is highlighted. */
  fresh: Record<string, true>;
  /** Ids from the last Stop's `other_tasks`; `null` once answered or dismissed. */
  notice: string[] | null;
}

export function initialTasks(): TasksModel {
  return { byId: {}, order: [], startSeq: {}, endSeq: {}, fresh: {}, notice: null };
}

const str = (v: unknown, max: number): string => (typeof v === "string" ? v.slice(0, max) : "");
const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Accept only a well-formed row; anything else is `null` and skipped. */
export function parseTask(raw: unknown, now: number): Task | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id, 40);
  const agent = str(r.agent, 60);
  const status = typeof r.status === "string" ? r.status : "";
  if (!id || !agent || !STATUSES.has(status)) return null;
  const out: Task = {
    id,
    agent,
    task: str(r.task, 200),
    status: status as TaskStatus,
    step: Math.max(0, Math.floor(num(r.step))),
    tool: str(r.tool, 60),
    created: num(r.created),
    elapsed: Math.max(0, num(r.elapsed)),
    at: now,
  };
  if (typeof r.outcome === "string" && r.outcome) out.outcome = r.outcome.slice(0, 40);
  if (typeof r.summary === "string" && r.summary) out.summary = r.summary.slice(0, 400);
  return out;
}

function upsert(model: TasksModel, incoming: Task, live: boolean): TasksModel {
  const old = model.byId[incoming.id];
  let task = incoming;
  if (old) {
    if (rank(old.status) > rank(incoming.status)) {
      // A late answer about an earlier moment: keep what we know.
      task = old;
    } else {
      task = { ...incoming, step: Math.max(old.step, incoming.step) };
      if (!task.summary && old.summary) task.summary = old.summary;
    }
  }
  const startSeq = { ...model.startSeq };
  const endSeq = { ...model.endSeq };
  const fresh = { ...model.fresh };
  // Pulses and highlights belong to live frames: hydrating a list on resume
  // must not replay an animation for something that happened long ago.
  if (live) {
    if (!old && isActive(task.status)) startSeq[task.agent] = (startSeq[task.agent] ?? 0) + 1;
    if (old && isActive(old.status) && !isActive(task.status)) {
      endSeq[task.agent] = (endSeq[task.agent] ?? 0) + 1;
      fresh[task.id] = true;
    }
    if (!old && !isActive(task.status)) fresh[task.id] = true;
  }
  const byId = { ...model.byId, [task.id]: task };
  const order = old
    ? model.order
    : [...model.order, task.id].sort((a, b) => (byId[b]?.created ?? 0) - (byId[a]?.created ?? 0));
  return { ...model, byId, order, startSeq, endSeq, fresh };
}

/** A live `{t:"task"}` frame. */
export function applyFrame(model: TasksModel, frame: unknown, now: number): TasksModel {
  const task = parseTask(frame, now);
  return task ? upsert(model, task, true) : model;
}

/** A `GET /api/tasks` listing or a cancel answer's `task`. */
export function hydrate(model: TasksModel, rows: unknown, now: number): TasksModel {
  if (!Array.isArray(rows)) return model;
  return rows.reduce<TasksModel>((m, raw) => {
    const task = parseTask(raw, now);
    return task ? upsert(m, task, false) : m;
  }, model);
}

/** A `cancelled` frame: remember which earlier tasks Stop left running. */
export function applyCancelled(model: TasksModel, frame: unknown): TasksModel {
  const raw = (frame as { other_tasks?: unknown } | null)?.other_tasks;
  const ids = Array.isArray(raw)
    ? raw.filter((x): x is string => typeof x === "string" && x.length > 0)
    : [];
  return { ...model, notice: ids.length ? ids : null };
}

export function dismissNotice(model: TasksModel): TasksModel {
  return model.notice === null ? model : { ...model, notice: null };
}

/** Ids the notice still applies to: an id we know has finished drops out, so
 *  the notice cannot offer to cancel something that already ended. */
export function noticeIds(model: TasksModel): string[] {
  return (model.notice ?? []).filter((id) => {
    const t = model.byId[id];
    return !t || isActive(t.status);
  });
}

/** The owner opened a finished chip. */
export function markSeen(model: TasksModel, id: string): TasksModel {
  if (!model.fresh[id]) return model;
  const fresh = { ...model.fresh };
  delete fresh[id];
  return { ...model, fresh };
}

export function activeTasks(model: TasksModel): Task[] {
  return model.order.map((id) => model.byId[id]).filter((t) => t && isActive(t.status));
}

/** What the tray shows: running work and reports nobody has opened yet.
 *  Older finished tasks stay in the agent pane, so the tray cannot grow. */
export function trayTasks(model: TasksModel, pinned: string[] = []): Task[] {
  return model.order
    .map((id) => model.byId[id])
    .filter((t) => t && (isActive(t.status) || model.fresh[t.id] || pinned.includes(t.id)));
}

/** Every task, newest first. */
export function tasksAll(model: TasksModel): Task[] {
  return model.order.map((id) => model.byId[id]).filter(Boolean);
}

export function tasksFor(model: TasksModel, agent: string): Task[] {
  return model.order.map((id) => model.byId[id]).filter((t) => t && t.agent === agent);
}

/** Live elapsed seconds: the server's figure plus time since we got it. */
export function elapsedNow(task: Task, now: number): number {
  return isActive(task.status)
    ? Math.max(0, Math.floor(task.elapsed + (now - task.at) / 1000))
    : task.elapsed;
}

/** "12s", "3m04s". */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

/** What a chip says besides the task text. */
export function chipLabel(task: Task, now: number): string {
  if (task.status === "queued") return "queued";
  if (task.status === "running") {
    const step = task.step > 0 ? `step ${task.step}` : "starting";
    return `${task.tool ? `${task.tool} · ` : ""}${step} · ${formatElapsed(elapsedNow(task, now))}`;
  }
  return `${task.outcome || task.status} · ${formatElapsed(task.elapsed)}`;
}

/**
 * The agents model with background work shown on it. A sub-agent running a
 * task is "working" even while the Orchestrator is idle, and each task start /
 * end restarts the connector pulse. The agent store itself is untouched: this
 * is a view, so the two stores cannot disagree about history.
 */
export function overlayAgents(model: AgentsModel, tasks: TasksModel): AgentsModel {
  const active = activeTasks(tasks);
  const touched = new Set([...Object.keys(tasks.startSeq), ...Object.keys(tasks.endSeq)]);
  for (const t of active) touched.add(t.agent);
  if (touched.size === 0) return model;
  const agents = { ...model.agents };
  for (const name of touched) {
    const view = agents[name];
    if (!view || view.role === "orchestrator") continue;
    const mine = active.filter((t) => t.agent === name);
    const patch: Partial<AgentView> = {
      outSeq: view.outSeq + (tasks.startSeq[name] ?? 0),
      backSeq: view.backSeq + (tasks.endSeq[name] ?? 0),
    };
    // A waiting agent keeps its coral badge: a confirmation outranks "working".
    if (mine.length > 0 && view.state !== "waiting") {
      const first = mine[0];
      patch.state = "working";
      patch.task = mine.length > 1 ? `${mine.length} background tasks` : first.task;
      patch.tool = first.tool || view.tool;
    }
    agents[name] = { ...view, ...patch };
  }
  return { ...model, agents };
}
