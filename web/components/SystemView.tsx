"use client";

import { useEffect, useRef, useState } from "react";

import type { StateSnapshot, SystemSnapshot } from "@/lib/protocol";
import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import { bytes, duration } from "@/lib/format";
import { Button, Chip, Empty, Field, Input, Panel, SectionTitle, Stat } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Icon } from "./icons";
import { Screen } from "./Shell";

const POLL_MS = 3000;
const SPARK_POINTS = 40;

interface ProcessRow {
  pid: number;
  name: string;
  user: string;
  cpu: number;
  memory: number;
  status: string;
}

function tone(value: number | undefined): "accent" | "moderate" | "critical" {
  if (value === undefined) return "accent";
  if (value >= 90) return "critical";
  if (value >= 75) return "moderate";
  return "accent";
}

/** Keeps a rolling window of a polled value so stats can show a sparkline. */
function useHistory(value: number | undefined, length = SPARK_POINTS): number[] {
  const ref = useRef<number[]>([]);
  const [series, setSeries] = useState<number[]>([]);
  useEffect(() => {
    if (value === undefined) return;
    ref.current = [...ref.current, value].slice(-length);
    setSeries(ref.current);
  }, [value, length]);
  return series;
}

export function SystemView({
  run,
  system,
}: {
  run: RunSkill;
  system: SystemSnapshot | null;
}) {
  const { data: state } = useResource<StateSnapshot>("/api/state");
  const procs = useResource<{ processes: ProcessRow[]; total: number }>(
    "/api/processes?sort=cpu&limit=30",
    5000,
  );
  const skill = useSkill(run);
  const [unit, setUnit] = useState("");
  const [sort, setSort] = useState<"cpu" | "memory">("cpu");

  const cpuHistory = useHistory(system?.cpu_percent);
  const memHistory = useHistory(system?.memory?.percent);

  if (!system) return <Screen><Empty>Reading system state…</Empty></Screen>;
  if (!system.available) {
    return <Screen><Empty>psutil is not installed.</Empty></Screen>;
  }

  const rows = (procs.data?.processes ?? [])
    .slice()
    .sort((a, b) => (sort === "cpu" ? b.cpu - a.cpu : b.memory - a.memory));

  return (
    <Screen
      actions={
        <>
          <Button onClick={() => void skill.call("system_status", { detailed: true })}>
            Full report
          </Button>
          <Button onClick={() => void skill.call("list_services", { failed_only: true })}>
            Failed units
          </Button>
        </>
      }
    >
      <div className="space-y-4 overflow-y-auto pb-6">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat
            label="CPU"
            value={Math.round(system.cpu_percent ?? 0)}
            unit="%"
            detail={
              system.load
                ? `load ${system.load.map((v) => v.toFixed(2)).join("  ")}`
                : `${system.cpu_count ?? "?"} cores`
            }
            spark={cpuHistory}
            percent={system.cpu_percent}
            tone={tone(system.cpu_percent)}
          />
          <Stat
            label="Memory"
            value={Math.round(system.memory?.percent ?? 0)}
            unit="%"
            detail={`${bytes(system.memory?.used)} of ${bytes(system.memory?.total)}`}
            spark={memHistory}
            percent={system.memory?.percent}
            tone={tone(system.memory?.percent)}
          />
          <Stat
            label="Disk /"
            value={Math.round(system.disk?.percent ?? 0)}
            unit="%"
            detail={`${bytes(system.disk?.used)} of ${bytes(system.disk?.total)}`}
            percent={system.disk?.percent}
            tone={tone(system.disk?.percent)}
          />
          <Stat
            label="Uptime"
            value={duration(system.uptime_seconds)}
            detail={
              system.network
                ? `↑ ${bytes(system.network.sent)}  ↓ ${bytes(system.network.received)}`
                : undefined
            }
          />
        </div>

        <SkillOutput state={skill} />

        <div>
          <SectionTitle
            count={procs.data?.total}
            actions={
              <div className="flex gap-2">
                <Chip active={sort === "cpu"} onClick={() => setSort("cpu")}>
                  CPU
                </Chip>
                <Chip active={sort === "memory"} onClick={() => setSort("memory")}>
                  Memory
                </Chip>
              </div>
            }
          >
            Processes
          </SectionTitle>

          <Panel padded={false}>
            <div className="max-h-[46vh] overflow-auto">
              <table className="w-full text-left text-[length:var(--text-xs)]">
                <thead className="sticky top-0 bg-[var(--surface)]">
                  <tr className="text-[var(--text-faint)]">
                    <th className="px-4 py-2 font-medium">Process</th>
                    <th className="px-3 py-2 font-medium">PID</th>
                    <th className="px-3 py-2 font-medium">User</th>
                    <th className="px-3 py-2 text-right font-medium">CPU</th>
                    <th className="px-3 py-2 text-right font-medium">Mem</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((proc) => (
                    <tr
                      key={proc.pid}
                      className="group/row border-t border-[var(--border)] hover:bg-[var(--surface-raised)]"
                    >
                      <td className="max-w-[18rem] truncate px-4 py-1.5 font-[family-name:var(--font-mono)]">
                        {proc.name}
                      </td>
                      <td className="px-3 py-1.5 tabular-nums text-[var(--text-muted)]">
                        {proc.pid}
                      </td>
                      <td className="px-3 py-1.5 text-[var(--text-muted)]">{proc.user}</td>
                      <td
                        className="px-3 py-1.5 text-right tabular-nums"
                        style={{ color: proc.cpu > 50 ? "var(--moderate)" : undefined }}
                      >
                        {proc.cpu.toFixed(1)}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums">
                        {proc.memory.toFixed(1)}
                      </td>
                      <td className="px-4 py-1.5 text-right">
                        <button
                          type="button"
                          onClick={async () => {
                            await skill.call("kill_process", { target: String(proc.pid) });
                            procs.reload();
                          }}
                          aria-label={`Kill ${proc.name} (pid ${proc.pid})`}
                          title={`Kill ${proc.name} (${proc.pid}) — asks first`}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)] border border-transparent text-[var(--text-faint)] opacity-0 hover:border-[var(--critical)] hover:text-[var(--critical)] focus-visible:opacity-100 group-hover/row:opacity-100"
                          style={{ transition: "all var(--motion-fast) var(--ease)" }}
                        >
                          <Icon name="trash" size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 && <Empty>No processes readable.</Empty>}
            </div>
          </Panel>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Services & logs">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={unit}
                onChange={setUnit}
                placeholder="unit name, e.g. NetworkManager"
                mono
                className="min-w-0 flex-1"
                onEnter={() => unit && void skill.call("service_status", { name: unit })}
              />
              <Button
                disabled={!unit}
                onClick={() => void skill.call("service_status", { name: unit })}
              >
                Status
              </Button>
              <Button onClick={() => void skill.call("read_logs", { unit, lines: 60 })}>
                Logs
              </Button>
            </div>
            <ActionRow>
              <Button onClick={() => void skill.call("list_services", {})}>All units</Button>
              <Button onClick={() => void skill.call("network_status", {})}>Network</Button>
              <Button onClick={() => void skill.call("disk_usage", { path: "/" })}>Disks</Button>
              <Button onClick={() => void skill.call("environment_report", {})}>
                Environment
              </Button>
            </ActionRow>
          </Panel>

          {/* Machine identity, after the Cardiology reference's patient card:
              a portrait block that says whose data this is, then the vitals
              beside it. Here it answers "which machine am I in control of". */}
          <Panel title="This machine" padded={false}>
            {state && (
              <>
                <div className="flex items-center gap-3.5 border-b border-[var(--border)] p-4">
                  <div
                    className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[var(--radius)] text-[length:var(--text-lg)] font-bold"
                    style={{
                      background: "var(--accent-dim)",
                      color: "var(--accent)",
                      border: "1px solid var(--accent-line)",
                    }}
                  >
                    {(state.hostname || state.environment.distro || "?")
                      .charAt(0)
                      .toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-[length:var(--text-base)] font-semibold">
                      {state.hostname || "this machine"}
                    </div>
                    <div className="truncate text-[length:var(--text-xs)] text-[var(--text-muted)]">
                      {state.environment.distro} · up {duration(system.uptime_seconds)}
                    </div>
                  </div>
                  <span
                    className="ml-auto shrink-0 rounded-[var(--radius-full)] px-2 py-0.5 text-[length:var(--text-xs)] font-medium"
                    style={{ background: "var(--accent-dim)", color: "var(--accent)" }}
                  >
                    {state.skills} skills
                  </span>
                </div>
                <dl className="space-y-1.5 p-4">
                  <Field label="Kernel" value={state.environment.kernel} />
                  <Field
                    label="Session"
                    value={`${state.environment.desktop} (${state.environment.session_type})`}
                  />
                  <Field label="Compositor" value={state.environment.compositor} />
                  <Field label="Audio" value={state.environment.audio} />
                  <Field label="Packages" value={state.environment.package_manager} />
                  <Field
                    label="Swap"
                    value={
                      system.swap
                        ? `${bytes(system.swap.used)} / ${bytes(system.swap.total)}`
                        : "—"
                    }
                  />
                </dl>
              </>
            )}
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
