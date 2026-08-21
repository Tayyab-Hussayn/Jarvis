"use client";

/**
 * The app shell.
 *
 * Single route with hash-based views rather than Next routes. A static export
 * served by FastAPI's StaticFiles resolves nested routes through
 * directory-index lookups, which turns every deep-link refresh into a
 * trailing-slash question. One route sidesteps that, and the app is small
 * enough that per-screen code splitting would buy nothing.
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
import { ConfirmOverlay, Dock, Rail, Screen, VIEWS, type ViewId } from "@/components/Shell";
import { SystemView } from "@/components/SystemView";
import { WindowsView } from "@/components/WindowsView";
import type { SkillInfo } from "@/lib/protocol";
import { useResource } from "@/lib/useApi";
import { useBrahma } from "@/lib/useBrahma";

const IDS = new Set<string>([...VIEWS.map((v) => v.id), "settings"]);

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
  } = useBrahma();

  const [view, setView] = useHashView();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const skills = useResource<{ skills: SkillInfo[] }>("/api/skills");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const unauthorized = connection === "unauthorized";

  return (
    <div className="flex h-dvh overflow-hidden">
      <Rail view={view} onSelect={setView} skills={hello?.skills} />

      <main className="relative min-w-0 flex-1 overflow-hidden">
        <div
          className="mx-auto flex h-full flex-col px-6 pt-5"
          style={{ maxWidth: "var(--content-max)", paddingBottom: "var(--dock-height)" }}
        >
          {unauthorized && (
            <div
              className="mb-4 rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
              style={{
                borderLeftColor: "var(--critical)",
                background: "var(--critical-dim)",
              }}
            >
              No valid token. Start the server with{" "}
              <code className="font-[family-name:var(--font-mono)]">brahma serve</code> and
              open the URL it prints — it carries the token.
            </div>
          )}

          {view === "console" && (
            <Screen
              title="Console"
              subtitle={hello?.environment}
              actions={
                <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  {hello ? `${hello.skills} skills available` : ""}
                </span>
              }
            >
              <Console
                messages={messages}
                busy={busy}
                connection={connection}
                onAsk={ask}
                onRespond={respondToConfirm}
                onReset={reset}
              />
            </Screen>
          )}

          {view === "system" && <SystemView run={run} />}
          {view === "windows" && <WindowsView run={run} />}
          {view === "files" && <FilesView run={run} />}
          {view === "apps" && <AppsView run={run} />}
          {view === "control" && <ControlView run={run} />}
          {view === "screen" && <ScreenView run={run} />}
          {view === "activity" && <ActivityView />}
          {view === "brain" && <BrainView run={run} />}
          {view === "settings" && <SettingsView />}
        </div>

        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-4">
          <Dock
            connection={connection}
            model={hello?.model}
            busy={busy}
            onPalette={() => setPaletteOpen(true)}
          />
        </div>
      </main>

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
