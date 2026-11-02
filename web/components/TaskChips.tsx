"use client";

/**
 * Background sub-agent tasks in the UI (engine #159): one compact chip per
 * task in the stream, the same list inside an agent's pane, and the notice
 * Stop leaves behind for tasks it did not start.
 *
 * Everything here renders from `lib/tasks.ts`; the only thing fetched is a
 * finished task's full report, and only when its chip is opened.
 */

import { useEffect, useState } from "react";

import { api } from "@/lib/session";
import {
  chipLabel,
  isActive,
  noticeIds,
  trayTasks,
  type Task,
  type TasksModel,
} from "@/lib/tasks";
import { Button } from "./ui";

/** A 1 s clock that only runs while something is running. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

const OUTCOME_COLOUR: Record<string, string> = {
  done: "var(--text-muted)",
  partial: "var(--moderate)",
  failed: "var(--state-failed)",
  declined: "var(--moderate)",
  "needs-approval": "var(--coral)",
  cancelled: "var(--text-faint)",
};

function TaskChip({
  task,
  now,
  fresh,
  cancelling,
  onCancel,
  onOpen,
  onDismiss,
}: {
  task: Task;
  now: number;
  fresh: boolean;
  cancelling: boolean;
  onCancel: (id: string) => void;
  onOpen: (id: string) => void;
  onDismiss?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<string | null>(null);
  const active = isActive(task.status);
  const finished = !active;

  useEffect(() => {
    if (!open || !finished || report !== null) return;
    let live = true;
    api<{ report?: string }>(`/api/tasks?id=${encodeURIComponent(task.id)}`)
      .then((r) => live && setReport(typeof r?.report === "string" ? r.report : ""))
      .catch(() => live && setReport(""));
    return () => {
      live = false;
    };
  }, [open, finished, report, task.id]);

  const toggle = () => {
    setOpen((value) => !value);
    if (fresh) onOpen(task.id);
  };
  const colour = finished ? OUTCOME_COLOUR[task.outcome || task.status] ?? "var(--text-muted)" : "var(--accent)";
  const body = report || task.summary || "";

  return (
    <li
      data-task={task.id}
      data-fresh={fresh || undefined}
      className="glass rounded-[var(--radius)] border"
      style={{
        borderColor: fresh ? "var(--accent-line)" : "var(--glass-border)",
        boxShadow: fresh ? "var(--glow-soft)" : undefined,
      }}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          disabled={active}
          aria-label={`${task.agent}: ${task.task}. ${chipLabel(task, now)}${fresh ? ". New report" : ""}`}
          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default max-sm:min-h-10"
        >
          <span
            aria-hidden
            className="h-2 w-2 shrink-0 rounded-full"
            style={{
              background: colour,
              animation: active ? "breathe var(--loop-breathe) ease-in-out infinite" : undefined,
            }}
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[length:var(--text-sm)] text-[var(--text)]">
              <span className="font-semibold">{task.agent}</span>
              <span className="text-[var(--text-muted)]"> · {task.task}</span>
            </span>
            <span
              className="block truncate text-[length:var(--text-xs)] tabular-nums"
              style={{ color: finished ? colour : "var(--text-faint)" }}
            >
              {chipLabel(task, now)}
              {fresh && " · new report"}
            </span>
          </span>
        </button>
        {active && (
          <Button
            variant="ghost"
            onClick={() => onCancel(task.id)}
            disabled={cancelling}
            title={`Cancel ${task.agent}'s task`}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </Button>
        )}
        {finished && onDismiss && (
          <Button variant="ghost" onClick={() => onDismiss(task.id)} title="Remove from this tray">
            Dismiss
          </Button>
        )}
      </div>
      {open && finished && (
        <div className="border-t border-[var(--border)] px-3 py-2">
          <p className="whitespace-pre-wrap text-[length:var(--text-sm)] text-[var(--text)]">
            {body || (report === null ? "Loading the report…" : "(no report)")}
          </p>
        </div>
      )}
    </li>
  );
}

export function TaskList({
  tasks,
  list,
  cancelling,
  onCancel,
  onSeen,
  onDismiss,
  label = "Background tasks",
}: {
  tasks: TasksModel;
  list: Task[];
  cancelling: string[];
  onCancel: (id: string) => void;
  onSeen: (id: string) => void;
  onDismiss?: (id: string) => void;
  label?: string;
}) {
  const now = useNow(list.some((t) => isActive(t.status)));
  if (list.length === 0) return null;
  return (
    <ul aria-label={label} className="space-y-2">
      {list.map((task) => (
        <TaskChip
          key={task.id}
          task={task}
          now={now}
          fresh={Boolean(tasks.fresh[task.id])}
          cancelling={cancelling.includes(task.id)}
          onCancel={onCancel}
          onOpen={onSeen}
          onDismiss={onDismiss}
        />
      ))}
    </ul>
  );
}

/**
 * The reserved tray above the composer: Stop notice, then the running tasks
 * and unopened reports. It collapses to one summary line when there are more
 * than two entries or the empty-chat hero is showing; the owner's own toggle
 * wins over that default. It takes layout space, so it can never sit on top of
 * other content.
 */
export function TaskTray({
  tasks,
  hero,
  cancelling,
  onCancel,
  onCancelAll,
  onKeep,
  onSeen,
}: {
  tasks: TasksModel;
  hero: boolean;
  cancelling: string[];
  onCancel: (id: string) => void;
  onCancelAll: (ids: string[]) => void;
  onKeep: () => void;
  onSeen: (id: string) => void;
}) {
  const [choice, setChoice] = useState<boolean | null>(null); // true = expanded
  // A report the owner opened stays until they dismiss it: opening clears the
  // "new" highlight, and without a pin the chip would vanish mid-read.
  const [pinned, setPinned] = useState<string[]>([]);
  const list = trayTasks(tasks, pinned);
  const hasNotice = noticeIds(tasks).length > 0;
  if (list.length === 0 && !hasNotice) return null;

  const running = list.filter((t) => isActive(t.status)).length;
  const reports = list.filter((t) => !isActive(t.status) && tasks.fresh[t.id]).length;
  const expanded = choice ?? !(list.length > 2 || hero);
  const summary = [
    running ? `${running} ${running === 1 ? "task" : "tasks"} running` : "",
    reports ? `${reports} new ${reports === 1 ? "report" : "reports"}` : "",
  ]
    .filter(Boolean)
    .join(" · ") || "Reports";

  return (
    <div data-task-tray className="mb-2 mt-2 space-y-2">
      <StopNotice tasks={tasks} onCancelAll={onCancelAll} onKeep={onKeep} />
      {list.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setChoice(!expanded)}
            aria-expanded={expanded}
            className="glass flex w-full items-center justify-between gap-3 rounded-[var(--radius)] px-3 py-1.5 text-left text-[length:var(--text-xs)] text-[var(--text-muted)] max-sm:min-h-10"
          >
            <span>{summary}</span>
            <span className="text-[var(--accent)]">{expanded ? "Collapse" : "Expand"}</span>
          </button>
          {expanded && (
            <div className="max-h-[26dvh] overflow-y-auto" data-task-list>
              <TaskList
                tasks={tasks}
                list={list}
                cancelling={cancelling}
                onCancel={onCancel}
                onSeen={(id) => {
                  setPinned((p) => (p.includes(id) ? p : [...p, id]));
                  onSeen(id);
                }}
                onDismiss={(id) => setPinned((p) => p.filter((x) => x !== id))}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** After Stop: earlier background tasks are still running. */
export function StopNotice({
  tasks,
  onCancelAll,
  onKeep,
}: {
  tasks: TasksModel;
  onCancelAll: (ids: string[]) => void;
  onKeep: () => void;
}) {
  const ids = noticeIds(tasks);
  if (ids.length === 0) return null;
  return (
    <div
      role="status"
      data-stop-notice
      className="glass flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius)] px-3 py-2"
      style={{ animation: "node-in var(--motion-base) var(--ease)" }}
    >
      <span className="min-w-0 flex-1 text-[length:var(--text-sm)] text-[var(--text)]">
        {ids.length} earlier {ids.length === 1 ? "task" : "tasks"} still running
      </span>
      <Button variant="danger" onClick={() => onCancelAll(ids)}>
        Cancel all
      </Button>
      <Button variant="ghost" onClick={onKeep}>
        Keep
      </Button>
    </div>
  );
}
