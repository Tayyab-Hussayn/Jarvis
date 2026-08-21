"use client";

import { useEffect, useState } from "react";

import type { StateSnapshot, SystemSnapshot } from "@/lib/protocol";
import { api } from "@/lib/session";
import { bytes, duration, percent } from "@/lib/format";
import { Empty, Panel, Stat } from "./ui";

const POLL_MS = 3000;

function tone(value: number | undefined): "accent" | "moderate" | "critical" {
  if (value === undefined) return "accent";
  if (value >= 90) return "critical";
  if (value >= 75) return "moderate";
  return "accent";
}

export function SystemView() {
  const [system, setSystem] = useState<SystemSnapshot | null>(null);
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;

    const poll = async () => {
      try {
        const snapshot = await api<SystemSnapshot>("/api/system");
        if (alive) {
          setSystem(snapshot);
          setError("");
        }
      } catch (exc) {
        if (alive) setError(String(exc));
      }
    };

    void poll();
    void api<StateSnapshot>("/api/state")
      .then((value) => alive && setState(value))
      .catch(() => undefined);

    const timer = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (error) return <Empty>Could not read system state: {error}</Empty>;
  if (!system) return <Empty>Reading system state…</Empty>;
  if (!system.available) return <Empty>psutil is not installed, so there is nothing to show.</Empty>;

  const env = state?.environment;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="CPU"
          value={percent(system.cpu_percent)}
          detail={
            system.load
              ? `load ${system.load.map((v) => v.toFixed(2)).join("  ")}`
              : `${system.cpu_count ?? "?"} cores`
          }
          percent={system.cpu_percent}
          tone={tone(system.cpu_percent)}
        />
        <Stat
          label="Memory"
          value={percent(system.memory?.percent)}
          detail={`${bytes(system.memory?.used)} of ${bytes(system.memory?.total)}`}
          percent={system.memory?.percent}
          tone={tone(system.memory?.percent)}
        />
        <Stat
          label="Disk /"
          value={percent(system.disk?.percent)}
          detail={`${bytes(system.disk?.used)} of ${bytes(system.disk?.total)}`}
          percent={system.disk?.percent}
          tone={tone(system.disk?.percent)}
        />
        <Stat
          label="Battery"
          value={system.battery ? `${system.battery.percent}%` : "—"}
          detail={
            system.battery
              ? system.battery.plugged
                ? "charging"
                : "on battery"
              : "no battery"
          }
          percent={system.battery?.percent}
          tone={
            system.battery && system.battery.percent < 20 && !system.battery.plugged
              ? "critical"
              : "safe"
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Machine">
          <dl className="space-y-1.5 text-[var(--text-sm)]">
            {env &&
              (
                [
                  ["Distro", env.distro],
                  ["Kernel", env.kernel],
                  ["Session", `${env.desktop} (${env.session_type})`],
                  ["Compositor", env.compositor],
                  ["Audio", env.audio],
                  ["Packages", env.package_manager],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="flex justify-between gap-4">
                  <dt className="text-[var(--text-muted)]">{label}</dt>
                  <dd className="text-right font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                    {value || "—"}
                  </dd>
                </div>
              ))}
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--text-muted)]">Uptime</dt>
              <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                {duration(system.uptime_seconds)}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel title="Resources">
          <dl className="space-y-1.5 text-[var(--text-sm)]">
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--text-muted)]">Swap</dt>
              <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                {system.swap
                  ? `${bytes(system.swap.used)} / ${bytes(system.swap.total)}`
                  : "—"}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--text-muted)]">Network sent</dt>
              <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                {bytes(system.network?.sent)}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-[var(--text-muted)]">Network received</dt>
              <dd className="font-[family-name:var(--font-mono)] text-[var(--text-xs)]">
                {bytes(system.network?.received)}
              </dd>
            </div>
          </dl>
        </Panel>
      </div>
    </div>
  );
}
