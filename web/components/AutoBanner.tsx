"use client";

/**
 * The red strip above every view while auto-approve is on. It is the owner's
 * constant reminder that risky actions are running without asking, so it
 * lives in the page chrome, not in a screen you have to open.
 *
 * Visibility follows the server's last answer. The local 1 s tick only
 * counts down between answers; when it reaches zero we ask the server
 * instead of trusting our own clock to have been right.
 */

import { useEffect, useState } from "react";

import { bannerVisible, formatClock, secondsRemaining } from "@/lib/policy";
import type { PolicyState } from "@/lib/usePolicy";
import { Button } from "./ui";

export function AutoBanner({ policy }: { policy: PolicyState }) {
  const { anchored, refresh, turnOff, busy, error } = policy;
  const [now, setNow] = useState(() => Date.now());
  const active = anchored?.policy.mode === "auto";

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  const left = secondsRemaining(anchored, now);
  const visible = bannerVisible(anchored, now);

  // Our countdown hit zero while the server last said "auto": ask it.
  useEffect(() => {
    if (active && left === 0) void refresh();
  }, [active, left, refresh]);

  if (!visible) return null;

  return (
    <div
      role="alert"
      className="flex shrink-0 flex-wrap items-center justify-center gap-x-4 gap-y-1 px-3 py-1.5 text-[length:var(--text-sm)] font-medium sm:px-4"
      style={{ background: "var(--critical)", color: "#fff" }}
    >
      <span>Auto-approve is ON — {formatClock(left)} left</span>
      <Button variant="ghost" disabled={busy} onClick={() => void turnOff()}>
        <span style={{ color: "#fff" }}>{busy ? "Turning off…" : "Turn off"}</span>
      </Button>
      {error && <span className="text-[length:var(--text-xs)]">Failed: {error}</span>}
    </div>
  );
}
