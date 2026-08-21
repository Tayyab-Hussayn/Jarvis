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
import { Button, DangerBadge } from "./ui";

const BORDER = {
  SAFE: "var(--safe)",
  MODERATE: "var(--moderate)",
  DESTRUCTIVE: "var(--destructive)",
  CRITICAL: "var(--critical)",
} as const;

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

  const approve = () => {
    if (needsDoubleClick && !armed) {
      setArmed(true);
      return;
    }
    onRespond(confirm.confirmId, true);
  };

  return (
    <div
      className="rounded-lg border-l-2 border border-[var(--border)] bg-[var(--surface-raised)] p-3"
      style={{ borderLeftColor: BORDER[confirm.danger] }}
      role={pending ? "alertdialog" : undefined}
      aria-label={pending ? `Confirm: ${confirm.action}` : undefined}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <DangerBadge danger={confirm.danger} />
            <span className="text-[var(--text-sm)] font-medium">{confirm.action}</span>
          </div>
          {confirm.detail && (
            <p className="mt-1.5 break-all font-[family-name:var(--font-mono)] text-[var(--text-xs)] text-[var(--text-muted)]">
              {confirm.detail}
            </p>
          )}
        </div>
        {pending && (
          <span
            className="shrink-0 tabular-nums text-[var(--text-xs)] text-[var(--text-faint)]"
            title="Denied automatically when this runs out"
          >
            {secondsLeft}s
          </span>
        )}
      </div>

      {pending ? (
        <div className="mt-3 flex items-center gap-2">
          <Button onClick={() => onRespond(confirm.confirmId, false)}>Deny</Button>
          <Button variant={armed ? "danger" : "default"} onClick={approve}>
            {needsDoubleClick && !armed ? "Approve…" : "Approve"}
          </Button>
          {needsDoubleClick && armed && (
            <span className="text-[var(--text-xs)] text-[var(--critical)]">
              Click again to confirm — this is a critical action.
            </span>
          )}
          {!needsDoubleClick && (
            <span className="text-[var(--text-xs)] text-[var(--text-faint)]">
              Brahma is waiting.
            </span>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[var(--text-xs)] text-[var(--text-muted)]">
          {confirm.status === "approved" && "Approved."}
          {confirm.status === "denied" && "Denied."}
          {confirm.status === "expired" &&
            "Expired without an answer — treated as denied."}
        </p>
      )}
    </div>
  );
}
