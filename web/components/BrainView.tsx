"use client";

import { useMemo, useState } from "react";

import type { SkillInfo, StateSnapshot } from "@/lib/protocol";
import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import {
  Button,
  Chip,
  DangerBadge,
  Empty,
  Field,
  Input,
  LightPanel,
  Panel,
  SectionTitle,
} from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

interface VoiceSnapshot {
  stt_backend: string;
  stt_status: string;
  stt_model: string;
  tts_available: boolean;
  live_configured: boolean;
  mic: {
    floor: number;
    peak: number;
    threshold: number;
    clipped_percent: number;
    clamped: boolean;
    healthy: boolean;
  } | null;
}

type Tab = "model" | "memory" | "voice" | "skills";

const TABS: { id: Tab; label: string }[] = [
  { id: "model", label: "Model" },
  { id: "memory", label: "Memory" },
  { id: "voice", label: "Voice" },
  { id: "skills", label: "Skills" },
];

export function BrainView({ run }: { run: RunSkill }) {
  const [tab, setTab] = useState<Tab>("model");
  const skill = useSkill(run);
  const state = useResource<StateSnapshot>("/api/state");
  const skills = useResource<{ skills: SkillInfo[] }>("/api/skills");
  const voice = useResource<VoiceSnapshot>("/api/voice");

  const [memKey, setMemKey] = useState("");
  const [memValue, setMemValue] = useState("");
  const [skillSearch, setSkillSearch] = useState("");
  const [selected, setSelected] = useState<SkillInfo | null>(null);

  const filtered = useMemo(() => {
    const rows = skills.data?.skills ?? [];
    const query = skillSearch.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter(
      (s) =>
        s.name.includes(query) ||
        s.description.toLowerCase().includes(query) ||
        s.category.toLowerCase().includes(query),
    );
  }, [skills.data, skillSearch]);

  return (
    <Screen
      subtitle={
        state.data
          ? `${state.data.provider} · ${state.data.skills} skills`
          : "Loading…"
      }
      actions={
        <div className="flex gap-2">
          {TABS.map((t) => (
            <Chip key={t.id} active={tab === t.id} onClick={() => setTab(t.id)}>
              {t.label}
            </Chip>
          ))}
        </div>
      }
    >
      <div className="space-y-4">
        <SkillOutput state={skill} />

        {tab === "model" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Active model">
              {state.data ? (
                <dl className="space-y-1.5">
                  <Field label="Provider" value={state.data.provider} />
                  <Field label="Model" value={state.data.model} />
                  <Field label="Fallback" value={state.data.fallback_model || "—"} />
                </dl>
              ) : (
                <Empty>Loading…</Empty>
              )}
              <div className="mt-4">
              <ActionRow>
                <Button onClick={() => void skill.call("llm_status", {})}>Status</Button>
                <Button onClick={() => void skill.call("list_llm_models", {})}>Models</Button>
                <Button onClick={() => void skill.call("reset_llm_failover", {})}>
                  Reset failover
                </Button>
              </ActionRow>
              </div>
            </Panel>

            <Panel title="Providers">
              <div className="space-y-2">
                {(state.data?.providers ?? []).map((provider) => (
                  <div
                    key={provider.name}
                    className="flex items-center justify-between rounded-[var(--radius)] border border-[var(--border)] px-3 py-2"
                  >
                    <span className="font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
                      {provider.name}
                    </span>
                    <span
                      className="text-[length:var(--text-xs)]"
                      style={{
                        color: provider.cooling
                          ? "var(--critical)"
                          : provider.available
                            ? "var(--accent)"
                            : "var(--text-faint)",
                      }}
                    >
                      {provider.cooling
                        ? "cooling after failures"
                        : provider.available
                          ? "ready"
                          : "not configured"}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Machine" className="lg:col-span-2">
              {state.data && (
                <dl className="grid gap-y-1.5 gap-x-10 sm:grid-cols-2">
                  <Field label="Distro" value={state.data.environment.distro} />
                  <Field label="Kernel" value={state.data.environment.kernel} />
                  <Field
                    label="Session"
                    value={`${state.data.environment.desktop} (${state.data.environment.session_type})`}
                  />
                  <Field label="Compositor" value={state.data.environment.compositor} />
                  <Field label="Audio" value={state.data.environment.audio} />
                  <Field label="Packages" value={state.data.environment.package_manager} />
                </dl>
              )}
            </Panel>
          </div>
        )}

        {tab === "memory" && (
          <Panel title="What Kravein remembers">
            <p className="mb-3 text-[length:var(--text-sm)] text-[var(--text-muted)]">
              Facts stored here are injected into the system prompt, so they
              shape every reply.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Input value={memKey} onChange={setMemKey} placeholder="Key" className="w-48" />
              <Input
                value={memValue}
                onChange={setMemValue}
                placeholder="Value"
                className="min-w-0 flex-1"
              />
              <Button
                variant="primary"
                disabled={!memKey || !memValue}
                onClick={() =>
                  void skill.call("remember", { key: memKey, value: memValue })
                }
              >
                Remember
              </Button>
            </div>
            <ActionRow>
              <Button onClick={() => void skill.call("recall", {})}>Recall everything</Button>
              <Button
                disabled={!memKey}
                onClick={() => void skill.call("recall", { query: memKey })}
              >
                Recall “{memKey || "…"}”
              </Button>
              <Button
                variant="danger"
                disabled={!memKey}
                onClick={() => void skill.call("forget", { key: memKey })}
              >
                Forget
              </Button>
            </ActionRow>
          </Panel>
        )}

        {tab === "voice" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Pipeline">
              {voice.data ? (
                <dl className="space-y-1.5">
                  <Field
                    label="Speech to text"
                    value={voice.data.stt_backend || "unavailable"}
                  />
                  <Field label="Model" value={voice.data.stt_model || "—"} />
                  <Field
                    label="Text to speech"
                    value={voice.data.tts_available ? "edge-tts" : "unavailable"}
                    tone={voice.data.tts_available ? "var(--accent)" : "var(--critical)"}
                  />
                  <Field
                    label="Gemini Live"
                    value={voice.data.live_configured ? "key present" : "no key"}
                    tone={
                      voice.data.live_configured ? "var(--accent)" : "var(--text-faint)"
                    }
                  />
                </dl>
              ) : (
                <Empty>Checking…</Empty>
              )}
              {voice.data?.stt_status && (
                <p className="mt-3 text-[length:var(--text-xs)] text-[var(--text-muted)]">
                  {voice.data.stt_status}
                </p>
              )}
            </Panel>

            <Panel title="Which path to use">
              <div className="space-y-3 text-[length:var(--text-sm)] text-[var(--text-muted)]">
                <p>
                  <code className="font-[family-name:var(--font-mono)] text-[var(--text)]">
                    kravein voice
                  </code>{" "}
                  runs entirely on this machine and costs nothing. Measured at
                  about 19s from the end of your sentence to the first spoken word.
                </p>
                <p>
                  <code className="font-[family-name:var(--font-mono)] text-[var(--text)]">
                    kravein voice live
                  </code>{" "}
                  uses Gemini native audio — about 7s, because it transcribes
                  while you are still speaking. Metered per second of audio,
                  including silence.
                </p>
                <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  Voice sessions run in the terminal. Driving the microphone
                  from the browser is a separate design problem from the local
                  pipeline, and is not built.
                </p>
              </div>
            </Panel>
          </div>
        )}

        {tab === "skills" && (
          <div>
            <SectionTitle count={filtered.length}>
              Skills
              <span className="ml-2 font-normal text-[length:var(--text-sm)] text-[var(--text-faint)]">
                click one to see its arguments
              </span>
            </SectionTitle>
            <div className="mb-3">
              <Input
                value={skillSearch}
                onChange={setSkillSearch}
                placeholder="Search skills…"
                className="w-full"
              />
            </div>

            <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
              <div className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
                {filtered.map((s) => (
                  <button
                    key={s.name}
                    type="button"
                    onClick={() => setSelected(s)}
                    className="flex w-full items-baseline gap-2 rounded-[var(--radius)] border px-3 py-2 text-left"
                    style={{
                      borderColor:
                        selected?.name === s.name ? "var(--accent)" : "var(--border)",
                      background:
                        selected?.name === s.name ? "var(--accent-dim)" : "transparent",
                      transition: "all var(--motion-fast) var(--ease)",
                    }}
                  >
                    <span
                      className="shrink-0 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                      style={{
                        color: s.available ? "var(--text)" : "var(--text-faint)",
                      }}
                    >
                      {s.name}
                    </span>
                    {s.danger !== "SAFE" && <DangerBadge danger={s.danger} />}
                    <span className="min-w-0 flex-1 truncate text-[length:var(--text-xs)] text-[var(--text-muted)]">
                      {s.available ? s.description : s.unavailable_reason || "unavailable"}
                    </span>
                  </button>
                ))}
                {filtered.length === 0 && <Empty>Nothing matches.</Empty>}
              </div>

              {selected ? (
                <LightPanel title={selected.name}>
                  <div className="space-y-3 p-4">
                    <p
                      className="text-[length:var(--text-sm)]"
                      style={{ color: "var(--panel-muted)" }}
                    >
                      {selected.description}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <DangerBadge danger={selected.danger} />
                      <span
                        className="text-[length:var(--text-xs)]"
                        style={{ color: "var(--panel-faint)" }}
                      >
                        {selected.category}
                      </span>
                    </div>
                    {!selected.available && (
                      <p
                        className="text-[length:var(--text-xs)]"
                        style={{ color: "var(--destructive)" }}
                      >
                        Unavailable — {selected.unavailable_reason}
                      </p>
                    )}
                    <div>
                      <div
                        className="mb-1 text-[length:var(--text-xs)] uppercase tracking-[0.08em]"
                        style={{ color: "var(--panel-faint)" }}
                      >
                        Arguments
                      </div>
                      <pre
                        className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                        style={{ color: "var(--panel-muted)" }}
                      >
                        {JSON.stringify(selected.parameters, null, 2)}
                      </pre>
                    </div>
                  </div>
                </LightPanel>
              ) : (
                <Panel title="Details">
                  <Empty>Select a skill.</Empty>
                </Panel>
              )}
            </div>
          </div>
        )}
      </div>
    </Screen>
  );
}
