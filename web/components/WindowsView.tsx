"use client";

import { useState } from "react";

import { homeOf, shortCwd, windowArgs } from "@/lib/windows";
import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Chip, Empty, Panel, SectionTitle } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

interface WindowRow {
  id: string;
  title: string;
  app: string;
  pid: number | null;
  workspace: string;
  focused: boolean;
  cwd: string | null;
}

interface WindowsSnapshot {
  available: boolean;
  backend?: string;
  error?: string;
  workspaces: string[];
  windows: WindowRow[];
}

export function WindowsView({ run }: { run: RunSkill }) {
  const { data, error, reload } = useResource<WindowsSnapshot>("/api/windows", 4000);
  const skill = useSkill(run);
  const [workspace, setWorkspace] = useState<string>("");

  const act = async (name: string, args: Record<string, unknown>) => {
    await skill.call(name, args);
    reload();
  };

  if (error) return <Screen><Empty>Could not read windows: {error}</Empty></Screen>;
  if (!data) return <Screen><Empty>Reading windows…</Empty></Screen>;

  if (!data.available) {
    return (
      <Screen>
        <Empty>
          No supported compositor detected{data.error ? ` — ${data.error}` : ""}. Window
          control needs Hyprland, Sway, or an X11 session with wmctrl.
        </Empty>
      </Screen>
    );
  }

  const visible = workspace
    ? data.windows.filter((w) => w.workspace === workspace)
    : data.windows;

  return (
    <Screen
      subtitle={`${data.windows.length} open across ${data.workspaces.length} workspaces · ${data.backend}`}
      actions={
        <>
          <Button onClick={reload}>Refresh</Button>
          <Button onClick={() => void act("toggle_fullscreen", {})}>Fullscreen</Button>
          <Button onClick={() => void act("toggle_floating", {})}>Float</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="no-scrollbar flex gap-2 overflow-x-auto">
          <Chip active={workspace === ""} onClick={() => setWorkspace("")}>
            All
          </Chip>
          {data.workspaces.map((ws) => (
            <Chip key={ws} active={workspace === ws} onClick={() => setWorkspace(ws)}>
              Workspace {ws}
            </Chip>
          ))}
        </div>

        <SkillOutput state={skill} />

        <div>
          <SectionTitle count={visible.length}>Open windows</SectionTitle>
          {visible.length === 0 ? (
            <Empty>Nothing on this workspace.</Empty>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {visible.map((win) => (
                <div
                  key={win.id}
                  className="glass rounded-[var(--radius-lg)] p-3.5"
                  style={{
                    borderColor: win.focused ? "var(--accent)" : "var(--glass-border)",
                  }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
                        {win.app || "(unknown)"}
                      </div>
                      <div
                        className="truncate text-[length:var(--text-xs)] text-[var(--text-muted)]"
                        title={win.title}
                      >
                        {win.title || "(untitled)"}
                      </div>
                    </div>
                    {win.focused && (
                      <span
                        className="shrink-0 rounded-[var(--radius-sm)] px-1.5 py-0.5 text-[length:var(--text-xs)] font-semibold"
                        style={{ background: "var(--accent-dim)", color: "var(--accent)" }}
                      >
                        focused
                      </span>
                    )}
                  </div>

                  <div className="mt-2 flex items-center gap-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
                    <span>ws {win.workspace || "?"}</span>
                    {win.pid && <span>pid {win.pid}</span>}
                  </div>
                  {win.cwd && (
                    <div
                      className="mt-1 truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]"
                      title={win.cwd}
                    >
                      {shortCwd(win.cwd, homeOf(win.cwd))}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button onClick={() => void act("focus_window", windowArgs(win))}>
                      Focus
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => void act("close_window", windowArgs(win))}
                    >
                      Close
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <Panel title="Workspaces">
          <ActionRow>
            {data.workspaces.map((ws) => (
              <Button key={ws} onClick={() => void act("switch_workspace", { workspace: ws })}>
                Go to {ws}
              </Button>
            ))}
          </ActionRow>
        </Panel>
      </div>
    </Screen>
  );
}
