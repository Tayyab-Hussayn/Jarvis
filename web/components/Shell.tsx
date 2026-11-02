"use client";

/**
 * Chrome for the Command Constellation layout: the HUD bar, the floating dock,
 * the pane sheet, the always-on-top confirmation overlay and `Screen`.
 *
 * There is no sidebar. Modules live in the dock and open as panes over the
 * canvas; the conversation and the agents stay underneath.
 *
 * Rules this file exists to hold:
 *
 * 1. The confirmation overlay floats above EVERYTHING (z-confirm > palette >
 *    pane > dock > HUD). A destructive action blocks the agent thread, and
 *    the user may be looking at any pane. A hidden card parks the worker until
 *    the 120 s timeout denies, and the UI would look merely broken.
 * 2. A pane is closable from the keyboard (Esc), returns focus to what opened
 *    it, and never traps the user: the dock and HUD stay reachable.
 * 3. The scroll container is `Screen`'s content wrapper. `overflow-y-auto`
 *    needs `min-h-0` on a flex child or the content is simply unreachable.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import type { ConfirmView, SystemSnapshot } from "@/lib/protocol";
import type { Connection } from "@/lib/useKravein";
import {
  MODULES,
  escapeClosesPane,
  focusReturnTarget,
  roveIndex,
  type ModuleId,
  type Route,
} from "@/lib/panes";
import type { PolicyMode } from "@/lib/policy";
import { classNames } from "@/lib/format";
import { ConfirmCard } from "./ConfirmCard";
import { Icon, type IconName } from "./icons";

const STATUS: Record<Connection, { label: string; colour: string }> = {
  connecting: { label: "Connecting", colour: "var(--moderate)" },
  open: { label: "Connected", colour: "var(--accent)" },
  reconnecting: { label: "Reconnecting", colour: "var(--moderate)" },
  offline: { label: "Server down", colour: "var(--critical)" },
  unauthorized: { label: "Not authorised", colour: "var(--critical)" },
  replaced: { label: "Open in another window", colour: "var(--moderate)" },
};

/* -- HUD -------------------------------------------------------------- */

function Vital({ label, percent }: { label: string; percent: number }) {
  const hot = percent >= 90;
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="eyebrow">{label}</span>
      <span
        className="tabular-nums text-[length:var(--text-sm)] font-semibold"
        style={{ color: hot ? "var(--critical)" : "var(--text)" }}
      >
        {Math.round(percent)}%
      </span>
    </span>
  );
}

const MODE_CHIP: Record<PolicyMode, { label: string; hint: string }> = {
  ask: { label: "Ask", hint: "Risky actions ask you first" },
  decline: { label: "Decline", hint: "Risky actions are declined without asking" },
  auto: { label: "Auto", hint: "Risky actions run without asking" },
};

function Clock() {
  const [now, setNow] = useState<Date | null>(null);
  // Set after mount: a server-rendered clock would mismatch on hydration.
  useEffect(() => {
    const tick = () => setNow(new Date());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, 15_000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, []);
  if (!now) return null;
  return (
    <time
      dateTime={now.toISOString()}
      className="tabular-nums text-[length:var(--text-sm)] text-[var(--text-muted)]"
    >
      {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </time>
  );
}

export function Hud({
  connection,
  assistant,
  model,
  hostname,
  system,
  policyMode,
  onHome,
  onPalette,
  onOpenSettings,
  voiceActive,
  onToggleVoice,
}: {
  connection: Connection;
  assistant?: string;
  model?: string;
  hostname?: string;
  system: SystemSnapshot | null;
  policyMode?: PolicyMode;
  onHome: () => void;
  onPalette: () => void;
  onOpenSettings: () => void;
  voiceActive?: boolean;
  onToggleVoice?: () => void;
}) {
  const status = STATUS[connection];
  const mode = policyMode ? MODE_CHIP[policyMode] : null;
  return (
    <header
      className="glass relative flex shrink-0 items-center gap-2 rounded-none border-x-0 border-t-0 px-3 sm:gap-4 sm:px-5"
      style={{ height: "var(--hud-height)", zIndex: "var(--z-hud)" as unknown as number }}
    >
      <button
        type="button"
        onClick={onHome}
        aria-label="Kravein, back to the conversation"
        className="wordmark -mx-1 shrink-0 rounded-[var(--radius-sm)] px-1 text-[length:var(--text-base)] font-semibold"
        style={{ minWidth: 0 }}
      >
        KRAVEIN
      </button>

      <span
        className="flex min-w-0 shrink-0 items-center gap-2 text-[length:var(--text-xs)]"
        title={status.label}
        role="status"
      >
        <span
          aria-hidden
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{
            background: status.colour,
            boxShadow: `0 0 8px ${status.colour}`,
            animation:
              connection === "connecting" || connection === "reconnecting"
                ? "pulse 1.2s ease-in-out infinite"
                : undefined,
          }}
        />
        {/* The word is always there for anything but a healthy link; colour is
            never the only signal. */}
        <span
          className={connection === "open" ? "hidden lg:inline" : ""}
          style={{ color: status.colour }}
        >
          {status.label}
        </span>
      </span>

      {model && (
        <span
          className="hidden min-w-0 max-w-[16rem] truncate rounded-[var(--radius-full)] border border-[var(--border-strong)] px-2.5 py-0.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)] md:inline"
          title={`${assistant ?? "Kravein"} on ${hostname ?? "this machine"}: ${model}`}
        >
          {model}
        </span>
      )}

      <div className="ml-auto flex items-center gap-3 sm:gap-4">
        {system?.available && (
          <div className="hidden items-center gap-4 lg:flex">
            <Vital label="CPU" percent={system.cpu_percent ?? 0} />
            <Vital label="MEM" percent={system.memory?.percent ?? 0} />
          </div>
        )}

        {mode && (
          <button
            type="button"
            onClick={onOpenSettings}
            title={`${mode.hint}. Open settings.`}
            aria-label={`Confirmation mode: ${mode.label}. ${mode.hint}. Open settings`}
            className="flex shrink-0 items-center gap-1.5 rounded-[var(--radius-full)] border px-2.5 py-1 text-[length:var(--text-xs)] font-medium"
            style={{
              borderColor: policyMode === "auto" ? "var(--critical)" : "var(--border-strong)",
              color: policyMode === "auto" ? "var(--critical)" : "var(--text-muted)",
              background: policyMode === "auto" ? "var(--critical-dim)" : "transparent",
            }}
          >
            <Icon name="settings" size={12} />
            <span className="hidden sm:inline">Confirm:</span>
            {mode.label}
          </button>
        )}

        {onToggleVoice && (
          <button
            type="button"
            onClick={onToggleVoice}
            aria-pressed={Boolean(voiceActive)}
            title={voiceActive ? "Stop live voice" : "Start live voice"}
            className="flex shrink-0 items-center gap-2 rounded-[var(--radius-full)] border px-3 py-1 text-[length:var(--text-xs)]"
            style={{
              transition: "all var(--motion-fast) var(--ease)",
              borderColor: voiceActive ? "var(--accent)" : "var(--border-strong)",
              background: voiceActive ? "var(--accent-dim)" : "transparent",
              boxShadow: voiceActive ? "var(--glow-soft)" : undefined,
              color: voiceActive ? "var(--accent)" : "var(--text-muted)",
            }}
          >
            <Icon name="mic" size={14} />
            <span className="hidden sm:inline">{voiceActive ? "Live" : "Voice"}</span>
            {voiceActive && (
              <span
                aria-hidden
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--accent)", animation: "pulse 1.1s ease-in-out infinite" }}
              />
            )}
          </button>
        )}

        <button
          type="button"
          onClick={onPalette}
          aria-label="Open the command palette"
          className="flex shrink-0 items-center gap-2 rounded-[var(--radius-full)] border border-[var(--border-strong)] px-3 py-1 text-[length:var(--text-xs)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)]"
          style={{ transition: "all var(--motion-fast) var(--ease)" }}
        >
          <Icon name="search" size={14} />
          <span className="hidden sm:inline">Search</span>
          <kbd className="hidden rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-1 font-[family-name:var(--font-mono)] sm:inline">
            ⌘K
          </kbd>
        </button>

        <span className="hidden sm:inline">
          <Clock />
        </span>
      </div>
    </header>
  );
}

/* -- dock ------------------------------------------------------------- */

export function Dock({
  route,
  onOpen,
  badges,
}: {
  route: Route;
  onOpen: (id: ModuleId) => void;
  badges?: Partial<Record<ModuleId, number>>;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const items = Array.from(
      listRef.current?.querySelectorAll<HTMLElement>("button[data-dock]:not([aria-disabled=true])") ?? [],
    );
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    const next = roveIndex(at, event.key, items.length);
    if (next === null) return;
    event.preventDefault();
    items[next].focus();
  };

  return (
    <nav
      aria-label="Modules"
      className="fixed bottom-0 left-0 right-0 sm:bottom-4 sm:left-1/2 sm:right-auto sm:-translate-x-1/2"
      style={{ zIndex: "var(--z-dock)" as unknown as number }}
    >
      <div
        ref={listRef}
        onKeyDown={onKeyDown}
        className={classNames(
          "glass glass-strong no-scrollbar flex items-center gap-0.5 overflow-x-auto px-2",
          // Phone: a full-width bottom bar. Desktop: a floating chrome capsule
          // at the bottom centre, directly under the composer (one deck).
          "max-sm:rounded-none max-sm:border-x-0 max-sm:border-b-0",
          "sm:overflow-visible sm:rounded-[var(--radius-full)] sm:px-3 sm:py-1.5",
        )}
        style={{ boxShadow: "var(--shadow-dock)", minHeight: "var(--dock-height)" }}
      >
        {MODULES.map((mod) => {
          const active = route.kind === "module" && route.id === mod.id;
          const badge = badges?.[mod.id];
          return (
            <button
              key={mod.id}
              type="button"
              data-dock={mod.id}
              aria-label={
                mod.soon
                  ? `${mod.label} (coming soon)`
                  : badge
                    ? `${mod.label}, ${badge} waiting for you`
                    : mod.label
              }
              aria-current={active ? "page" : undefined}
              aria-disabled={mod.soon ? true : undefined}
              onClick={() => !mod.soon && onOpen(mod.id)}
              className={classNames(
                "group relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--radius-lg)]",
                mod.soon && "cursor-default opacity-45",
                active
                  ? "text-[var(--gradient-accent-contrast)]"
                  : "text-[var(--text-muted)] hover:bg-[var(--glass-highlight)] hover:text-[var(--text-strong)]",
              )}
              style={{
                transition: "background var(--motion-fast) var(--ease), color var(--motion-fast) var(--ease)",
                // The open module is lit in laser.
                background: active ? "var(--gradient-accent)" : undefined,
                boxShadow: active ? "var(--glow)" : undefined,
              }}
            >
              <Icon name={mod.icon as IconName} size={19} />
              {badge !== undefined && badge > 0 && (
                <span
                  className="absolute right-0.5 top-0.5 min-w-4 rounded-[var(--radius-full)] px-1 text-center text-[length:var(--text-xs)] font-semibold leading-4"
                  style={{ background: "var(--laser)", color: "var(--gradient-accent-contrast)" }}
                >
                  {badge}
                </span>
              )}
              <span
                role="presentation"
                className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-3 hidden -translate-x-1/2 whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-raised)] px-2 py-1 text-[length:var(--text-xs)] text-[var(--text)] shadow-lg group-hover:sm:block group-focus-visible:sm:block"
              >
                {mod.label}
                {mod.soon ? " · soon" : ""}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

/* -- pane ------------------------------------------------------------- */

/**
 * A glass sheet over the canvas. Mounted once while any module or agent is
 * open (not keyed by route), so the opener it returns focus to is the control
 * the user first used, not whichever dock button they pressed last.
 */
export function Pane({
  title,
  eyebrow,
  onClose,
  children,
}: {
  title: string;
  eyebrow?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const sheetRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  // Kept current after each render, so the Esc listener (installed once) never
  // calls a stale close.
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    sheetRef.current?.focus({ preventScroll: true });

    const onKey = (event: KeyboardEvent) => {
      if (!escapeClosesPane(event)) return;
      event.preventDefault();
      closeRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      // Back to what opened the pane; if that is gone (a node that re-rendered),
      // to the composer so the keyboard is never stranded on <body>.
      const fallback = document.querySelector<HTMLElement>("[data-composer]");
      focusReturnTarget(opener, fallback)?.focus({ preventScroll: true });
    };
  }, []);

  return (
    <>
      {/* Click-away scrim, below the dock so modules stay switchable. */}
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close pane"
        onClick={onClose}
        className="fixed inset-0 cursor-default"
        style={{ zIndex: "calc(var(--z-dock) - 1)" as unknown as number, background: "var(--scrim)", minHeight: 0 }}
      />
      <section
        ref={sheetRef}
        role="dialog"
        aria-label={title}
        tabIndex={-1}
        className="glass-pane pane-sheet fixed flex flex-col overflow-hidden outline-none"
        style={{ zIndex: "var(--z-pane)" as unknown as number }}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-[var(--border)] px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            <h2 className="truncate text-[length:var(--text-lg)] font-semibold text-[var(--text-strong)]">
              {title}
            </h2>
          </div>
          <kbd className="hidden rounded-[var(--radius-sm)] border border-[var(--border-strong)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)] sm:inline">
            Esc
          </kbd>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            className="flex h-9 w-9 items-center justify-center rounded-[var(--radius)] text-[var(--text-muted)] hover:bg-[var(--glass-highlight)] hover:text-[var(--text-strong)]"
          >
            <Icon name="close" size={18} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-hidden px-3 py-3 sm:px-6 sm:py-4">{children}</div>
      </section>
    </>
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
    <div
      data-confirm-overlay
      className="pointer-events-none fixed inset-x-0 top-2 flex justify-center px-2 sm:top-4 sm:px-4"
      style={{ zIndex: "var(--z-confirm)" as unknown as number }}
    >
      <div className="pointer-events-auto max-h-[calc(100dvh-1rem)] w-full max-w-lg space-y-2 overflow-y-auto">
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

/** Move keyboard focus to the first open confirmation, wherever it lives. */
export function focusConfirmation(): void {
  const target =
    document.querySelector<HTMLElement>("[data-confirm-overlay] button") ??
    document.querySelector<HTMLElement>("[data-confirm-card] button");
  target?.focus();
}

/** Per-pane wrapper: layout only, the title lives in the pane header. */
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
      {/* The one scroll container every panel inherits.

          `flex-1` plus `min-h-0` is what makes `overflow-y-auto` actually
          engage: without `min-h-0` a flex child refuses to shrink below its
          content, so the element grows instead of scrolling and the overflow
          escapes into an `overflow-hidden` ancestor, where it is simply
          unreachable. Panels used to each add their own `overflow-y-auto` on a
          div with no height constraint, which is why five screens clipped.
          One scroller here and every current and future panel gets it. */}
      <div className="min-h-0 flex-1 overflow-y-auto pb-6">{children}</div>
    </div>
  );
}
