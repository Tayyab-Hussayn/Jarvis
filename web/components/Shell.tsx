"use client";

/**
 * Chrome: the left icon rail, the floating dock, and the global confirmation
 * overlay.
 *
 * The overlay matters. A destructive action started from a panel blocks the
 * agent thread exactly like one the model asked for, but the user may have
 * navigated away from the screen that started it. Rendering panel
 * confirmations above everything means the block is always visible and always
 * answerable — otherwise the worker sits there until it times out and denies,
 * and the UI looks merely broken.
 */

import type { ReactNode } from "react";

import type { ConfirmView } from "@/lib/protocol";
import type { Connection } from "@/lib/useBrahma";
import { ConfirmCard } from "./ConfirmCard";
import { classNames } from "@/lib/format";

export const VIEWS = [
  { id: "console", label: "Console", glyph: "◈" },
  { id: "system", label: "System", glyph: "▤" },
  { id: "windows", label: "Windows", glyph: "▦" },
  { id: "files", label: "Files", glyph: "▢" },
  { id: "apps", label: "Apps", glyph: "◰" },
  { id: "control", label: "Control", glyph: "◑" },
  { id: "screen", label: "Screen", glyph: "▣" },
  { id: "activity", label: "Activity", glyph: "≡" },
  { id: "brain", label: "Brain", glyph: "◉" },
] as const;

export type ViewId = (typeof VIEWS)[number]["id"] | "settings";

const STATUS: Record<Connection, { label: string; colour: string }> = {
  connecting: { label: "Connecting", colour: "var(--moderate)" },
  open: { label: "Connected", colour: "var(--accent)" },
  reconnecting: { label: "Reconnecting", colour: "var(--moderate)" },
  offline: { label: "Server down", colour: "var(--critical)" },
  unauthorized: { label: "Not authorised", colour: "var(--critical)" },
};

function RailButton({
  label,
  glyph,
  active,
  onClick,
}: {
  label: string;
  glyph: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      className={classNames(
        "group relative flex h-11 w-11 items-center justify-center rounded-[var(--radius)] text-[length:var(--text-lg)]",
        active
          ? "text-[var(--accent-contrast)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
      style={{
        background: active ? "var(--accent)" : "transparent",
        transition: "all var(--motion-fast) var(--ease)",
      }}
    >
      <span aria-hidden>{glyph}</span>
      <span className="pointer-events-none absolute left-full z-20 ml-2 hidden whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-raised)] px-2 py-1 text-[length:var(--text-xs)] text-[var(--text)] group-hover:block">
        {label}
      </span>
    </button>
  );
}

export function Rail({
  view,
  onSelect,
  skills,
}: {
  view: ViewId;
  onSelect: (view: ViewId) => void;
  skills?: number;
}) {
  return (
    <nav
      className="flex shrink-0 flex-col items-center gap-1 border-r border-[var(--border)] bg-[var(--surface)] py-3"
      style={{ width: "var(--rail-width)" }}
      aria-label="Sections"
    >
      <div
        className="mb-3 flex h-9 w-9 items-center justify-center rounded-[var(--radius)] text-[length:var(--text-sm)] font-bold"
        style={{ background: "var(--accent)", color: "var(--accent-contrast)" }}
        title={skills ? `Brahma — ${skills} skills` : "Brahma"}
      >
        B
      </div>

      {VIEWS.map((entry) => (
        <RailButton
          key={entry.id}
          label={entry.label}
          glyph={entry.glyph}
          active={view === entry.id}
          onClick={() => onSelect(entry.id)}
        />
      ))}

      <div className="mt-auto">
        <RailButton
          label="Settings"
          glyph="⚙"
          active={view === "settings"}
          onClick={() => onSelect("settings")}
        />
      </div>
    </nav>
  );
}

export function Dock({
  connection,
  model,
  busy,
  onPalette,
}: {
  connection: Connection;
  model?: string;
  busy: boolean;
  onPalette: () => void;
}) {
  const status = STATUS[connection];
  return (
    <div
      className="pointer-events-auto flex items-center gap-3 rounded-[var(--radius-full)] border border-[var(--border)] bg-[var(--surface-raised)] px-4 py-2"
      style={{ boxShadow: "var(--shadow-dock)" }}
    >
      <span className="flex items-center gap-2 text-[length:var(--text-xs)]">
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
        <span style={{ color: status.colour }}>{status.label}</span>
      </span>

      {model && (
        <>
          <span aria-hidden className="text-[var(--border-strong)]">|</span>
          <span
            className="max-w-[16rem] truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]"
            title={model}
          >
            {model}
          </span>
        </>
      )}

      {busy && (
        <>
          <span aria-hidden className="text-[var(--border-strong)]">|</span>
          <span
            className="text-[length:var(--text-xs)]"
            style={{ color: "var(--moderate)" }}
          >
            working…
          </span>
        </>
      )}

      <span aria-hidden className="text-[var(--border-strong)]">|</span>
      <button
        type="button"
        onClick={onPalette}
        className="flex items-center gap-1.5 text-[length:var(--text-xs)] text-[var(--text-muted)] hover:text-[var(--accent)]"
        style={{ transition: "color var(--motion-fast) var(--ease)" }}
      >
        Run a skill
        <kbd className="rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
          ⌘K
        </kbd>
      </button>
    </div>
  );
}

/**
 * Confirmations raised by panel actions, floated above every screen.
 */
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
                className="absolute right-3 top-3 text-[length:var(--text-xs)] text-[var(--text-faint)] hover:text-[var(--text)]"
              >
                dismiss
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function Screen({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1
            className="font-semibold tracking-[-0.02em]"
            style={{ fontSize: "var(--text-2xl)", lineHeight: 1.1 }}
          >
            {title}
          </h1>
          {subtitle && (
            <p className="mt-1 text-[length:var(--text-sm)] text-[var(--text-muted)]">
              {subtitle}
            </p>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}
