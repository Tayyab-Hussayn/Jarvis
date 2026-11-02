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
}: {
  task: Task;
  now: number;
  fresh: boolean;
  cancelling: boolean;
  onCancel: (id: string) => void;
  onOpen: (id: string) => void;
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
  label = "Background tasks",
}: {
  tasks: TasksModel;
  list: Task[];
  cancelling: string[];
  onCancel: (id: string) => void;
  onSeen: (id: string) => void;
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
        />
      ))}
    </ul>
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
      className="glass mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius)] px-3 py-2"
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
