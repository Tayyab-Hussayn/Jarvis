"use client";

/**
 * Scheduled jobs and reminders.
 *
 * Reads `/api/jobs` for records rather than parsing `list_jobs`' prose — same
 * rule as the windows and process panels, and for the same reason: that
 * wording exists to be read by a model and is free to change.
 *
 * The one thing this screen must never do is look healthy when it is not. A
 * list of jobs that cannot fire is indistinguishable from one that can, so the
 * timer's state is the first thing on the page and everything else is muted
 * behind it when it is off.
 */

import { useEffect, useMemo, useState } from "react";

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

const STATE_TONE: Record<string, string> = {
  pending: "var(--accent)",
  running: "var(--moderate)",
  done: "var(--text-faint)",
  failed: "var(--critical)",
  missed: "var(--destructive)",
};

const EVENT_TONE: Record<string, string> = {
  ran: "var(--accent)",
  scheduled: "var(--text-muted)",
  cancelled: "var(--text-faint)",
  failed: "var(--critical)",
  missed: "var(--destructive)",
  reaped: "var(--moderate)",
  tick_error: "var(--critical)",
  notify_failed: "var(--moderate)",
};

/** "in 3h 12m" / "4m ago". Rendered client-side so it stays live between polls. */
function relative(iso: string, from: number): string {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return "—";
  const delta = Math.round((target - from) / 1000);
  const ahead = delta >= 0;
  const s = Math.abs(delta);

  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;

  let text: string;
  if (days) text = `${days}d ${hours}h`;
  else if (hours) text = `${hours}h ${minutes}m`;
  else if (minutes) text = `${minutes}m`;
  else text = `${seconds}s`;

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

/* -- the banner that matters ------------------------------------------- */

function TimerBanner({ active }: { active: boolean }) {
  if (active) {
    return (
      <div
        className="flex items-center gap-2 rounded-[var(--radius)] px-3 py-2 text-[length:var(--text-xs)]"
        style={{ background: "var(--accent-dim)", color: "var(--accent)" }}
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: "var(--accent)", animation: "pulse 2s ease-in-out infinite" }}
        />
        Tick timer running — jobs fire every minute, with or without Kravein open.
      </div>
    );
  }
  return (
    <div
      className="rounded-[var(--radius)] border-l-2 px-3 py-2.5 text-[length:var(--text-sm)]"
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

/* -- one job ------------------------------------------------------------ */

function JobCard({
  job,
  now,
  flash,
  onCancel,
  busy,
}: {
  job: JobRow;
  now: number;
  flash: boolean;
  onCancel: () => void;
  busy: boolean;
}) {
  const live = job.state === "pending" || job.state === "running";
  const tone = STATE_TONE[job.state] ?? "var(--text-faint)";

  return (
    <div
      className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-raised)] px-3.5 py-3"
      style={{
        opacity: live ? 1 : 0.62,
        // A job that just fired is worth catching the eye for one beat.
        animation: flash ? "node-in var(--motion-base) var(--ease)" : undefined,
        borderColor: flash ? "var(--accent)" : undefined,
        transition: "border-color var(--motion-base) var(--ease)",
      }}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: tone,
            animation: job.state === "running" ? "pulse 1.2s ease-in-out infinite" : undefined,
          }}
        />
        <div className="min-w-0 flex-1">
          <p className="break-words text-[length:var(--text-sm)]">{job.prompt}</p>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            <span className="font-[family-name:var(--font-mono)]">{job.id}</span>
            <span style={{ color: tone }}>{job.state}</span>
            {live && (
              <span
                className="font-[family-name:var(--font-mono)]"
                style={{ color: "var(--text-muted)" }}
                title={absolute(job.next_run)}
              >
                {relative(job.next_run, now)}
              </span>
            )}
            <span title={job.next_run}>{absolute(job.next_run)}</span>
            {job.cron && (
              <span className="font-[family-name:var(--font-mono)]" style={{ color: "var(--moderate)" }}>
                {job.cron}
              </span>
            )}
            {job.kind === "agent" && (
              <span style={{ color: "var(--moderate)" }}>runs tools</span>
            )}
            {job.speak && <span>speaks</span>}
            {job.runs > 0 && <span>{job.runs} run{job.runs === 1 ? "" : "s"}</span>}
            {job.attempts > 0 && (
              <span style={{ color: "var(--moderate)" }}>retry {job.attempts}</span>
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

        {live && (
          <Button variant="ghost" onClick={onCancel} disabled={busy} title="Cancel this job">
            <Icon name="trash" size={14} />
          </Button>
        )}
      </div>
    </div>
  );
}

/* -- the screen --------------------------------------------------------- */

export function JobsView({
  run,
  jobEvents,
}: {
  run: RunSkill;
  jobEvents: JobEvent[];
}) {
  const { data, error, reload } = useResource<JobsSnapshot>("/api/jobs", 10000);
  const skill = useSkill(run);

  const [message, setMessage] = useState("");
  const [when, setWhen] = useState("");
  const [repeat, setRepeat] = useState("");
  const [runsTools, setRunsTools] = useState(false);

  // Countdowns tick locally so they stay honest between the 10s polls —
  // otherwise "in 1m" sits there for ten seconds after it should read "now".
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // A job firing arrives over the socket; refresh so the store view agrees.
  const latestEvent = jobEvents[0]?.at ?? 0;
  useEffect(() => {
    if (latestEvent) reload();
  }, [latestEvent, reload]);

  const recentlyFired = useMemo(
    () => new Set(jobEvents.filter((e) => now - e.at < 10000).map((e) => e.id)),
    [jobEvents, now],
  );

  const submit = async () => {
    if (!message.trim()) return;
    await skill.call("schedule_job", {
      prompt: message.trim(),
      when: when.trim(),
      repeat: repeat.trim(),
      kind: runsTools ? "agent" : "notify",
    });
    setMessage("");
    setWhen("");
    setRepeat("");
    reload();
  };

  const cancel = async (id: string) => {
    await skill.call("cancel_job", { job_id: id });
    reload();
  };

  if (error) {
    return <Screen><Empty>Could not read scheduled jobs: {error}</Empty></Screen>;
  }
  if (!data) {
    return <Screen><Empty>Reading scheduled jobs…</Empty></Screen>;
  }

  const live = data.jobs.filter((j) => j.state === "pending" || j.state === "running");
  const finished = data.jobs.filter((j) => j.state !== "pending" && j.state !== "running");

  return (
    <Screen
      subtitle={
        `${live.length} scheduled · ${data.timezone} · ` +
        `skips anything more than ${Math.round(data.grace_seconds / 60)} min late`
      }
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      <div className="min-h-0 space-y-4 overflow-y-auto pb-6">
        <TimerBanner active={data.timer_active} />

        <div className="grid items-start gap-4 lg:grid-cols-[1.4fr_1fr]">
          <div className="space-y-4">
            <Panel title="New reminder">
              <div className="space-y-2.5">
                <Input
                  value={message}
                  onChange={setMessage}
                  placeholder="What should it say, or do?"
                />
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-1 block text-[length:var(--text-xs)] text-[var(--text-faint)]">
                      When
                    </span>
                    <Input
                      value={when}
                      onChange={setWhen}
                      placeholder="2026-09-07T17:00 or 20m"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[length:var(--text-xs)] text-[var(--text-faint)]">
                      Repeat (cron)
                    </span>
                    <Input
                      value={repeat}
                      onChange={setRepeat}
                      placeholder="0 9 * * *"
                    />
                  </label>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Chip
                    active={runsTools}
                    tone="var(--moderate)"
                    onClick={() => setRunsTools((v) => !v)}
                  >
                    {runsTools ? "Runs tools" : "Just notifies"}
                  </Chip>
                  <Button
                    variant="primary"
                    onClick={submit}
                    disabled={!message.trim() || Boolean(skill.running)}
                  >
                    {Boolean(skill.running) ? "Scheduling…" : "Schedule"}
                  </Button>
                </div>

                <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  {runsTools
                    ? "Runs a full agent turn unattended. Destructive actions are refused, not approved — nobody is there to confirm them."
                    : "Shows a desktop notification. No model call, so it cannot fail or surprise you."}
                  {" "}Or just ask in the Console: “remind me to call the client at 5pm tomorrow”.
                </p>

                <SkillOutput state={skill} />
              </div>
            </Panel>

            <div>
              <SectionTitle>
                Scheduled{live.length ? ` · ${live.length}` : ""}
              </SectionTitle>
              {live.length === 0 ? (
                <Empty>Nothing scheduled.</Empty>
              ) : (
                <div className="space-y-2">
                  {live
                    .slice()
                    .sort((a, b) => (a.next_run || "").localeCompare(b.next_run || ""))
                    .map((job) => (
                      <JobCard
                        key={job.id}
                        job={job}
                        now={now}
                        flash={recentlyFired.has(job.id)}
                        busy={Boolean(skill.running)}
                        onCancel={() => cancel(job.id)}
                      />
                    ))}
                </div>
              )}
            </div>

            {finished.length > 0 && (
              <div>
                <SectionTitle>Finished · {finished.length}</SectionTitle>
                <div className="space-y-2">
                  {finished
                    .slice()
                    .sort((a, b) => (b.last_run || b.next_run || "").localeCompare(a.last_run || a.next_run || ""))
                    .slice(0, 8)
                    .map((job) => (
                      <JobCard
                        key={job.id}
                        job={job}
                        now={now}
                        flash={false}
                        busy={Boolean(skill.running)}
                        onCancel={() => cancel(job.id)}
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
              <div className="divide-y divide-[var(--border)]">
                {data.history.map((entry, index) => (
                  <div
                    key={`${entry.ts}-${index}`}
                    className="flex items-baseline gap-2 py-1.5 text-[length:var(--text-xs)]"
                  >
                    <span className="shrink-0 font-[family-name:var(--font-mono)] text-[var(--text-faint)]">
                      {entry.ts.slice(11, 19)}
                    </span>
                    <span
                      className="shrink-0 font-medium"
                      style={{ color: EVENT_TONE[entry.event] ?? "var(--text-muted)" }}
                    >
                      {entry.event}
                    </span>
                    <span className="shrink-0 font-[family-name:var(--font-mono)] text-[var(--text-faint)]">
                      {entry.id ?? ""}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[var(--text-faint)]">
                      {entry.error ||
                        (entry.late_seconds
                          ? `${Math.round(entry.late_seconds / 60)} min late`
                          : entry.seconds !== undefined
                            ? `${entry.seconds}s`
                            : "")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
