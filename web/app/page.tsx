"use client";

/**
 * The app shell.
 *
 * Single route with hash-based views rather than Next routes. A static export
 * served by FastAPI's StaticFiles resolves nested routes through
 * directory-index lookups, which turns every deep-link refresh into a
 * trailing-slash question. One route sidesteps that, and the app is small
 * enough that per-screen code splitting would buy nothing.
 *
 * Layout is rail | (top bar / content). The floating dock is gone: it occluded
 * panel content on every screen and competed with the composer. Its contents
 * moved into the top bar, where they are always visible without covering
 * anything.
 */

import { useCallback, useEffect, useState } from "react";

import { ActivityView } from "@/components/ActivityView";
import { AppsView } from "@/components/AppsView";
import { BrainView } from "@/components/BrainView";
import { Console } from "@/components/Console";
import { ControlView } from "@/components/ControlView";
import { AutoBanner } from "@/components/AutoBanner";
import { FilesView } from "@/components/FilesView";
import { HabitsView } from "@/components/HabitsView";
import { JobsView } from "@/components/JobsView";
import { Palette } from "@/components/Palette";
import { ScreenView } from "@/components/ScreenView";
import { PhonesView } from "@/components/PhonesView";
import { SettingsView } from "@/components/SettingsView";
import {
  ConfirmOverlay,
  Rail,
  TopBar,
  VIEWS,
  type ViewId,
} from "@/components/Shell";
import { SystemView } from "@/components/SystemView";
import { WindowsView } from "@/components/WindowsView";
import type { SkillInfo, StateSnapshot, SystemSnapshot } from "@/lib/protocol";
import { isVoiceLive } from "@/lib/protocol";
import { useResource } from "@/lib/useApi";
import { usePolicy } from "@/lib/usePolicy";
import { useKravein } from "@/lib/useKravein";

const IDS = new Set<string>(VIEWS.map((view) => view.id));
const TITLES: Record<string, string> = Object.fromEntries(
  VIEWS.map((view) => [view.id, view.label]),
);
const RAIL_KEY = "kravein.railExpanded";

function useHashView(): [ViewId, (view: ViewId) => void] {
  const [view, setView] = useState<ViewId>("console");

  useEffect(() => {
    const read = () => {
      const id = window.location.hash.replace(/^#\/?/, "");
      if (IDS.has(id)) setView(id as ViewId);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  return [
    view,
    useCallback((next: ViewId) => {
      window.location.hash = `#/${next}`;
      setView(next);
    }, []),
  ];
}

export default function Page() {
  const {
    connection,
    hello,
    messages,
    busy,
    panelConfirms,
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
    voiceStopping,
  } = useKravein();

  const [view, setView] = useHashView();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [railExpanded, setRailExpanded] = useState(false);

  const policy = usePolicy();
  const skills = useResource<{ skills: SkillInfo[] }>("/api/skills");
  const state = useResource<StateSnapshot>("/api/state");
  // Polled once here and shared with the top bar, so every screen shows the
  // same vitals rather than each maintaining its own timer.
  const system = useResource<SystemSnapshot>("/api/system", 3000);
  const voice = useResource<{
    stt_backend: string;
    stt_model: string;
    tts_available: boolean;
    live_configured: boolean;
  }>("/api/voice");

  // There are two independent pipelines and either one is enough. The
  // on-device path needs both halves — something to hear with and something to
  // answer through — while Live needs only a Gemini key, since it does both
  // itself. Gating everything on the local halves is what disabled voice
  // entirely on a machine with a key but no local speech model, even though
  // the server would happily have started Live.
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

  // Send the preference, not a decision. `voice.mode` in settings picks the
  // pipeline and the server resolves it — "auto" prefers Live when a key is
  // present, because it answers in ~7s against ~19s and hears continuously
  // rather than an utterance at a time.
  const selectVoice = useCallback(() => startVoice("auto"), [startVoice]);

  useEffect(() => {
    setRailExpanded(localStorage.getItem(RAIL_KEY) === "1");
  }, []);

  const toggleRail = useCallback(() => {
    setRailExpanded((open) => {
      localStorage.setItem(RAIL_KEY, open ? "0" : "1");
      return !open;
    });
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
      // There is no panel to show or hide any more, so this starts and stops
      // the session itself.
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

  return (
    <div className="flex h-dvh overflow-hidden">
      <Rail
        view={view}
        onSelect={setView}
        expanded={railExpanded}
        onToggle={toggleRail}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          title={TITLES[view] ?? "Kravein"}
          hostname={state.data?.hostname}
          environment={hello?.environment}
          system={system.data}
          connection={connection}
          onPalette={() => setPaletteOpen(true)}
          voiceActive={isVoiceLive(voicePhase)}
          voiceOpen={isVoiceLive(voicePhase)}
          onToggleVoice={() => (isVoiceLive(voicePhase) ? stopVoice() : selectVoice())}
        />

        <AutoBanner policy={policy} />

        <div className="flex min-h-0 flex-1 overflow-hidden">
        <main className="min-h-0 flex-1 overflow-hidden">
          <div
            key={view}
            className="mx-auto flex h-full flex-col px-6 py-5"
            style={{
              maxWidth: "var(--content-max)",
              animation: "view-in var(--motion-base) var(--ease)",
            }}
          >
            {connection === "unauthorized" && (
              <div
                className="mb-4 rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
                style={{
                  borderLeftColor: "var(--critical)",
                  background: "var(--critical-dim)",
                }}
              >
                No valid token. Start the server with{" "}
                <code className="font-[family-name:var(--font-mono)]">kravein serve</code>{" "}
                and open the URL it prints — it carries the token.
              </div>
            )}

            {view === "console" && (
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
                voiceReady={voiceReady}
                canSwitchPipeline={localReady && liveConfigured}
                voiceUnavailableReason={voiceUnavailableReason}
                onSelectText={stopVoice}
                onSelectVoice={selectVoice}
                onRestartVoice={restartVoice}
                onDismissVoiceError={dismissVoiceError}
              />
            )}
            {view === "schedule" && <JobsView run={run} jobEvents={jobEvents} />}
            {view === "habits" && <HabitsView run={run} routineEvents={routineEvents} />}
            {view === "system" && <SystemView run={run} system={system.data} />}
            {view === "windows" && <WindowsView run={run} />}
            {view === "files" && <FilesView run={run} />}
            {view === "apps" && <AppsView run={run} />}
            {view === "control" && <ControlView run={run} system={system.data} />}
            {view === "phones" && <PhonesView />}
            {view === "screen" && <ScreenView run={run} />}
            {view === "activity" && <ActivityView />}
            {view === "brain" && <BrainView run={run} />}
            {view === "settings" && <SettingsView policy={policy} />}
          </div>
        </main>

        </div>
      </div>

      <ConfirmOverlay
        confirms={panelConfirms}
        onRespond={respondToConfirm}
        onDismiss={dismissPanelConfirm}
      />

      <Palette
        open={paletteOpen}
        skills={skills.data?.skills ?? []}
        onClose={() => setPaletteOpen(false)}
        onRun={(name, args) => {
          void run(name, args).catch(() => undefined);
        }}
      />
    </div>
  );
}
