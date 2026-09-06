"use client";

/**
 * Chrome: the left rail, the top bar with live vitals, the dock, and the
 * global confirmation overlay.
 *
 * Two rules this file exists to hold:
 *
 * 1. The confirmation overlay floats above everything. A destructive action
 *    started from a panel blocks the agent thread exactly like one the model
 *    asked for, but the user may have navigated away from the screen that
 *    started it. If the block were not visible everywhere, the worker would
 *    sit there until it timed out and denied, and the UI would look merely
 *    broken.
 * 2. Vitals live in the top bar, not on a screen you have to navigate to. For
 *    an assistant whose whole promise is control over the machine, the
 *    machine's state should never be more than a glance away.
 */

import type { ReactNode } from "react";

import type { ConfirmView, SystemSnapshot } from "@/lib/protocol";
import type { Connection } from "@/lib/useKravein";
import { ConfirmCard } from "./ConfirmCard";
import { Icon, type IconName } from "./icons";
import { classNames } from "@/lib/format";

/**
 * Grouped, because these are three different kinds of thing: the agent, the
 * machine's surfaces, and introspection. A flat run of nine made them all look
 * equivalent.
 */
export type ViewId =
  | "console"
  | "schedule"
  | "system"
  | "windows"
  | "files"
  | "apps"
  | "control"
  | "screen"
  | "activity"
  | "brain"
  | "settings";

export interface NavItem {
  id: ViewId;
  label: string;
  icon: IconName;
}

export const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Agent",
    items: [{ id: "console", label: "Console", icon: "console" }],
  },
  {
    label: "Machine",
    items: [
      { id: "system", label: "System", icon: "system" },
      { id: "schedule", label: "Schedule", icon: "schedule" },
      { id: "windows", label: "Windows", icon: "windows" },
      { id: "files", label: "Files", icon: "files" },
      { id: "apps", label: "Apps", icon: "apps" },
      { id: "control", label: "Control", icon: "control" },
      { id: "screen", label: "Screen", icon: "screen" },
    ],
  },
  {
    label: "Inspect",
    items: [
      { id: "activity", label: "Activity", icon: "activity" },
      { id: "brain", label: "Brain", icon: "brain" },
      { id: "settings", label: "Settings", icon: "settings" },
    ],
  },
];

export const VIEWS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

const STATUS: Record<Connection, { label: string; colour: string }> = {
  connecting: { label: "Connecting", colour: "var(--moderate)" },
  open: { label: "Connected", colour: "var(--accent)" },
  reconnecting: { label: "Reconnecting", colour: "var(--moderate)" },
  offline: { label: "Server down", colour: "var(--critical)" },
  unauthorized: { label: "Not authorised", colour: "var(--critical)" },
};

/* -- rail ------------------------------------------------------------- */

function RailItem({
  label,
  icon,
  active,
  badge,
  expanded,
  onClick,
}: {
  label: string;
  icon: IconName;
  active: boolean;
  badge?: number;
  expanded: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={expanded ? undefined : label}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      className={classNames(
        "group relative flex h-10 w-full items-center gap-3 rounded-[var(--radius)] px-3",
        // A subtle tint plus an indicator bar, not a solid fill. Solid accent
        // is reserved for primary actions; spending it on navigation left
        // nothing louder for the things that actually matter.
        active
          ? "bg-[var(--accent-dim)] text-[var(--accent)]"
          : "text-[var(--text-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--text)]",
      )}
      style={{ transition: "background var(--motion-fast) var(--ease), color var(--motion-fast) var(--ease)" }}
    >
      <span
        aria-hidden
        className="absolute left-0 top-1/2 w-[3px] -translate-y-1/2 rounded-r-[var(--radius-full)]"
        style={{
          height: active ? "18px" : "0px",
          background: "var(--accent)",
          transition: "height var(--motion-base) var(--ease)",
        }}
      />
      <Icon name={icon} size={19} className="shrink-0" />
      {expanded && (
        <span className="min-w-0 flex-1 truncate text-left text-[length:var(--text-sm)]">
          {label}
        </span>
      )}
      {expanded && badge !== undefined && badge > 0 && (
        <span
          className="shrink-0 rounded-[var(--radius-full)] px-1.5 text-[length:var(--text-xs)] font-semibold tabular-nums"
          style={{ background: "var(--critical-dim)", color: "var(--critical)" }}
        >
          {badge}
        </span>
      )}
      {!expanded && (
        <span className="pointer-events-none absolute left-full z-30 ml-2 hidden whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-raised)] px-2 py-1 text-[length:var(--text-xs)] text-[var(--text)] shadow-lg group-hover:block">
          {label}
        </span>
      )}
    </button>
  );
}

export function Rail({
  view,
  onSelect,
  expanded,
  onToggle,
  badges,
}: {
  view: ViewId;
  onSelect: (view: ViewId) => void;
  expanded: boolean;
  onToggle: () => void;
  badges?: Partial<Record<ViewId, number>>;
}) {
  return (
    <nav
      className="flex shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] px-2.5 py-3"
      style={{
        width: expanded ? "var(--rail-width-open)" : "var(--rail-width)",
        transition: "width var(--motion-base) var(--ease)",
      }}
      aria-label="Sections"
    >
      {/* Wordmark, not a lime tile — the logo previously matched the active
          nav item so closely that it read as a tenth destination. */}
      <div className="mb-4 flex h-10 items-center gap-2.5 px-1">
        <span
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] text-[length:var(--text-xs)] font-bold"
          style={{
            background: "transparent",
            color: "var(--accent)",
            border: "1.5px solid var(--accent)",
          }}
        >
          B
        </span>
        {expanded && (
          <span className="truncate text-[length:var(--text-sm)] font-semibold tracking-[-0.01em]">
            Kravein
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            {expanded ? (
              <div className="mb-1 px-3 text-[length:var(--text-xs)] uppercase tracking-[0.1em] text-[var(--text-faint)]">
                {group.label}
              </div>
            ) : (
              <div
                aria-hidden
                className="mx-3 mb-2 border-t border-[var(--border)] first:border-0"
              />
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <RailItem
                  key={item.id}
                  label={item.label}
                  icon={item.icon}
                  active={view === item.id}
                  badge={badges?.[item.id]}
                  expanded={expanded}
                  onClick={() => onSelect(item.id)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={onToggle}
        aria-label={expanded ? "Collapse sidebar" : "Expand sidebar"}
        title={expanded ? "Collapse sidebar" : "Expand sidebar"}
        className="mt-2 flex h-8 items-center justify-center rounded-[var(--radius)] text-[var(--text-faint)] hover:bg-[var(--surface-raised)] hover:text-[var(--text)]"
        style={{ transition: "all var(--motion-fast) var(--ease)" }}
      >
        <Icon
          name="chevron"
          size={16}
          className={expanded ? "rotate-180" : undefined}
        />
      </button>
    </nav>
  );
}

/* -- top bar ---------------------------------------------------------- */

function Vital({
  label,
  value,
  percent,
  tone,
}: {
  label: string;
  value: string;
  percent?: number;
  tone?: string;
}) {
  const colour =
    tone ??
    (percent === undefined
      ? "var(--accent)"
      : percent >= 90
        ? "var(--critical)"
        : percent >= 75
          ? "var(--moderate)"
          : "var(--accent)");
  return (
    <div className="flex items-center gap-2">
      <span className="text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
        {label}
      </span>
      <span
        className="tabular-nums text-[length:var(--text-sm)] font-semibold"
        style={{ color: colour }}
      >
        {value}
      </span>
      {percent !== undefined && (
        <span
          aria-hidden
          className="hidden h-1 w-10 overflow-hidden rounded-[var(--radius-full)] bg-[var(--surface-sunken)] lg:block"
        >
          <span
            className="block h-full rounded-[var(--radius-full)]"
            style={{
              width: `${Math.min(100, Math.max(0, percent))}%`,
              background: colour,
              transition: "width var(--motion-base) var(--ease)",
            }}
          />
        </span>
      )}
    </div>
  );
}

/**
 * Persistent header: where you are, what machine this is, and how it is doing.
 * After the Cardiology reference's inline vitals row.
 */
export function TopBar({
  title,
  hostname,
  environment,
  system,
  connection,
  onPalette,
  voiceActive,
  voiceOpen,
  onToggleVoice,
}: {
  title: string;
  hostname?: string;
  environment?: string;
  system: SystemSnapshot | null;
  connection: Connection;
  onPalette: () => void;
  voiceActive?: boolean;
  voiceOpen?: boolean;
  onToggleVoice?: () => void;
}) {
  const status = STATUS[connection];
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-[var(--border)] bg-[var(--surface)] px-5">
      <div className="min-w-0">
        <h1 className="truncate text-[length:var(--text-lg)] font-semibold tracking-[-0.01em]">
          {title}
        </h1>
        {environment && (
          <p className="truncate text-[length:var(--text-xs)] text-[var(--text-faint)]">
            {hostname ? `${hostname} · ` : ""}
            {environment}
          </p>
        )}
      </div>

      <div className="ml-auto hidden items-center gap-5 md:flex">
        {system?.available && (
          <>
            <Vital
              label="CPU"
              value={`${Math.round(system.cpu_percent ?? 0)}%`}
              percent={system.cpu_percent}
            />
            <Vital
              label="MEM"
              value={`${Math.round(system.memory?.percent ?? 0)}%`}
              percent={system.memory?.percent}
            />
            <Vital
              label="DISK"
              value={`${Math.round(system.disk?.percent ?? 0)}%`}
              percent={system.disk?.percent}
            />
            {system.battery && (
              <Vital
                label="BAT"
                value={`${Math.round(system.battery.percent)}%`}
                percent={system.battery.percent}
                tone={
                  system.battery.percent < 20 && !system.battery.plugged
                    ? "var(--critical)"
                    : undefined
                }
              />
            )}
          </>
        )}
      </div>

      {onToggleVoice && (
        <button
          type="button"
          onClick={onToggleVoice}
          aria-pressed={Boolean(voiceOpen)}
          title={voiceOpen ? "Hide live voice" : "Live voice"}
          className="flex shrink-0 items-center gap-2 rounded-[var(--radius-full)] border px-3 py-1.5 text-[length:var(--text-xs)]"
          style={{
            transition: "all var(--motion-fast) var(--ease)",
            borderColor: voiceActive
              ? "var(--accent)"
              : voiceOpen
                ? "var(--border-strong)"
                : "var(--border-strong)",
            background: voiceActive ? "var(--accent-dim)" : "transparent",
            color: voiceActive ? "var(--accent)" : "var(--text-muted)",
          }}
        >
          <Icon name="mic" size={14} />
          <span className="hidden sm:inline">Voice</span>
          {voiceActive && (
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{
                background: "var(--accent)",
                animation: "pulse 1.1s ease-in-out infinite",
              }}
            />
          )}
        </button>
      )}

      <button
        type="button"
        onClick={onPalette}
        className="flex shrink-0 items-center gap-2 rounded-[var(--radius-full)] border border-[var(--border-strong)] px-3 py-1.5 text-[length:var(--text-xs)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
        style={{ transition: "all var(--motion-fast) var(--ease)" }}
      >
        <Icon name="search" size={14} />
        <span className="hidden sm:inline">Run a skill</span>
        <kbd className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-1 font-[family-name:var(--font-mono)]">
          ⌘K
        </kbd>
      </button>

      <span
        className="flex shrink-0 items-center gap-2 text-[length:var(--text-xs)]"
        title={status.label}
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{
            background: status.colour,
            animation:
              connection === "connecting" || connection === "reconnecting"
                ? "pulse 1.2s ease-in-out infinite"
                : undefined,
          }}
        />
        <span className="hidden lg:inline" style={{ color: status.colour }}>
          {status.label}
        </span>
      </span>
    </header>
  );
}

/* -- overlay ---------------------------------------------------------- */

export function ConfirmOverlay({
  confirms,
  onRespond,
  onDismiss,
}: {
  confirms: ConfirmView[];
  onRespond: (confirmId: string, approve: boolean) => void;
  onDismiss: (confirmId: string) => void;
}) {
  if (confirms.length === 0) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-50 flex justify-center px-4">
      <div className="pointer-events-auto w-full max-w-lg space-y-2">
        {confirms.map((confirm) => (
          <div key={confirm.confirmId} className="relative">
            <ConfirmCard confirm={confirm} onRespond={onRespond} />
            {confirm.status !== "pending" && (
              <button
                type="button"
                onClick={() => onDismiss(confirm.confirmId)}
                aria-label="Dismiss"
                className="absolute right-3 top-3 text-[var(--text-faint)] hover:text-[var(--text)]"
              >
                <Icon name="close" size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Per-screen wrapper. The title now lives in the top bar, so this is layout only. */
export function Screen({
  actions,
  children,
  subtitle,
}: {
  actions?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {(actions || subtitle) && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          {subtitle ? (
            <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">{subtitle}</p>
          ) : (
            <span />
          )}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
