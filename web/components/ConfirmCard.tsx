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
 * - Deny is the low-friction option; approve is the one you have to mean.
 */

import { useEffect, useState } from "react";

import type { ConfirmView } from "@/lib/protocol";
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
  onRespond: (confirmId: string, approve: boolean) => void;
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
    onRespond(confirm.confirmId, true);
  };

  return (
    <div
      className="rounded-[var(--radius-lg)] border border-l-2 border-[var(--border)] bg-[var(--surface-raised)] p-3.5"
      style={
        {
          borderLeftColor: colour,
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
            <span className="text-[length:var(--text-sm)] font-medium">
              {confirm.action}
            </span>
          </div>
          {confirm.detail && (
            <p className="mt-1.5 break-all font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
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
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button onClick={() => onRespond(confirm.confirmId, false)}>Deny</Button>
          <Button variant={armed ? "danger" : "default"} onClick={approve}>
            {needsDoubleClick && !armed ? "Approve…" : "Approve"}
          </Button>
          <span
            className="text-[length:var(--text-xs)]"
            style={{ color: armed ? "var(--critical)" : "var(--text-faint)" }}
          >
            {needsDoubleClick && armed
              ? "Click again — this is a critical action."
              : "Brahma is waiting."}
          </span>
        </div>
      ) : (
        <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-muted)]">
          {confirm.status === "approved" && "Approved."}
          {confirm.status === "denied" && "Denied."}
          {confirm.status === "expired" &&
            "Expired without an answer — treated as denied."}
        </p>
      )}
    </div>
  );
}
