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
import { FilesView } from "@/components/FilesView";
import { Palette } from "@/components/Palette";
import { ScreenView } from "@/components/ScreenView";
import { SettingsView } from "@/components/SettingsView";
import {
  ConfirmOverlay,
  Rail,
  TopBar,
  VIEWS,
  type ViewId,
} from "@/components/Shell";
import { SystemView } from "@/components/SystemView";
import { VoicePanel } from "@/components/VoicePanel";
import { WindowsView } from "@/components/WindowsView";
import type { SkillInfo, StateSnapshot, SystemSnapshot } from "@/lib/protocol";
import { useResource } from "@/lib/useApi";
import { useBrahma } from "@/lib/useBrahma";

const IDS = new Set<string>(VIEWS.map((view) => view.id));
const TITLES: Record<string, string> = Object.fromEntries(
  VIEWS.map((view) => [view.id, view.label]),
);
const RAIL_KEY = "brahma.railExpanded";

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
    respondToConfirm,
    dismissPanelConfirm,
    reset,
    voicePhase,
    voiceLines,
    voiceMode,
    voiceError,
    startVoice,
    stopVoice,
    clearVoiceLines,
  } = useBrahma();

  const [view, setView] = useHashView();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [railExpanded, setRailExpanded] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);

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
      if (
        (event.metaKey || event.ctrlKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === "v"
      ) {
        event.preventDefault();
        setVoiceOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
          title={TITLES[view] ?? "Brahma"}
          hostname={state.data?.hostname}
          environment={hello?.environment}
          system={system.data}
          connection={connection}
          onPalette={() => setPaletteOpen(true)}
          voiceActive={voicePhase !== "off" && voicePhase !== "error"}
          voiceOpen={voiceOpen}
          onToggleVoice={() => setVoiceOpen((open) => !open)}
        />

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
                <code className="font-[family-name:var(--font-mono)]">brahma serve</code>{" "}
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
              />
            )}
            {view === "system" && <SystemView run={run} system={system.data} />}
            {view === "windows" && <WindowsView run={run} />}
            {view === "files" && <FilesView run={run} />}
            {view === "apps" && <AppsView run={run} />}
            {view === "control" && <ControlView run={run} system={system.data} />}
            {view === "screen" && <ScreenView run={run} />}
            {view === "activity" && <ActivityView />}
            {view === "brain" && <BrainView run={run} />}
            {view === "settings" && <SettingsView />}
          </div>
        </main>

        <VoicePanel
          open={voiceOpen}
          onClose={() => setVoiceOpen(false)}
          phase={voicePhase}
          lines={voiceLines}
          mode={voiceMode}
          error={voiceError}
          readiness={voice.data}
          connected={connection === "open"}
          onStart={startVoice}
          onStop={stopVoice}
          onClear={clearVoiceLines}
        />
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
