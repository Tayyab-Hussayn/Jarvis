"use client";

/**
 * The inline confirmation for a destructive action.
 *
 * The agent thread is *blocked* on this card. Design rules that follow from
 * that, and that should survive any restyle:
 *
 * - The action and its exact target are always visible. Never summarise.
 * - CRITICAL requires a second, deliberate click. One misplaced Enter should
 *   not reboot the machine.
 * - The countdown is shown because the server denies on timeout. A card that
 *   silently stopped working would be worse than one that says so.
 * - Decline is the low-friction option; approve is the one you have to mean.
 * - "Always allow" only exists when the server offers it, and it names the
 *   exact signature it would store. It is never shown for CRITICAL, whatever
 *   a frame says: a rule must not be one click away from a reboot.
 */

import { useEffect, useState } from "react";

import { alwaysOffer, outcomeLabel } from "@/lib/confirm";
import type { ConfirmView, Decision } from "@/lib/protocol";
import { Button, DangerBadge, dangerColour } from "./ui";

function useCountdown(expiresAt: number, active: boolean): number {
  const [left, setLeft] = useState(() =>
    Math.max(0, Math.round((expiresAt - Date.now()) / 1000)),
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      setLeft(Math.max(0, Math.round((expiresAt - Date.now()) / 1000)));
    }, 1000);
    return () => clearInterval(timer);
  }, [expiresAt, active]);
  return left;
}

export function ConfirmCard({
  confirm,
  onRespond,
}: {
  confirm: ConfirmView;
  onRespond: (confirmId: string, decision: Decision) => void;
}) {
  const [armed, setArmed] = useState(false);
  const pending = confirm.status === "pending";
  const secondsLeft = useCountdown(confirm.expiresAt, pending);
  const needsDoubleClick = confirm.danger === "CRITICAL";
  const colour = dangerColour(confirm.danger);

  const approve = () => {
    if (needsDoubleClick && !armed) {
      setArmed(true);
      return;
    }
    onRespond(confirm.confirmId, "approve");
  };
  const offer = needsDoubleClick ? null : alwaysOffer(confirm.always);

  return (
    <div
      data-confirm-card={pending ? "" : undefined}
      className="glass glass-strong rounded-[var(--radius-lg)] border-l-[3px] p-4"
      style={
        {
          borderLeftColor: colour,
          boxShadow: pending
            ? `var(--shadow-panel), 0 0 24px color-mix(in srgb, ${colour} 22%, transparent)`
            : undefined,
          "--pulse-tint": `color-mix(in srgb, ${colour} 22%, transparent)`,
          animation: pending ? "await-answer 2.2s ease-in-out infinite" : undefined,
        } as React.CSSProperties
      }
      role={pending ? "alertdialog" : undefined}
      aria-label={pending ? `Confirm: ${confirm.action}` : undefined}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DangerBadge danger={confirm.danger} />
            <span className="text-[length:var(--text-base)] font-semibold tracking-[-0.01em]">
              {confirm.action}
            </span>
          </div>
          {confirm.detail && (
            <p className="mt-2 break-all rounded-[var(--radius-sm)] bg-[var(--surface-sunken)] px-2.5 py-1.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
              {confirm.detail}
            </p>
          )}
        </div>
        {pending && (
          <span
            className="shrink-0 tabular-nums text-[length:var(--text-xs)] text-[var(--text-faint)]"
            title="Denied automatically when this runs out"
          >
            {secondsLeft}s
          </span>
        )}
      </div>

      {pending ? (
        // Stacked on a phone so each target is full width; a row from `sm` up.
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center max-sm:[&>button]:w-full">
          {/* Decline is focused first: Enter or Space on a card nobody has read
              must land on the safe answer. Approve is the primary style, so
              it is the obvious thing to reach for deliberately. */}
          <Button autoFocus onClick={() => onRespond(confirm.confirmId, "deny")}>
            Decline
          </Button>
          <Button variant={armed ? "danger" : "primary"} onClick={approve}>
            {needsDoubleClick && !armed ? "Approve…" : "Approve"}
          </Button>
          {offer && (
            <Button
              title={`Approve now and stop asking for: ${offer.signature}`}
              onClick={() => onRespond(confirm.confirmId, "always")}
            >
              Always allow:{" "}
              <code className="break-all font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
                {offer.signature}
              </code>
            </Button>
          )}
          <span
            className="text-[length:var(--text-xs)]"
            style={{ color: armed ? "var(--critical)" : "var(--text-faint)" }}
          >
            {needsDoubleClick && armed
              ? "Click again — this is a critical action."
              : "Kravein is waiting."}
          </span>
        </div>
      ) : (
        <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-muted)]">
          {outcomeLabel(confirm)}
        </p>
      )}
    </div>
  );
}
