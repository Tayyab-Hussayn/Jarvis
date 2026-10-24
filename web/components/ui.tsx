"use client";

/**
 * Shared primitives.
 *
 * Hand-written rather than pulled from a component kit: these have no
 * accessibility behaviour worth outsourcing. Radix earns its place in the
 * command palette (focus trap, portal, roving tabindex) and nowhere else yet.
 */

import type { ReactNode } from "react";

import type { Danger } from "@/lib/protocol";
import { classNames } from "@/lib/format";

/* -- panels ---------------------------------------------------------- */

export function Panel({
  title,
  actions,
  children,
  className,
  padded = true,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={classNames(
        "rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]",
        className,
      )}
      style={{ boxShadow: "var(--shadow-card)" }}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
          <h2 className="text-[length:var(--text-sm)] font-medium">{title}</h2>
          {actions}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

/**
 * The light card floating over the dark ground — the reference's signature
 * move. Uses its own text tokens rather than inverting, because inverting
 * inside a dark app is where contrast bugs breed.
 */
export function LightPanel({
  title,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={classNames(
        "overflow-hidden rounded-[var(--radius-lg)] border border-[var(--panel-border)]",
        className,
      )}
      style={{
        background: "var(--panel)",
        color: "var(--panel-text)",
        boxShadow: "var(--shadow-panel)",
      }}
    >
      {(title || actions) && (
        <header
          className="flex items-center justify-between gap-3 border-b px-4 py-3"
          style={{ borderColor: "var(--panel-border)" }}
        >
          <h2 className="text-[length:var(--text-sm)] font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

/* -- stats ----------------------------------------------------------- */

export function Stat({
  label,
  value,
  unit,
  detail,
  percent,
  tone = "accent",
  spark,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  detail?: ReactNode;
  percent?: number;
  tone?: "accent" | "moderate" | "destructive" | "critical";
  spark?: number[];
}) {
  const colour = tone === "accent" ? "var(--accent)" : `var(--${tone})`;
  return (
    <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
        {label}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1">
        <span
          className="font-semibold tabular-nums"
          style={{
            fontSize: "var(--text-stat)",
            letterSpacing: "var(--tracking-stat)",
            lineHeight: 1.05,
          }}
        >
          {value}
        </span>
        {unit && (
          <span className="text-[length:var(--text-sm)] text-[var(--text-muted)]">{unit}</span>
        )}
      </div>
      <div className="mt-1 min-h-[1.15rem] text-[length:var(--text-xs)] text-[var(--text-muted)]">
        {detail}
      </div>

      {/* Every card reserves the same meter area whether or not it has data.
          Previously CPU had a sparkline + bar, Disk a bar only and Uptime
          neither, so one row of cards showed three different anatomies — and
          the sparkline cards visibly reflowed seconds after load as history
          accumulated. Reserving the space keeps the row stable and uniform. */}
      <div className="mt-2.5 h-8">
        {spark && spark.length >= 5 ? (
          <Sparkline values={spark} colour={colour} />
        ) : (
          <div className="flex h-full items-end">
            {percent !== undefined && (
            <div
              className="h-1.5 w-full overflow-hidden rounded-[var(--radius-full)] bg-[var(--surface-sunken)]"
              role={percent !== undefined ? "progressbar" : undefined}
              aria-valuenow={percent !== undefined ? Math.round(percent) : undefined}
              aria-valuemin={percent !== undefined ? 0 : undefined}
              aria-valuemax={percent !== undefined ? 100 : undefined}
              aria-label={percent !== undefined ? label : undefined}
            >
              <div
                className="h-full rounded-[var(--radius-full)]"
                style={{
                  width: `${Math.min(100, Math.max(0, percent))}%`,
                  background: colour,
                  transition: `width var(--motion-base) var(--ease)`,
                }}
              />
            </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export function Sparkline({ values, colour }: { values: number[]; colour: string }) {
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 100;
      const y = 100 - ((v - min) / span) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg
      className="h-8 w-full"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline
        points={points}
        fill="none"
        stroke={colour}
        strokeWidth="2.5"
        vectorEffect="non-scaling-stroke"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* -- chips and badges ------------------------------------------------ */

export function Chip({
  children,
  active,
  onClick,
  tone,
  title,
}: {
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
  tone?: string;
  title?: string;
}) {
  const colour = tone ?? "var(--accent)";
  const interactive = Boolean(onClick);
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={!interactive}
      aria-pressed={interactive ? Boolean(active) : undefined}
      className={classNames(
        "shrink-0 rounded-[var(--radius-full)] border px-3 py-1 text-[length:var(--text-xs)] font-medium whitespace-nowrap",
        interactive && "cursor-pointer",
      )}
      style={{
        transition: `all var(--motion-fast) var(--ease)`,
        borderColor: active ? colour : "var(--border)",
        color: active ? (tone ? colour : "var(--accent-contrast)") : "var(--text-muted)",
        background: active
          ? tone
            ? "transparent"
            : colour
          : "transparent",
      }}
    >
      {children}
    </button>
  );
}

const DANGER_TONE: Record<Danger, { fg: string; bg: string }> = {
  SAFE: { fg: "var(--safe)", bg: "var(--safe-dim)" },
  MODERATE: { fg: "var(--moderate)", bg: "var(--moderate-dim)" },
  DESTRUCTIVE: { fg: "var(--destructive)", bg: "var(--destructive-dim)" },
  CRITICAL: { fg: "var(--critical)", bg: "var(--critical-dim)" },
};

export function dangerColour(danger: Danger): string {
  return (DANGER_TONE[danger] ?? DANGER_TONE.SAFE).fg;
}

export function DangerBadge({ danger }: { danger: Danger }) {
  const tone = DANGER_TONE[danger] ?? DANGER_TONE.SAFE;
  return (
    <span
      className="shrink-0 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[length:var(--text-xs)] font-semibold uppercase tracking-[0.06em]"
      style={{ color: tone.fg, background: tone.bg }}
    >
      {danger.toLowerCase()}
    </span>
  );
}

/* -- controls -------------------------------------------------------- */

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  title,
  full,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  title?: string;
  full?: boolean;
}) {
  const styles: Record<string, string> = {
    default:
      "border-[var(--border-strong)] bg-[var(--surface-raised)] text-[var(--text)] hover:border-[var(--text-faint)]",
    primary:
      "border-transparent bg-[var(--accent)] text-[var(--accent-contrast)] hover:bg-[var(--accent-hover)]",
    danger:
      "border-[var(--critical)] bg-transparent text-[var(--critical)] hover:bg-[var(--critical-dim)]",
    ghost:
      "border-transparent bg-transparent text-[var(--text-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--text)]",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={classNames(
        "rounded-[var(--radius-full)] border px-3.5 py-1.5 text-[length:var(--text-sm)] font-medium disabled:cursor-not-allowed disabled:opacity-40 max-sm:min-h-10",
        full && "w-full",
        styles[variant],
      )}
      style={{ transition: `all var(--motion-fast) var(--ease)` }}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  value,
  tone,
  mono = true,
}: {
  label: string;
  value: ReactNode;
  tone?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="shrink-0 text-[length:var(--text-sm)] text-[var(--text-muted)]">{label}</dt>
      <dd
        className={classNames(
          "min-w-0 truncate text-right text-[length:var(--text-xs)]",
          mono && "font-[family-name:var(--font-mono)]",
        )}
        style={tone ? { color: tone } : undefined}
      >
        {value}
      </dd>
    </div>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  onEnter,
  mono,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  onEnter?: () => void;
  mono?: boolean;
  className?: string;
}) {
  return (
    <input
      value={value}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && onEnter) onEnter();
      }}
      className={classNames(
        "rounded-[var(--radius-full)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3.5 py-1.5 text-[length:var(--text-sm)] outline-none placeholder:text-[var(--text-faint)] focus:border-[var(--accent)]",
        mono && "font-[family-name:var(--font-mono)]",
        className,
      )}
      style={{ transition: `border-color var(--motion-fast) var(--ease)` }}
    />
  );
}

/* -- states ---------------------------------------------------------- */

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="py-10 text-center text-[length:var(--text-sm)] text-[var(--text-faint)]">
      {children}
    </p>
  );
}

export function SectionTitle({
  children,
  count,
  actions,
}: {
  children: ReactNode;
  count?: number;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-center gap-3">
      <h2 className="text-[length:var(--text-lg)] font-semibold tracking-[-0.01em]">{children}</h2>
      {count !== undefined && (
        <span
          className="rounded-[var(--radius-full)] px-2 py-0.5 text-[length:var(--text-xs)] font-semibold"
          style={{ background: "var(--accent-dim)", color: "var(--accent)" }}
        >
          {count}
        </span>
      )}
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[length:var(--text-sm)] text-[var(--text-muted)]">
      <span
        aria-hidden
        className="inline-block h-3 w-3 rounded-full border-2 border-[var(--border-strong)] border-t-[var(--accent)]"
        style={{ animation: "spin 700ms linear infinite" }}
      />
      {label}
    </span>
  );
}
