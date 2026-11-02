"use client";

/**
 * The app: "Command Constellation".
 *
 * No sidebar. A HUD bar on top, the Orchestrator and its sub-agents as the
 * hero, the conversation as a transmission stream beneath them with a floating
 * composer capsule, and a dock whose modules open as glass panes over the
 * canvas. A pane is addressed by `#name` (`#settings`, `#agent/research`), so
 * deep links, the old `#/name` links and the browser's Back button all work.
 *
 * Single route with hash-based views rather than Next routes: a static export
 * served by FastAPI's StaticFiles resolves nested routes through
 * directory-index lookups, which turns every deep-link refresh into a
 * trailing-slash question.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ActivityView } from "@/components/ActivityView";
import { AgentPane } from "@/components/AgentPane";
import { AppsView } from "@/components/AppsView";
import { AutoBanner } from "@/components/AutoBanner";
import { BrainView } from "@/components/BrainView";
import { Constellation, AgentStrip } from "@/components/Constellation";
import { Console } from "@/components/Console";
import { ControlView } from "@/components/ControlView";
import { FilesView } from "@/components/FilesView";
import { HabitsView } from "@/components/HabitsView";
import { JobsView } from "@/components/JobsView";
import { Palette, type PaletteCommand } from "@/components/Palette";
import { PhonesView } from "@/components/PhonesView";
import { ScreenView } from "@/components/ScreenView";
import { SettingsView } from "@/components/SettingsView";
import {
  ConfirmOverlay,
  Dock,
  Hud,
  Pane,
  focusConfirmation,
} from "@/components/Shell";
import { SystemView } from "@/components/SystemView";
import { WindowsView } from "@/components/WindowsView";
import { ORCHESTRATOR, type ProfileInfo, type AgentStateFrame } from "@/lib/agents";
import { overlayConfirms } from "@/lib/confirm";
import {
  MODULES,
  moduleLabel,
  parseHash,
  routeHash,
  type ModuleId,
  type Route,
} from "@/lib/panes";
import type { SkillInfo, StateSnapshot, SystemSnapshot } from "@/lib/protocol";
import { isVoiceLive } from "@/lib/protocol";
import { api } from "@/lib/session";
import { useResource } from "@/lib/useApi";
import { useKravein } from "@/lib/useKravein";
import { usePolicy } from "@/lib/usePolicy";

/** The route lives in the URL hash; this keeps React state and the hash in step. */
function useRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>({ kind: "home" });

  useEffect(() => {
    const read = () => setRoute(parseHash(window.location.hash));
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  const go = useCallback((next: Route) => {
    const hash = routeHash(next);
    // `pushState` keeps the token-free path and search and does not fire
    // `hashchange`, so state is set here; the listener covers Back/Forward.
    history.pushState(null, "", `${window.location.pathname}${window.location.search}${hash}`);
    setRoute(next);
  }, []);

  return [route, go];
}

export default function Page() {
  const {
    connection,
    hello,
    messages,
    busy,
    panelConfirms,
    agents,
    dispatchAgents,
    ask,
    run,
    cancel,
    cancelling,
    jobEvents,
    routineEvents,
    respondToConfirm,
    dismissPanelConfirm,
    reset,
    voicePhase,
    voiceMode,
    voiceError,
    voiceNotes,
    startVoice,
    stopVoice,
    restartVoice,
    dismissVoiceError,
    audioChoice,
    setAudioChoice,
    deviceAudioUnsupported,
    voiceStopping,
  } = useKravein();

  const [route, go] = useRoute();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const closePane = useCallback(() => go({ kind: "home" }), [go]);
  const openModule = useCallback((id: ModuleId) => go({ kind: "module", id }), [go]);
  const openAgent = useCallback((name: string) => go({ kind: "agent", name }), [go]);

  const policy = usePolicy();
  const skills = useResource<{ skills: SkillInfo[] }>("/api/skills");
  const state = useResource<StateSnapshot>("/api/state");
  // Polled once here and shared, so every pane shows the same vitals rather
  // than each maintaining its own timer.
  const system = useResource<SystemSnapshot>("/api/system", 3000);
  const voice = useResource<{
    stt_backend: string;
    stt_model: string;
    tts_available: boolean;
    live_configured: boolean;
  }>("/api/voice");

  // Identity and a correct-at-once snapshot from the engine (#146). Older
  // servers answer 404; the store then keeps deriving from frames. Re-asked on
  // every hello, so a reconnect or a resume starts from the server's truth.
  useEffect(() => {
    if (!hello) return;
    let live = true;
    api<{ agents: ProfileInfo[]; orchestrator_enabled?: boolean }>("/api/agents")
      .then((r) => {
        if (live && Array.isArray(r?.agents)) {
          dispatchAgents({
            t: "profiles",
            agents: r.agents,
            orchestratorEnabled: r.orchestrator_enabled,
          });
        }
      })
      .catch(() => undefined);
    api<{ states: AgentStateFrame[] }>("/api/agents/state")
      .then((r) => {
        if (live && Array.isArray(r?.states)) dispatchAgents({ t: "snapshot", states: r.states });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [hello, dispatchAgents]);

  // There are two independent voice pipelines and either one is enough. The
  // on-device path needs both halves (hearing and answering) while Live needs
  // only a Gemini key. Gating everything on the local halves disabled voice on
  // a machine with a key but no local speech model.
  const localReady = Boolean(voice.data?.stt_backend && voice.data?.tts_available);
  const liveConfigured = Boolean(voice.data?.live_configured);
  const voiceReady = localReady || liveConfigured;
  const voiceUnavailableReason = voiceReady || !voice.data
    ? ""
    : [
        !voice.data.stt_backend ? "no speech recognition" : "",
        !voice.data.tts_available ? "no audio playback (install mpv)" : "",
        "no Gemini key for Live",
      ]
        .filter(Boolean)
        .join(", ");

  // This device's mic and speaker run through Gemini Live only, so a missing
  // key or an insecure page is a reason to say, not a pipeline to fall back to.
  const deviceAudioReason =
    deviceAudioUnsupported() ||
    (voice.data && !liveConfigured
      ? "This device's mic and speaker need Gemini Live (no API key set)."
      : "");

  // Send the preference, not a decision: the server resolves "auto".
  const selectVoice = useCallback(() => startVoice("auto"), [startVoice]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
      if (
        (event.metaKey || event.ctrlKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === "v"
      ) {
        event.preventDefault();
        if (isVoiceLive(voicePhase)) stopVoice();
        else selectVoice();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [voicePhase, stopVoice, selectVoice]);

  // Everything the palette can open, above the 100+ skills it can run.
  const commands = useMemo<PaletteCommand[]>(() => {
    const list: PaletteCommand[] = [
      { id: "home", label: "Conversation", hint: "Back to the stream", run: closePane },
      {
        id: "clear",
        label: "Clear conversation",
        hint: busy ? "Wait for the current turn to finish" : "Start a fresh conversation",
        // Same guard as the Clear button: never reset mid-turn.
        run: () => {
          if (!busy) reset();
        },
      },
    ];
    for (const m of MODULES.filter((x) => !x.soon)) {
      list.push({
        id: m.id,
        label: m.label,
        hint: `Open ${m.label}${m.aliases.length ? ` · ${m.aliases.join(", ")}` : ""}`,
        run: () => openModule(m.id),
      });
    }
    for (const name of agents.order) {
      list.push({
        id: `agent-${name}`,
        label: name === ORCHESTRATOR ? "Orchestrator" : name,
        hint: `Agent details · ${agents.agents[name].purpose || "live state"}`,
        run: () => openAgent(name),
      });
    }
    return list;
  }, [agents.order, agents.agents, busy, reset, closePane, openModule, openAgent]);

  const voiceLive = isVoiceLive(voicePhase);
  const paneOpen = route.kind !== "home";
  const confirms = overlayConfirms(panelConfirms, messages, paneOpen);

  const pane = (() => {
    if (route.kind === "agent") {
      return {
        title: route.name === ORCHESTRATOR ? "Orchestrator" : route.name,
        eyebrow: "Agent",
        body: <AgentPane name={route.name} model={agents} onReview={focusConfirmation} />,
      };
    }
    if (route.kind !== "module") return null;
    const bodies: Record<ModuleId, React.ReactNode> = {
      windows: <WindowsView run={run} />,
      apps: <AppsView run={run} />,
      files: <FilesView run={run} />,
      screen: <ScreenView run={run} />,
      system: <SystemView run={run} system={system.data} />,
      control: <ControlView run={run} system={system.data} />,
      habits: <HabitsView run={run} routineEvents={routineEvents} />,
      schedule: <JobsView run={run} jobEvents={jobEvents} />,
      phones: <PhonesView />,
      brain: <BrainView run={run} />,
      activity: <ActivityView />,
      settings: <SettingsView policy={policy} />,
      proposals: null,
    };
    return { title: moduleLabel(route.id), eyebrow: "Module", body: bodies[route.id] };
  })();

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden" style={{ zIndex: 1 }}>
      <Hud
        connection={connection}
        assistant={hello?.assistantName}
        model={hello ? `${hello.provider} · ${hello.model}` : undefined}
        hostname={state.data?.hostname}
        system={system.data}
        policyMode={policy.anchored?.policy.mode}
        onHome={closePane}
        onPalette={() => setPaletteOpen(true)}
        onOpenSettings={() => openModule("settings")}
        voiceActive={voiceLive}
        onToggleVoice={() => (voiceLive ? stopVoice() : selectVoice())}
      />

      <AutoBanner policy={policy} />

      <main className="relative flex min-h-0 flex-1 flex-col pb-[var(--dock-height)] sm:pb-0 sm:pl-[4.5rem]">
        {connection === "unauthorized" && (
          <div
            className="mx-3 mt-3 rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
            style={{ borderLeftColor: "var(--critical)", background: "var(--critical-dim)" }}
          >
            No valid token. Start the server with{" "}
            <code className="font-[family-name:var(--font-mono)]">kravein serve</code> and open
            the URL it prints — it carries the token.
          </div>
        )}

        <AgentStrip
          model={agents}
          voiceThinking={voicePhase === "thinking"}
          onOpenAgent={openAgent}
          onReview={focusConfirmation}
        />
        <Constellation
          model={agents}
          voiceThinking={voicePhase === "thinking"}
          onOpenAgent={openAgent}
          onReview={focusConfirmation}
        />

        <div className="min-h-0 flex-1">
          <Console
            messages={messages}
            busy={busy}
            connection={connection}
            skills={hello?.skills}
            onAsk={ask}
            onRespond={respondToConfirm}
            onReset={reset}
            onCancel={cancel}
            cancelling={cancelling}
            voicePhase={voicePhase}
            voiceStopping={voiceStopping}
            voiceMode={voiceMode}
            voiceError={voiceError}
            voiceNote={voiceNotes[voiceNotes.length - 1] ?? ""}
            voiceReady={audioChoice === "device" ? !deviceAudioReason : voiceReady}
            // Switching pipelines is a laptop-mic thing: device audio only
            // exists on Live.
            canSwitchPipeline={localReady && liveConfigured && audioChoice === "laptop"}
            voiceUnavailableReason={
              audioChoice === "device" ? deviceAudioReason : voiceUnavailableReason
            }
            audioChoice={audioChoice}
            onAudioChoice={setAudioChoice}
            deviceAudioReason={deviceAudioReason}
            onSelectText={stopVoice}
            onSelectVoice={selectVoice}
            onRestartVoice={restartVoice}
            onDismissVoiceError={dismissVoiceError}
          />
        </div>
      </main>

      <Dock route={route} onOpen={openModule} />

      {pane && (
        <Pane title={pane.title} eyebrow={pane.eyebrow} onClose={closePane}>
          {pane.body}
        </Pane>
      )}

      <ConfirmOverlay
        confirms={confirms}
        onRespond={respondToConfirm}
        onDismiss={dismissPanelConfirm}
      />

      <Palette
        open={paletteOpen}
        skills={skills.data?.skills ?? []}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
        onRun={(name, args) => {
          void run(name, args).catch(() => undefined);
        }}
      />
    </div>
  );
}
