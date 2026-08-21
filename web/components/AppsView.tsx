"use client";

import { useMemo, useState } from "react";

import { useResource, useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Chip, Empty, Input, Panel, SectionTitle } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

interface AppRow {
  name: string;
  comment: string;
  categories: string[];
  terminal: boolean;
}

interface AppsSnapshot {
  available: boolean;
  error?: string;
  apps: AppRow[];
}

/** Freedesktop main categories worth surfacing as filters. */
const CATEGORIES = [
  "Development",
  "Graphics",
  "Network",
  "AudioVideo",
  "Office",
  "System",
  "Utility",
  "Game",
];

export function AppsView({ run }: { run: RunSkill }) {
  const { data, error, reload } = useResource<AppsSnapshot>("/api/apps");
  const skill = useSkill(run);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [pkgQuery, setPkgQuery] = useState("");

  const visible = useMemo(() => {
    const rows = data?.apps ?? [];
    const query = search.trim().toLowerCase();
    return rows.filter((app) => {
      if (category && !app.categories.includes(category)) return false;
      if (!query) return true;
      return (
        app.name.toLowerCase().includes(query) ||
        app.comment.toLowerCase().includes(query)
      );
    });
  }, [data, search, category]);

  if (error) return <Screen><Empty>Could not read apps: {error}</Empty></Screen>;

  return (
    <Screen
      subtitle={`${data?.apps.length ?? 0} desktop entries · packages and games`}
      actions={
        <>
          <Button onClick={reload}>Rescan</Button>
          <Button onClick={() => void skill.call("check_updates", {})}>Updates</Button>
          <Button onClick={() => void skill.call("list_orphan_packages", {})}>Orphans</Button>
        </>
      }
    >
      <div className="space-y-4 overflow-y-auto pb-6">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            value={search}
            onChange={setSearch}
            placeholder="Search installed apps…"
            className="min-w-0 flex-1"
          />
        </div>

        <div className="no-scrollbar flex gap-2 overflow-x-auto">
          <Chip active={category === ""} onClick={() => setCategory("")}>
            All
          </Chip>
          {CATEGORIES.map((c) => (
            <Chip key={c} active={category === c} onClick={() => setCategory(c)}>
              {c}
            </Chip>
          ))}
        </div>

        <SkillOutput state={skill} />

        <div>
          <SectionTitle count={visible.length}>Installed</SectionTitle>
          {visible.length === 0 ? (
            <Empty>Nothing matches.</Empty>
          ) : (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {visible.slice(0, 120).map((app) => (
                <div
                  key={app.name}
                  className="flex items-center gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[length:var(--text-sm)]">{app.name}</div>
                    {app.comment && (
                      <div className="truncate text-[length:var(--text-xs)] text-[var(--text-faint)]">
                        {app.comment}
                      </div>
                    )}
                  </div>
                  <Button onClick={() => void skill.call("open_app", { app_name: app.name })}>
                    Open
                  </Button>
                </div>
              ))}
            </div>
          )}
          {visible.length > 120 && (
            <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              Showing the first 120 — narrow the search to see the rest.
            </p>
          )}
        </div>

        <Panel title="Packages">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={pkgQuery}
              onChange={setPkgQuery}
              placeholder="Search the package repos…"
              className="min-w-0 flex-1"
              onEnter={() =>
                pkgQuery && void skill.call("search_packages", { query: pkgQuery })
              }
            />
            <Button
              variant="primary"
              disabled={!pkgQuery}
              onClick={() => void skill.call("search_packages", { query: pkgQuery })}
            >
              Search
            </Button>
            <Button
              disabled={!pkgQuery}
              onClick={() => void skill.call("package_info", { name: pkgQuery })}
            >
              Info
            </Button>
          </div>
          <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            Installing is a critical action — it will ask before running.
          </p>
        </Panel>

        <Panel title="Games">
          <ActionRow>
            <Button onClick={() => void skill.call("list_games", {})}>List games</Button>
            <Button onClick={() => void skill.call("game_storage", {})}>Storage used</Button>
            <Button onClick={() => void skill.call("update_games", {})}>Update</Button>
          </ActionRow>
        </Panel>
      </div>
    </Screen>
  );
}
