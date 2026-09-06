"use client";

/**
 * Scheduled jobs and reminders.
 *
 * Reads `/api/jobs` for records rather than parsing `list_jobs`' prose — same
 * rule as the windows and process panels, and for the same reason: that
 * wording exists to be read by a model and is free to change.
 *
 * Two things drive the layout. First, the timer's state comes before anything
 * else: a list of jobs that cannot fire looks identical to one that can, so
 * when it is off the page says so instead of rendering a tidy list of things
 * that will never happen. Second, upcoming work outranks history — the
 * question this screen answers is "what is about to happen", not "what did".
 *
 * Every mutation goes through the existing `run` frame, so a scheduled agent
 * job raises the same confirmation card a model-issued one would. There is no
 * second write path.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { JobEvent } from "@/lib/protocol";
import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Chip, Empty, Input, Panel, SectionTitle } from "./ui";
import { SkillOutput } from "./SkillOutput";
import { Icon } from "./icons";
import { Screen } from "./Shell";

interface JobRow {
  id: string;
  kind: "notify" | "agent";
  prompt: string;
  title: string;
  cron: string;
  timezone: string;
  speak: boolean;
  created_at: string;
  next_run: string;
  state: string;
  attempts: number;
  last_run: string;
  last_error: string;
  runs: number;
  running_pid: number | null;
}

interface HistoryRow {
  ts: string;
  event: string;
  id?: string;
  error?: string;
  seconds?: number;
  next_run?: string;
  late_seconds?: number;
}

interface JobsSnapshot {
  timer_active: boolean;
  timezone: string;
  grace_seconds: number;
  now: string;
  jobs: JobRow[];
  history: HistoryRow[];
}

/* -- vocabulary --------------------------------------------------------- */

const STATE_STYLE: Record<string, { label: string; tone: string; dim?: boolean }> = {
  pending: { label: "Scheduled", tone: "var(--accent)" },
  running: { label: "Running", tone: "var(--moderate)" },
  paused: { label: "Paused", tone: "var(--text-faint)" },
  done: { label: "Done", tone: "var(--text-faint)", dim: true },
  failed: { label: "Failed", tone: "var(--critical)" },
  missed: { label: "Missed", tone: "var(--destructive)" },
};

const EVENT_STYLE: Record<string, { label: string; tone: string }> = {
  ran: { label: "ran", tone: "var(--accent)" },
  scheduled: { label: "scheduled", tone: "var(--moderate)" },
  edited: { label: "edited", tone: "var(--moderate)" },
  paused: { label: "paused", tone: "var(--text-faint)" },
  resumed: { label: "resumed", tone: "var(--accent)" },
  cancelled: { label: "cancelled", tone: "var(--text-faint)" },
  failed: { label: "failed", tone: "var(--critical)" },
  missed: { label: "missed", tone: "var(--destructive)" },
  reaped: { label: "reaped", tone: "var(--moderate)" },
  tick_error: { label: "tick error", tone: "var(--critical)" },
  notify_failed: { label: "notify failed", tone: "var(--moderate)" },
};

/** Common schedules, so the usual case never needs cron syntax. */
const PRESETS: { label: string; cron: string }[] = [
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Every day 9am", cron: "0 9 * * *" },
  { label: "Weekdays 9am", cron: "0 9 * * 1-5" },
  { label: "Mondays 9am", cron: "0 9 * * 1" },
  { label: "1st of month", cron: "0 9 1 * *" },
];

/* -- time --------------------------------------------------------------- */

function relative(iso: string, from: number): string {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return "—";
  const delta = Math.round((target - from) / 1000);
  const ahead = delta >= 0;
  const s = Math.abs(delta);
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);

  let text: string;
  if (days) text = `${days}d ${hours}h`;
  else if (hours) text = `${hours}h ${minutes}m`;
  else if (minutes) text = `${minutes}m`;
  else text = `${s % 60}s`;
  return ahead ? `in ${text}` : `${text} ago`;
}

function absolute(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  return at.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
}

/** UTC ISO → the `YYYY-MM-DDTHH:mm` a datetime-local input wants, in local time. */
function toLocalInput(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

function defaultWhen(): string {
  const at = new Date(Date.now() + 60 * 60 * 1000);
  at.setSeconds(0, 0);
  return toLocalInput(at.toISOString());
}

/**
 * Shape check only — five or six space-separated fields.
 *
 * The server has croniter and is the authority; this exists so a typo is
 * caught before a round trip, not to reimplement the grammar here.
 */
function cronLooksValid(expression: string): boolean {
  const parts = expression.trim().split(/\s+/).filter(Boolean);
  if (parts.length < 5 || parts.length > 6) return false;
  return parts.every((p) => /^[\d*,\-/LW?#]+$/i.test(p));
}

/* -- pieces ------------------------------------------------------------- */

function StatusBadge({ state }: { state: string }) {
  const style = STATE_STYLE[state] ?? { label: state, tone: "var(--text-faint)" };
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded-[var(--radius-full)] px-2 py-0.5 text-[length:var(--text-xs)] font-medium"
      style={{ color: style.tone, background: "color-mix(in srgb, currentColor 12%, transparent)" }}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full"
        style={{
          background: style.tone,
          animation: state === "running" ? "pulse 1.2s ease-in-out infinite" : undefined,
        }}
      />
      {style.label}
    </span>
  );
}

function TimerBanner({ active }: { active: boolean }) {
  if (active) return null;
  return (
    <div
      className="rounded-[var(--radius)] border-l-2 px-4 py-3 text-[length:var(--text-sm)]"
      style={{
        borderLeftColor: "var(--critical)",
        background: "var(--critical-dim)",
        color: "var(--critical)",
      }}
    >
      <strong>Nothing below will fire.</strong> The scheduled-jobs timer is not
      running, so jobs are only being stored, never executed.
      <pre className="mt-2 overflow-x-auto font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
        systemctl --user enable --now kravein-jobs.timer
      </pre>
    </div>
  );
}

/* -- one row ------------------------------------------------------------ */

function JobRowView({
  job,
  now,
  flash,
  busy,
  onEdit,
  onPause,
  onDelete,
}: {
  job: JobRow;
  now: number;
  flash: boolean;
  busy: boolean;
  onEdit: () => void;
  onPause: () => void;
  onDelete: () => void;
}) {
  const live = job.state === "pending" || job.state === "running";
  const paused = job.state === "paused";
  const finished = !live && !paused;

  return (
    <div
      className="group grid grid-cols-[1fr_auto] items-start gap-3 rounded-[var(--radius)] border px-3.5 py-3"
      style={{
        borderColor: flash ? "var(--accent)" : "var(--border)",
        background: "var(--surface-raised)",
        opacity: finished ? 0.6 : 1,
        transition: "border-color var(--motion-base) var(--ease)",
      }}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge state={job.state} />
          <p
            className="min-w-0 break-words text-[length:var(--text-sm)]"
            style={{ textDecoration: paused ? "none" : undefined }}
          >
            {job.prompt}
          </p>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          <span className="font-[family-name:var(--font-mono)]">{job.id}</span>
          <span title={job.next_run}>{absolute(job.next_run)}</span>
          {(live || paused) && (
            <span style={{ color: paused ? "var(--text-faint)" : "var(--text-muted)" }}>
              {paused ? "paused" : relative(job.next_run, now)}
            </span>
          )}
          {job.cron && (
            <span
              className="rounded-[var(--radius-sm)] px-1.5 py-0.5 font-[family-name:var(--font-mono)]"
              style={{ background: "var(--surface-sunken)", color: "var(--moderate)" }}
              title="Repeats on this schedule"
            >
              {job.cron}
            </span>
          )}
          {job.kind === "agent" && (
            <span style={{ color: "var(--moderate)" }}>runs tools</span>
          )}
          {job.speak && <span>speaks</span>}
          {job.runs > 0 && <span>{job.runs} run{job.runs === 1 ? "" : "s"}</span>}
          {job.attempts > 0 && (
            <span style={{ color: "var(--moderate)" }}>retry {job.attempts}/3</span>
          )}
        </div>

        {job.last_error && (
          <p
            className="mt-1.5 break-words text-[length:var(--text-xs)]"
            style={{ color: "var(--critical)" }}
          >
            {job.last_error}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button variant="ghost" onClick={onEdit} disabled={busy} title="Edit">
          <Icon name="settings" size={14} />
        </Button>
        {(live || paused) && (
          <Button
            variant="ghost"
            onClick={onPause}
            disabled={busy}
            title={paused ? "Resume" : "Pause"}
          >
            <span
              aria-hidden
              className="block text-[length:var(--text-xs)] font-[family-name:var(--font-mono)]"
              style={{ color: paused ? "var(--accent)" : "var(--text-muted)" }}
            >
              {paused ? "▶" : "❚❚"}
            </span>
          </Button>
        )}
        <Button variant="ghost" onClick={onDelete} disabled={busy} title="Delete">
          <Icon name="trash" size={14} />
        </Button>
      </div>
    </div>
  );
}

/* -- the form ----------------------------------------------------------- */

type Mode = "once" | "repeat";

function JobForm({
  editing,
  busy,
  onSubmit,
  onCancelEdit,
}: {
  editing: JobRow | null;
  busy: boolean;
  onSubmit: (values: {
    prompt: string; when: string; repeat: string; kind: "notify" | "agent";
  }) => void;
  onCancelEdit: () => void;
}) {
  // Seeded from props, never synced by an effect. The caller gives this
  // component a `key` of the job id, so picking a different job remounts it
  // with fresh initial state — which is what an effect would have been
  // imitating, badly. It also means a background poll can never overwrite
  // what is being typed.
  const [prompt, setPrompt] = useState(editing?.prompt ?? "");
  const [mode, setMode] = useState<Mode>(editing?.cron ? "repeat" : "once");
  const [when, setWhen] = useState(() =>
    editing && !editing.cron ? toLocalInput(editing.next_run) : defaultWhen(),
  );
  const [cron, setCron] = useState(editing?.cron || "0 9 * * *");
  const [runsTools, setRunsTools] = useState(editing?.kind === "agent");

  const cronOk = mode === "repeat" ? cronLooksValid(cron) : true;
  const whenOk = mode === "once" ? Boolean(when) : true;
  const ready = Boolean(prompt.trim()) && cronOk && whenOk && !busy;

  const submit = () => {
    if (!ready) return;
    onSubmit({
      prompt: prompt.trim(),
      when: mode === "once" ? when : "",
      repeat: mode === "repeat" ? cron.trim() : "",
      kind: runsTools ? "agent" : "notify",
    });
    if (!editing) setPrompt("");
  };

  return (
    <Panel
      title={editing ? `Editing ${editing.id}` : "New reminder"}
      actions={
        editing ? (
          <Button variant="ghost" onClick={onCancelEdit}>Cancel</Button>
        ) : undefined
      }
    >
      <div className="space-y-3">
        <Input
          value={prompt}
          onChange={setPrompt}
          placeholder="What should it say, or do?"
          onEnter={submit}
        />

        <div
          role="radiogroup"
          aria-label="Schedule type"
          className="inline-flex rounded-[var(--radius)] border border-[var(--border-strong)] p-0.5"
        >
          {(["once", "repeat"] as Mode[]).map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={mode === option}
              onClick={() => setMode(option)}
              className="rounded-[var(--radius-sm)] px-3 py-1 text-[length:var(--text-xs)]"
              style={{
                background: mode === option ? "var(--accent)" : "transparent",
                color: mode === option ? "var(--accent-contrast)" : "var(--text-muted)",
                transition: "all var(--motion-fast) var(--ease)",
              }}
            >
              {option === "once" ? "Once" : "Repeating"}
            </button>
          ))}
        </div>

        {mode === "once" ? (
          <label className="block">
            <span className="mb-1 block text-[length:var(--text-xs)] text-[var(--text-faint)]">
              Date and time
            </span>
            {/* A native picker, so there is no format to get wrong. It yields
                `YYYY-MM-DDTHH:mm` in local time, which is exactly what the
                backend reads in the job's timezone. */}
            <input
              type="datetime-local"
              value={when}
              onChange={(event) => setWhen(event.target.value)}
              className="w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-2 text-[length:var(--text-sm)] outline-none focus:border-[var(--accent)]"
              style={{ colorScheme: "light dark", transition: "border-color var(--motion-fast) var(--ease)" }}
            />
          </label>
        ) : (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((preset) => (
                <Chip
                  key={preset.cron}
                  active={cron.trim() === preset.cron}
                  tone="var(--accent)"
                  onClick={() => setCron(preset.cron)}
                >
                  {preset.label}
                </Chip>
              ))}
            </div>
            <label className="block">
              <span className="mb-1 block text-[length:var(--text-xs)] text-[var(--text-faint)]">
                Cron expression
              </span>
              <Input value={cron} onChange={setCron} mono onEnter={submit} />
            </label>
            <p
              className="text-[length:var(--text-xs)]"
              style={{ color: cronOk ? "var(--text-faint)" : "var(--critical)" }}
            >
              {cronOk
                ? "minute · hour · day of month · month · day of week"
                : "Needs five fields, e.g. 0 9 * * 1-5"}
            </p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Chip
            active={runsTools}
            tone="var(--moderate)"
            onClick={() => setRunsTools((v) => !v)}
          >
            {runsTools ? "Runs tools" : "Just notifies"}
          </Chip>
          <Button variant="primary" onClick={submit} disabled={!ready}>
            {busy ? "Saving…" : editing ? "Save changes" : "Schedule"}
          </Button>
        </div>

        <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {runsTools
            ? "Runs a full agent turn unattended. Destructive actions are refused rather than approved — nobody is there to confirm them."
            : "Shows a desktop notification. No model call, so it cannot fail or surprise you."}
        </p>
      </div>
    </Panel>
  );
}

/* -- the screen --------------------------------------------------------- */

export function JobsView({ run, jobEvents }: { run: RunSkill; jobEvents: JobEvent[] }) {
  const { data, error, reload } = useResource<JobsSnapshot>("/api/jobs", 10000);
  const skill = useSkill(run);
  const [editing, setEditing] = useState<JobRow | null>(null);

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const latestEvent = jobEvents[0]?.at ?? 0;
  useEffect(() => {
    if (latestEvent) reload();
  }, [latestEvent, reload]);

  const recentlyFired = useMemo(
    () => new Set(jobEvents.filter((e) => now - e.at < 10000).map((e) => e.id)),
    [jobEvents, now],
  );

  const act = useCallback(
    async (name: string, args: Record<string, unknown>) => {
      await skill.call(name, args);
      reload();
    },
    [skill, reload],
  );

  const save = (values: {
    prompt: string; when: string; repeat: string; kind: "notify" | "agent";
  }) => {
    if (editing) {
      void act("edit_job", {
        job_id: editing.id,
        prompt: values.prompt,
        when: values.when,
        repeat: values.repeat,
      }).then(() => setEditing(null));
      return;
    }
    void act("schedule_job", values);
  };

  if (error) return <Screen><Empty>Could not read scheduled jobs: {error}</Empty></Screen>;
  if (!data) return <Screen><Empty>Reading scheduled jobs…</Empty></Screen>;

  const upcoming = data.jobs
    .filter((j) => ["pending", "running", "paused"].includes(j.state))
    .sort((a, b) => (a.next_run || "").localeCompare(b.next_run || ""));
  const finished = data.jobs
    .filter((j) => !["pending", "running", "paused"].includes(j.state))
    .sort((a, b) => (b.last_run || b.next_run || "").localeCompare(a.last_run || a.next_run || ""));

  const busy = Boolean(skill.running);

  return (
    <Screen
      subtitle={
        `${upcoming.length} upcoming · ${data.timezone} · ` +
        `skips anything more than ${Math.round(data.grace_seconds / 60)} min late`
      }
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      <div className="min-h-0 space-y-4 overflow-y-auto pb-6">
        <TimerBanner active={data.timer_active} />

        {/* Upcoming work is the question this screen answers, so it gets the
            wide column; history is reference material and sits beside it. */}
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
          <div className="space-y-4">
            <div>
              <SectionTitle>
                Upcoming{upcoming.length ? ` · ${upcoming.length}` : ""}
              </SectionTitle>
              {upcoming.length === 0 ? (
                <Empty>
                  Nothing scheduled. Add one below, or just ask in the Console —
                  “remind me to call the client at 5pm tomorrow”.
                </Empty>
              ) : (
                <div className="space-y-2">
                  {upcoming.map((job) => (
                    <JobRowView
                      key={job.id}
                      job={job}
                      now={now}
                      flash={recentlyFired.has(job.id)}
                      busy={busy}
                      onEdit={() => setEditing(job)}
                      onPause={() =>
                        void act("pause_job", {
                          job_id: job.id, resume: job.state === "paused",
                        })
                      }
                      onDelete={() => void act("cancel_job", { job_id: job.id })}
                    />
                  ))}
                </div>
              )}
            </div>

            <JobForm
              key={editing?.id ?? "new"}
              editing={editing}
              busy={busy}
              onSubmit={save}
              onCancelEdit={() => setEditing(null)}
            />

            <SkillOutput state={skill} />

            {finished.length > 0 && (
              <div>
                <SectionTitle>Finished · {finished.length}</SectionTitle>
                <div className="space-y-2">
                  {finished.slice(0, 6).map((job) => (
                    <JobRowView
                      key={job.id}
                      job={job}
                      now={now}
                      flash={false}
                      busy={busy}
                      onEdit={() => setEditing(job)}
                      onPause={() => undefined}
                      onDelete={() => void act("cancel_job", { job_id: job.id })}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          <Panel title="Run history">
            {data.history.length === 0 ? (
              <Empty>Nothing has run yet.</Empty>
            ) : (
              // A timeline: one rail, one dot per event, colour carrying the
              // outcome so a failure is visible without reading a word.
              <div className="relative pl-4">
                <span
                  aria-hidden
                  className="absolute bottom-2 left-[3px] top-2 w-px"
                  style={{ background: "var(--border)" }}
                />
                {data.history.map((entry, index) => {
                  const style = EVENT_STYLE[entry.event] ?? {
                    label: entry.event, tone: "var(--text-muted)",
                  };
                  const detail =
                    entry.error ||
                    (entry.late_seconds
                      ? `${Math.round(entry.late_seconds / 60)} min late`
                      : entry.seconds !== undefined
                        ? `${entry.seconds}s`
                        : "");
                  return (
                    <div key={`${entry.ts}-${index}`} className="relative py-1.5">
                      <span
                        aria-hidden
                        className="absolute -left-4 top-[0.6rem] h-[7px] w-[7px] rounded-full"
                        style={{ background: style.tone, outline: "3px solid var(--surface)" }}
                      />
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span
                          className="text-[length:var(--text-xs)] font-medium"
                          style={{ color: style.tone }}
                        >
                          {style.label}
                        </span>
                        {entry.id && (
                          <span className="font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
                            {entry.id}
                          </span>
                        )}
                        <span className="ml-auto font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
                          {entry.ts.slice(11, 16)}
                        </span>
                      </div>
                      {detail && (
                        <p className="break-words text-[length:var(--text-xs)] text-[var(--text-faint)]">
                          {detail}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
