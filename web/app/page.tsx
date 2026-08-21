"use client";

/**
 * The app shell.
 *
 * Single route with hash-based views rather than Next routes. A static export
 * served by FastAPI's StaticFiles resolves nested routes through
 * directory-index lookups, which turns every deep-link refresh into a
 * trailing-slash question. One route sidesteps that entirely, and the app is
 * small enough that code-splitting per screen would buy nothing.
 */

import { useEffect, useState } from "react";

import { ActivityView } from "@/components/ActivityView";
import { Console } from "@/components/Console";
import { SettingsView } from "@/components/SettingsView";
import { SystemView } from "@/components/SystemView";
import { VoiceView } from "@/components/VoiceView";
import { classNames } from "@/lib/format";
import { useBrahma, type Connection } from "@/lib/useBrahma";

const VIEWS = [
  { id: "console", label: "Console" },
  { id: "system", label: "System" },
  { id: "activity", label: "Activity" },
  { id: "voice", label: "Voice" },
  { id: "settings", label: "Settings" },
] as const;

type ViewId = (typeof VIEWS)[number]["id"];

const STATUS: Record<Connection, { label: string; colour: string }> = {
  connecting: { label: "Connecting", colour: "var(--moderate)" },
  open: { label: "Connected", colour: "var(--safe)" },
  reconnecting: { label: "Reconnecting", colour: "var(--moderate)" },
  offline: { label: "Server down", colour: "var(--critical)" },
  unauthorized: { label: "Not authorised", colour: "var(--critical)" },
};

function useHashView(): [ViewId, (view: ViewId) => void] {
  const [view, setView] = useState<ViewId>("console");

  useEffect(() => {
    const read = () => {
      const id = window.location.hash.replace(/^#\/?/, "") as ViewId;
      if (VIEWS.some((entry) => entry.id === id)) setView(id);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);

  return [
    view,
    (next: ViewId) => {
      window.location.hash = `#/${next}`;
      setView(next);
    },
  ];
}

export default function Page() {
  const { connection, hello, messages, busy, ask, respondToConfirm, reset } = useBrahma();
  const [view, setView] = useHashView();
  const status = STATUS[connection];

  return (
    <div className="flex h-dvh">
      <nav className="flex shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] p-3"
           style={{ width: "var(--nav-width)" }}>
        <div className="px-2 py-2">
          <div className="text-[var(--text-lg)] font-semibold tracking-tight">
            {hello?.assistantName ?? "Brahma"}
          </div>
          <div className="text-[var(--text-xs)] text-[var(--text-faint)]">
            {hello ? `${hello.skills} skills` : "…"}
          </div>
        </div>

        <ul className="mt-4 space-y-0.5">
          {VIEWS.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                onClick={() => setView(entry.id)}
                aria-current={view === entry.id ? "page" : undefined}
                className={classNames(
                  "w-full rounded-[var(--radius)] px-2.5 py-1.5 text-left text-[var(--text-sm)] transition-colors",
                  view === entry.id
                    ? "bg-[var(--accent-dim)] text-[var(--accent)]"
                    : "text-[var(--text-muted)] hover:bg-[var(--surface-raised)] hover:text-[var(--text)]",
                )}
                style={{ transitionDuration: "var(--motion-fast)" }}
              >
                {entry.label}
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-auto space-y-2 px-2 pt-4">
          <div className="flex items-center gap-2 text-[var(--text-xs)]">
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: status.colour }}
            />
            <span style={{ color: status.colour }}>{status.label}</span>
          </div>
          {hello?.model && (
            <div
              className="truncate font-[family-name:var(--font-mono)] text-[var(--text-xs)] text-[var(--text-faint)]"
              title={hello.model}
            >
              {hello.model}
            </div>
          )}
        </div>
      </nav>

      <main className="min-w-0 flex-1 overflow-hidden">
        <div className="mx-auto flex h-full flex-col px-6 py-4"
             style={{ maxWidth: "var(--content-max)" }}>
          {connection === "unauthorized" && (
            <div
              className="mb-4 rounded-[var(--radius)] border-l-2 px-3 py-2 text-[var(--text-sm)]"
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
              onAsk={ask}
              onRespond={respondToConfirm}
              onReset={reset}
            />
          )}
          {view !== "console" && (
            <div className="overflow-y-auto pb-6">
              {view === "system" && <SystemView />}
              {view === "activity" && <ActivityView />}
              {view === "voice" && <VoiceView />}
              {view === "settings" && <SettingsView />}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
