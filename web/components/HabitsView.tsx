"use client";

/**
 * Health & habits routines.
 *
 * Reads `/api/habits` for records rather than parsing `routine_status`' prose
 * — same rule as the jobs, windows and process panels, and for the same
 * reason: that wording exists to be read by a model and is free to change.
 *
 * Three things drive the layout, in order of how much they matter.
 *
 * First, whether anything can actually nag comes before the content. A list of
 * habits that will never remind you looks identical to one that will, so the
 * timer's state and the WhatsApp link get a banner rather than a footnote.
 *
 * Second, today outranks history. The question this screen answers is "what am
 * I meant to be doing", not "how did last Tuesday go", so the timeline sits at
 * the top and the heat strip is at the bottom.
 *
 * Third, the score is shown against what has actually come due, not against
 * the whole day. `score/total` reads as failure all morning, before you have
 * had the chance to do the evening steps at all — which is how a tracker stops
 * getting opened in week two.
 *
 * Every mutation goes through the existing `run` frame, so a routine edit from
 * here is the same call the model would make and gets the same safety
 * treatment. There is no second write path.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { RoutineEvent } from "@/lib/protocol";
import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Chip, Empty, Input, Panel, SectionTitle, Stat } from "./ui";
import { Icon } from "./icons";
import { Screen } from "./Shell";

/* -- shapes ------------------------------------------------------------- */

interface StepTemplate {
  id: string;
  label: string;
  at: string | null;
  after: { step: string; minutes: number } | null;
  requires: string[];
  window_minutes: number;
  points: number;
  insist: boolean;
  messages: string[];
  nag: { every_minutes: number; max: number; escalate: boolean };
}

interface StepState {
  state: string;
  due_at: string;
  expires_at: string;
  done_at: string;
  source: string;
  nags_sent: number;
  next_nag_at: string;
}

interface DayInstance {
  date: string;
  opened_at: string;
  ends_at: string;
  rest: boolean;
  score: number;
  available: number;
  total: number;
  skipped_points?: number;
  steps: Record<string, StepState>;
}

interface Routine {
  id: string;
  name: string;
  enabled: boolean;
  timezone: string;
  day_start: string;
  days: number[];
  streak: number;
  best_streak: number;
  steps: StepTemplate[];
  today: DayInstance;
}

interface HistoryRow {
  ts: string;
  event: string;
  routine?: string;
  name?: string;
  date?: string;
  rest?: boolean;
  score?: number;
  total?: number;
  judged?: number;
  skipped_points?: number;
  ratio?: number;
  kept_streak?: boolean;
  streak?: number;
  done?: number;
  missed?: number;
  skipped?: number;
  days?: number;
  from?: string;
  to?: string;
}

interface WhatsAppState {
  state: string;
  detail?: string;
  linked?: boolean;
  jid?: string;
  qr_modules?: string[];
  qr_age?: number;
  qr_stale?: boolean;
}

interface HabitsSnapshot {
  timer_active: boolean;
  now: string;
  timezone: string;
  streak_threshold: number;
  routines: Routine[];
  history: HistoryRow[];
  whatsapp: WhatsAppState;
}

/* -- vocabulary --------------------------------------------------------- */

const STEP_STYLE: Record<string, { label: string; tone: string; glyph: string; dim?: boolean }> = {
  locked: { label: "Locked", tone: "var(--text-faint)", glyph: "○", dim: true },
  due: { label: "Now", tone: "var(--accent)", glyph: "◉" },
  nagging: { label: "Reminding", tone: "var(--moderate)", glyph: "◉" },
  done: { label: "Done", tone: "var(--safe)", glyph: "●" },
  missed: { label: "Missed", tone: "var(--destructive)", glyph: "✕", dim: true },
  skipped: { label: "Skipped", tone: "var(--text-faint)", glyph: "–", dim: true },
};

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/* -- time --------------------------------------------------------------- */

function clockOf(iso: string): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return "—";
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function countdown(iso: string, from: number): string {
  const target = Date.parse(iso);
  if (Number.isNaN(target)) return "—";
  const delta = Math.round((target - from) / 1000);
  const ahead = delta >= 0;
  const s = Math.abs(delta);
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const text = hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m` : `${s % 60}s`;
  return ahead ? `in ${text}` : `${text} ago`;
}

/* -- pieces ------------------------------------------------------------- */

function Banner({
  tone,
  title,
  children,
  action,
}: {
  tone: string;
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-lg)] border px-4 py-3"
      style={{ borderColor: tone, background: "var(--surface)" }}
    >
      <div className="min-w-0">
        <div className="text-[length:var(--text-sm)] font-medium" style={{ color: tone }}>
          {title}
        </div>
        <div className="mt-0.5 text-[length:var(--text-xs)] text-[var(--text-muted)]">
          {children}
        </div>
      </div>
      {action}
    </div>
  );
}

/**
 * The pairing code, drawn from squares the server already encoded.
 *
 * The QR library lives in Python — it is already a dependency there, used by
 * `kravein whatsapp link` for the same job — so the browser gets rows of bits
 * and draws rectangles. That also means the raw code never reaches the page in
 * a form anything else would recognise as a credential.
 */
function QrCode({ modules }: { modules: string[] }) {
  if (!modules.length) {
    return <div className="h-[232px] w-[232px] animate-pulse rounded bg-[var(--surface-raised)]" />;
  }
  const n = modules.length;
  // Four modules, because that is the quiet zone the QR spec requires and a
  // scanner is entitled to reject anything less.
  const quiet = 4;
  const span = n + quiet * 2;
  return (
    <svg
      viewBox={`0 0 ${span} ${span}`}
      className="h-[232px] w-[232px]"
      shapeRendering="crispEdges"
      role="img"
      aria-label="WhatsApp pairing code"
    >
      {/* Deliberately not theme-aware. A scanner needs real white behind the
          modules, and a dark-mode QR does not scan. */}
      <rect width={span} height={span} fill="#ffffff" />
      {modules.map((row, y) =>
        Array.from(row).map((bit, x) =>
          bit === "1" ? (
            <rect key={`${x}-${y}`} x={x + quiet} y={y + quiet} width={1} height={1} fill="#000000" />
          ) : null,
        ),
      )}
    </svg>
  );
}

function WhatsAppCard({
  state,
  onLink,
  busy,
}: {
  state: WhatsAppState;
  onLink: () => void;
  busy: boolean;
}) {
  const pairing = state.state === "pairing";
  const stale = Boolean(state.qr_stale);

  return (
    <Panel title="WhatsApp">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
            {state.state === "connected"
              ? `Linked${state.jid ? ` · ${state.jid}` : ""}`
              : state.detail || state.state}
          </span>
          {state.state !== "connected" && (
            <Button variant="primary" onClick={onLink} disabled={busy || pairing}>
              {pairing ? "Waiting…" : "Link"}
            </Button>
          )}
        </div>

        {pairing && (state.qr_modules?.length ?? 0) > 0 && (
          <div className="space-y-2">
            <div className="inline-block bg-white p-2">
              <QrCode modules={state.qr_modules ?? []} />
            </div>
            <p className="text-[length:var(--text-xs)] text-[var(--text-muted)]">
              WhatsApp → Settings → Linked devices → Link a device.
              {/* No countdown: the code's lifetime is not constant (the first
                  is 60s, refreshes are 20s), so a timer here would regularly
                  call a live code dead. The server pushes replacements; the
                  page just draws the newest one. */}
              {stale ? (
                <span className="text-[var(--moderate)]">
                  {" "}The code went stale — press Link again.
                </span>
              ) : (
                " It refreshes on its own until you scan it."
              )}
            </p>
          </div>
        )}

        {pairing && !(state.qr_modules?.length) && (
          <p className="text-[length:var(--text-xs)] text-[var(--text-muted)]">
            Waiting for a code from WhatsApp…
          </p>
        )}

        <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
          The last rung of the reminder ladder. Desktop notifications always
          fire first, so nothing is lost when this is not linked.
        </p>
      </div>
    </Panel>
  );
}

function StepRow({
  step,
  state,
  routine,
  now,
  busy,
  flash,
  onDone,
  onSkip,
  onRemove,
}: {
  step: StepTemplate;
  state: StepState | undefined;
  routine: Routine;
  now: number;
  busy: boolean;
  flash: boolean;
  onDone: () => void;
  onSkip: () => void;
  onRemove: () => void;
}) {
  const style = STEP_STYLE[state?.state ?? "locked"] ?? STEP_STYLE.locked;
  const actionable = state?.state === "due" || state?.state === "nagging";
  const blocker = step.after?.step
    ? routine.steps.find((s) => s.id === step.after?.step)?.label
    : "";

  return (
    <div
      className="flex items-center gap-3 rounded-[var(--radius-lg)] border px-3 py-2.5"
      style={{
        borderColor: flash ? "var(--moderate)" : "var(--border)",
        background: "var(--surface)",
        opacity: style.dim ? 0.62 : 1,
        transition: "border-color var(--motion-slow) var(--ease), opacity var(--motion-fast) var(--ease)",
      }}
    >
      <span className="w-4 shrink-0 text-center" style={{ color: style.tone }}>
        {style.glyph}
      </span>

      <span className="w-14 shrink-0 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)] tabular-nums">
        {state?.due_at ? clockOf(state.due_at) : "—"}
      </span>

      <div className="min-w-0 flex-1">
        <div className="truncate text-[length:var(--text-sm)]">{step.label}</div>
        <div className="truncate text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {state?.state === "done" && state.done_at
            ? `done ${clockOf(state.done_at)}${state.source ? ` · ${state.source}` : ""}`
            : state?.state === "locked" && !state.due_at && blocker
              ? `after ${blocker}`
              : state?.state === "nagging"
                ? `reminded ${state.nags_sent}× · ${
                    state.expires_at ? countdown(state.expires_at, now) + " left" : "no deadline"
                  }`
                : state?.state === "due" && state.expires_at
                  ? `${countdown(state.expires_at, now)} left`
                  : state?.state === "skipped" && state.source
                    ? state.source
                    : `${step.points} pts`}
        </div>
      </div>

      {step.insist && (
        <Chip tone="var(--moderate)" active title="Reminders start one rung louder">
          insist
        </Chip>
      )}

      <span className="shrink-0 text-[length:var(--text-xs)] tabular-nums text-[var(--text-faint)]">
        {step.points}pt
      </span>

      <span
        className="w-20 shrink-0 text-right text-[length:var(--text-xs)]"
        style={{ color: style.tone }}
      >
        {style.label}
      </span>

      {/* Only offered where it will actually work. A Done button on a locked
          step that then refuses the click teaches people the UI lies. */}
      <div className="flex shrink-0 gap-1.5">
        {actionable && (
          <>
            <Button variant="primary" onClick={onDone} disabled={busy}>
              Done
            </Button>
            <Button variant="ghost" onClick={onSkip} disabled={busy} title="Not today">
              Skip
            </Button>
          </>
        )}
        <Button variant="ghost" onClick={onRemove} disabled={busy} title="Delete this step">
          <Icon name="trash" size={15} />
        </Button>
      </div>
    </div>
  );
}

/**
 * Add a step.
 *
 * State is seeded from props and the whole form is remounted with a `key`
 * rather than synced by an effect. A polling panel re-renders constantly, and
 * an effect that copies props into state on every one of those renders wipes
 * whatever is half-typed.
 */
function StepForm({
  routine,
  busy,
  onAdd,
  onCancel,
}: {
  routine: Routine;
  busy: boolean;
  onAdd: (args: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState("");
  const [mode, setMode] = useState<"at" | "after">(routine.steps.length ? "after" : "at");
  const [at, setAt] = useState("07:00");
  const [afterStep, setAfterStep] = useState(routine.steps[routine.steps.length - 1]?.id ?? "");
  const [afterMinutes, setAfterMinutes] = useState("30");
  const [windowMinutes, setWindowMinutes] = useState("60");
  const [points, setPoints] = useState("10");
  const [insist, setInsist] = useState(false);
  const [messages, setMessages] = useState("");

  const ready =
    label.trim().length > 0 &&
    (mode === "at" ? /^\d{1,2}:\d{2}$/.test(at) : Boolean(afterStep));

  const submit = () => {
    if (!ready || busy) return;
    onAdd({
      routine_id: routine.id,
      label: label.trim(),
      at: mode === "at" ? at : "",
      after_step: mode === "after" ? afterStep : "",
      after_minutes: mode === "after" ? Number(afterMinutes) || 0 : 0,
      window_minutes: Number(windowMinutes) || 60,
      points: Number(points) || 0,
      insist,
      messages: messages.trim(),
    });
  };

  return (
    <div className="space-y-3 rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] p-3">
      <Input value={label} onChange={setLabel} placeholder="What is the habit? e.g. Breakfast" className="w-full" />

      <div className="flex flex-wrap items-center gap-2">
        <Chip active={mode === "at"} onClick={() => setMode("at")}>
          At a time
        </Chip>
        <Chip
          active={mode === "after"}
          onClick={() => setMode("after")}
          title={routine.steps.length ? undefined : "Add a first step before chaining"}
        >
          After another step
        </Chip>

        {mode === "at" ? (
          <input
            type="time"
            value={at}
            onChange={(event) => setAt(event.target.value)}
            className="rounded-[var(--radius-full)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[length:var(--text-sm)] outline-none focus:border-[var(--accent)]"
          />
        ) : (
          <>
            <select
              value={afterStep}
              onChange={(event) => setAfterStep(event.target.value)}
              className="rounded-[var(--radius-full)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[length:var(--text-sm)] outline-none focus:border-[var(--accent)]"
            >
              {routine.steps.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <Input
              value={afterMinutes}
              onChange={setAfterMinutes}
              placeholder="30"
              mono
              className="w-16"
            />
            <span className="text-[length:var(--text-xs)] text-[var(--text-muted)]">
              min after it is confirmed
            </span>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[length:var(--text-xs)] text-[var(--text-muted)]">
        <span>window</span>
        <Input value={windowMinutes} onChange={setWindowMinutes} mono className="w-16" />
        <span>min · worth</span>
        <Input value={points} onChange={setPoints} mono className="w-14" />
        <span>pts</span>
        <Chip active={insist} tone="var(--moderate)" onClick={() => setInsist((v) => !v)}>
          insist
        </Chip>
      </div>

      <Input
        value={messages}
        onChange={setMessages}
        placeholder="Reminder wordings, separated by | — leave empty to use the label"
        className="w-full"
      />

      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={submit} disabled={!ready || busy}>
          {busy ? "Adding…" : "Add step"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>

      <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
        {mode === "after"
          ? "Timed from when the previous step is actually confirmed, not from when it was due. It stays locked until then."
          : "A fixed time each day. New steps start counting from the next routine day."}
      </p>
    </div>
  );
}

function RoutineCard({
  routine,
  now,
  busy,
  flashed,
  act,
}: {
  routine: Routine;
  now: number;
  busy: boolean;
  flashed: Set<string>;
  act: (skill: string, args: Record<string, unknown>) => void;
}) {
  const [adding, setAdding] = useState(false);
  const day = routine.today;

  /**
   * Schedule order, including steps that do not have a time yet.
   *
   * A chained step has no due time until its predecessor is confirmed, so
   * ranking purely by `due_at` sorts every one of them to the bottom — the
   * first render put Breakfast and Meds *after* the 9pm walk, which is not
   * the order anyone is going to do them in. An undated step instead inherits
   * its anchor's position and sits just behind it, so the chain reads down the
   * page the way it will actually happen.
   */
  const ordered = useMemo(() => {
    const byId = new Map(routine.steps.map((s) => [s.id, s]));
    const cache = new Map<string, number>();

    const rank = (step: StepTemplate, depth = 0): number => {
      const cached = cache.get(step.id);
      if (cached !== undefined) return cached;

      const due = day.steps[step.id]?.due_at;
      let value: number;
      if (due) {
        value = Date.parse(due);
      } else {
        const anchor = step.after?.step ? byId.get(step.after.step) : undefined;
        // `depth` bounds the walk. Cycles are refused when a step is written,
        // but a rendering pass must not be the thing that discovers otherwise.
        value =
          anchor && depth < routine.steps.length
            ? rank(anchor, depth + 1) + Math.max(1, step.after?.minutes ?? 1) * 60_000
            : Number.MAX_SAFE_INTEGER;
      }
      cache.set(step.id, value);
      return value;
    };

    return [...routine.steps].sort((a, b) => rank(a) - rank(b));
  }, [routine.steps, day.steps]);

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {routine.name}
          {!routine.enabled && (
            <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">paused</span>
          )}
        </span>
      }
      actions={
        <div className="flex items-center gap-2">
          <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
            day starts {routine.day_start} · {routine.days.length === 7
              ? "every day"
              : routine.days.map((d) => DAY_NAMES[d]).join(" ")}
          </span>
          <Button
            variant="ghost"
            onClick={() => act("pause_routine", { routine_id: routine.id, resume: !routine.enabled })}
            disabled={busy}
          >
            {routine.enabled ? "Pause" : "Resume"}
          </Button>
          <Button variant="ghost" onClick={() => setAdding((v) => !v)} disabled={busy}>
            {adding ? "Close" : "Add step"}
          </Button>
        </div>
      }
    >
      <div className="space-y-2">
        {day.rest ? (
          <Empty>Rest day — nothing scheduled, and the streak is safe.</Empty>
        ) : ordered.length === 0 ? (
          <Empty>No steps yet. Add the first one, then chain the rest behind it.</Empty>
        ) : (
          ordered.map((step) => (
            <StepRow
              key={step.id}
              step={step}
              state={day.steps[step.id]}
              routine={routine}
              now={now}
              busy={busy}
              flash={flashed.has(`${routine.id}/${step.id}`)}
              onDone={() => act("complete_step", { routine_id: routine.id, step_id: step.id })}
              onSkip={() => act("skip_step", { routine_id: routine.id, step_id: step.id })}
              onRemove={() =>
                act("delete_routine_step", { routine_id: routine.id, step_id: step.id })
              }
            />
          ))
        )}

        {adding && (
          <StepForm
            key={`${routine.id}-${routine.steps.length}`}
            routine={routine}
            busy={busy}
            onAdd={(args) => {
              act("add_routine_step", args);
              setAdding(false);
            }}
            onCancel={() => setAdding(false)}
          />
        )}
      </div>
    </Panel>
  );
}

function HeatStrip({ rows }: { rows: HistoryRow[] }) {
  const days = rows.filter((r) => r.event === "closed").slice(0, 30).reverse();
  if (!days.length) {
    return <Empty>No completed days yet. Come back tomorrow.</Empty>;
  }
  return (
    <div className="overflow-x-auto">
      <div className="flex min-w-min items-end gap-1 pb-1">
        {days.map((row, index) => {
          const ratio = row.rest ? -1 : (row.ratio ?? 0);
          const colour =
            ratio < 0
              ? "var(--border)"
              : ratio >= 0.8
                ? "var(--safe)"
                : ratio >= 0.5
                  ? "var(--moderate)"
                  : "var(--destructive)";
          return (
            <div
              key={`${row.routine}-${row.date}-${index}`}
              className="h-7 w-7 shrink-0 rounded-[4px]"
              style={{ background: colour, opacity: ratio < 0 ? 0.5 : 0.35 + ratio * 0.65 }}
              title={
                row.rest
                  ? `${row.date} — rest day`
                  : `${row.date} — ${row.score}/${row.judged ?? row.total} (${Math.round(
                      (row.ratio ?? 0) * 100,
                    )}%)${row.skipped_points ? `, ${row.skipped_points}pt skipped` : ""}`
              }
            />
          );
        })}
      </div>
    </div>
  );
}

/* -- the screen --------------------------------------------------------- */

export function HabitsView({
  run,
  routineEvents,
}: {
  run: RunSkill;
  routineEvents: RoutineEvent[];
}) {
  const { data, error, reload } = useResource<HabitsSnapshot>("/api/habits", 15000);
  const skill = useSkill(run);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [linking, setLinking] = useState(false);

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // A reminder just went out; the panel is stale by definition.
  const latestEvent = routineEvents[0]?.at ?? 0;
  useEffect(() => {
    if (latestEvent) reload();
  }, [latestEvent, reload]);

  // While pairing, the code rotates every 20-30s, so a 15s poll would show an
  // expired square for up to half its life. Poll hard, but only then.
  const pairing = data?.whatsapp?.state === "pairing";
  useEffect(() => {
    if (!pairing) return;
    const timer = setInterval(reload, 2000);
    return () => clearInterval(timer);
  }, [pairing, reload]);

  const flashed = useMemo(
    () =>
      new Set(
        routineEvents.filter((e) => now - e.at < 12000).map((e) => `${e.routine}/${e.step}`),
      ),
    [routineEvents, now],
  );

  const act = useCallback(
    (name: string, args: Record<string, unknown>) => {
      void skill.call(name, args).then(() => reload());
    },
    [skill, reload],
  );

  const link = useCallback(async () => {
    setLinking(true);
    try {
      await fetch("/api/whatsapp/link", { method: "POST" });
    } catch {
      /* the panel reports the state it reads back, not this call */
    } finally {
      setLinking(false);
      reload();
    }
  }, [reload]);

  if (error) return <Screen><Empty>Could not read routines: {error}</Empty></Screen>;
  if (!data) return <Screen><Empty>Reading routines…</Empty></Screen>;

  const busy = Boolean(skill.running);
  const active = data.routines.filter((r) => r.enabled && !r.today.rest);

  const score = active.reduce((sum, r) => sum + r.today.score, 0);
  const available = active.reduce((sum, r) => sum + r.today.available, 0);
  const total = active.reduce((sum, r) => sum + r.today.total, 0);
  const counts = active.reduce(
    (acc, r) => {
      for (const step of Object.values(r.today.steps)) {
        if (step.state === "done") acc.done += 1;
        else if (step.state === "due" || step.state === "nagging") acc.pending += 1;
        else if (step.state === "locked") acc.locked += 1;
        else if (step.state === "missed") acc.missed += 1;
      }
      return acc;
    },
    { done: 0, pending: 0, locked: 0, missed: 0 },
  );
  const streak = Math.max(0, ...data.routines.map((r) => r.streak));

  // The next thing that will actually happen, across every routine.
  const upcoming = active
    .flatMap((r) =>
      r.steps
        .map((s) => ({ routine: r, step: s, state: r.today.steps[s.id] }))
        .filter((row) => row.state && ["due", "nagging", "locked"].includes(row.state.state))
        .filter((row) => row.state?.due_at),
    )
    .sort((a, b) => Date.parse(a.state!.due_at) - Date.parse(b.state!.due_at))[0];

  const percent = available ? Math.round((score / available) * 100) : 0;

  return (
    <Screen
      subtitle={
        `${data.routines.length} routine${data.routines.length === 1 ? "" : "s"} · ` +
        `${data.timezone} · streak survives at ` +
        `${Math.round(data.streak_threshold * 100)}% of judged points`
      }
      actions={
        <>
          <Button onClick={() => setCreating((v) => !v)}>
            {creating ? "Cancel" : "New routine"}
          </Button>
          <Button onClick={reload}>Refresh</Button>
        </>
      }
    >
      <div className="space-y-4">
        {!data.timer_active && (
          <Banner tone="var(--critical)" title="The timer is not running">
            Nothing will remind you. Start it with{" "}
            <code className="font-[family-name:var(--font-mono)]">
              systemctl --user enable --now kravein-jobs.timer
            </code>
            .
          </Banner>
        )}

        {creating && (
          <Panel title="New routine">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={newName}
                onChange={setNewName}
                placeholder="Name it — Morning, Evening wind-down…"
                className="min-w-[16rem] flex-1"
                onEnter={() => {
                  if (!newName.trim()) return;
                  act("add_routine", { name: newName.trim() });
                  setNewName("");
                  setCreating(false);
                }}
              />
              <Button
                variant="primary"
                disabled={!newName.trim() || busy}
                onClick={() => {
                  act("add_routine", { name: newName.trim() });
                  setNewName("");
                  setCreating(false);
                }}
              >
                Create
              </Button>
            </div>
          </Panel>
        )}

        {/* Measured against what has come due, not the whole day. */}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {/* "0 / 0" is what `score/available` reads as before anything has
              come due, which is worse than no number at all. Until the first
              step opens, this shows what the day is worth instead. */}
          <Stat
            label="Today"
            value={available ? score : 0}
            unit={available ? `/ ${available}` : "pts"}
            detail={
              available
                ? `${total} pts scheduled`
                : total
                  ? `nothing due yet · ${total} pts today`
                  : "nothing scheduled"
            }
            percent={available ? percent : undefined}
            tone={!available || percent >= 80 ? "accent" : percent >= 50 ? "moderate" : "destructive"}
          />
          <Stat
            label="Done"
            value={counts.done}
            detail={`${counts.pending} open · ${counts.locked} locked`}
          />
          <Stat
            label="Missed"
            value={counts.missed}
            tone={counts.missed ? "destructive" : "accent"}
            detail={counts.missed ? "counts against the streak" : "nothing missed"}
          />
          <Stat
            label="Streak"
            value={streak}
            unit={streak === 1 ? "day" : "days"}
            detail={
              upcoming
                ? `next: ${upcoming.step.label} ${countdown(upcoming.state!.due_at, now)}`
                : "nothing else due today"
            }
          />
        </div>

        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="space-y-4">
            <SectionTitle>Today</SectionTitle>
            {data.routines.length === 0 ? (
              <Empty>
                No routines yet. Create one above, or just ask in the Console —
                “wake me at seven, breakfast half an hour after I’m actually up”.
              </Empty>
            ) : (
              data.routines.map((routine) => (
                <RoutineCard
                  key={routine.id}
                  routine={routine}
                  now={now}
                  busy={busy}
                  flashed={flashed}
                  act={act}
                />
              ))
            )}
          </div>

          <div className="space-y-4">
            <WhatsAppCard state={data.whatsapp} onLink={link} busy={linking} />

            <Panel title="Last 30 days">
              <div className="space-y-3">
                <HeatStrip rows={data.history} />
                <div className="space-y-1">
                  {data.history
                    .filter((r) => r.event === "closed")
                    .slice(0, 6)
                    .map((row, index) => (
                      <div
                        key={`${row.routine}-${row.date}-${index}`}
                        className="flex items-baseline justify-between gap-3 text-[length:var(--text-xs)]"
                      >
                        <span className="text-[var(--text-muted)]">{row.date}</span>
                        <span className="truncate text-[var(--text-faint)]">{row.name}</span>
                        <span
                          className="tabular-nums"
                          style={{
                            color: row.kept_streak ? "var(--safe)" : "var(--destructive)",
                          }}
                        >
                          {row.rest ? "rest" : `${row.score}/${row.judged ?? row.total}`}
                        </span>
                      </div>
                    ))}
                </div>
              </div>
            </Panel>

            {skill.output && (
              <Panel title="Last action">
                <pre className="whitespace-pre-wrap break-words text-[length:var(--text-xs)] text-[var(--text-muted)]">
                  {skill.output}
                </pre>
              </Panel>
            )}
            {skill.error && (
              <Banner tone="var(--destructive)" title="That did not work">
                {skill.error}
              </Banner>
            )}
          </div>
        </div>
      </div>
    </Screen>
  );
}
