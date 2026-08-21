"use client";

/**
 * Shared primitives.
 *
 * Hand-written rather than pulled from a component kit: the shell needs a
 * panel, a stat, a badge and a button, none of which have accessibility
 * behaviour worth outsourcing. Radix/shadcn earns its place when the command
 * palette and dialogs land (focus traps, portals, roving tabindex) — see
 * PLAN.md Phase 3.
 */

import type { ReactNode } from "react";

import type { Danger } from "@/lib/protocol";
import { classNames } from "@/lib/format";

export function Panel({
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
        "rounded-lg border border-[var(--border)] bg-[var(--surface)]",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <h2 className="text-[var(--text-sm)] font-medium text-[var(--text-muted)]">
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({
  label,
  value,
  detail,
  percent,
  tone = "accent",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  percent?: number;
  tone?: "accent" | "safe" | "moderate" | "critical";
}) {
  const colour = `var(--${tone === "accent" ? "accent" : tone})`;
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
      <div className="text-[var(--text-xs)] uppercase tracking-wide text-[var(--text-faint)]">
        {label}
      </div>
      <div className="mt-1 text-[var(--text-xl)] font-semibold tabular-nums">{value}</div>
      {detail && (
        <div className="mt-0.5 text-[var(--text-xs)] text-[var(--text-muted)]">{detail}</div>
      )}
      {percent !== undefined && (
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--surface-sunken)]"
          role="progressbar"
          aria-valuenow={Math.round(percent)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
        >
          <div
            className="h-full rounded-full transition-[width]"
            style={{
              width: `${Math.min(100, Math.max(0, percent))}%`,
              background: colour,
              transitionDuration: "var(--motion-base)",
            }}
          />
        </div>
      )}
    </div>
  );
}

const DANGER_TONE: Record<Danger, { fg: string; bg: string }> = {
  SAFE: { fg: "var(--safe)", bg: "var(--safe-dim)" },
  MODERATE: { fg: "var(--moderate)", bg: "var(--moderate-dim)" },
  DESTRUCTIVE: { fg: "var(--destructive)", bg: "var(--destructive-dim)" },
  CRITICAL: { fg: "var(--critical)", bg: "var(--critical-dim)" },
};

export function DangerBadge({ danger }: { danger: Danger }) {
  const tone = DANGER_TONE[danger] ?? DANGER_TONE.SAFE;
  return (
    <span
      className="rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[var(--text-xs)] font-medium uppercase tracking-wide"
      style={{ color: tone.fg, background: tone.bg }}
    >
      {danger.toLowerCase()}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const styles: Record<string, string> = {
    default:
      "border-[var(--border-strong)] bg-[var(--surface-raised)] text-[var(--text)] hover:border-[var(--text-faint)]",
    primary:
      "border-transparent bg-[var(--accent)] text-[var(--accent-contrast)] hover:bg-[var(--accent-hover)]",
    danger:
      "border-[var(--critical)] bg-transparent text-[var(--critical)] hover:bg-[var(--critical-dim)]",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={classNames(
        "rounded-[var(--radius)] border px-3 py-1.5 text-[var(--text-sm)] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        styles[variant],
      )}
      style={{ transitionDuration: "var(--motion-fast)" }}
    >
      {children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="py-8 text-center text-[var(--text-sm)] text-[var(--text-faint)]">
      {children}
    </p>
  );
}
