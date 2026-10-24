"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { flatten, groupApps, moveIndex, type LauncherApp } from "@/lib/workspaces";
import { Button } from "./ui";

/**
 * "Open app" dropdown for one workspace: a searchable combobox, frequent apps
 * first. It only reports the choice; the caller runs `open_app` so the launch
 * goes through the same confirmation and audit path as every other panel action.
 */
export function AppLauncher({
  apps,
  frequent,
  workspace,
  busy,
  onLaunch,
}: {
  apps: LauncherApp[];
  frequent: string[];
  workspace: string;
  busy: boolean;
  onLaunch: (app: LauncherApp) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  // Capped so the keyboard order and the rendered rows stay the same list.
  const groups = useMemo(() => {
    const g = groupApps(apps, frequent, query);
    return { frequent: g.frequent, rest: g.rest.slice(0, 150) };
  }, [apps, frequent, query]);
  const flat = useMemo(() => flatten(groups), [groups]);

  const close = () => {
    setOpen(false);
    setQuery("");
    setActive(-1);
  };

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const away = (event: PointerEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  const pick = (app: LauncherApp | undefined) => {
    if (!app) return;
    close();
    onLaunch(app);
  };

  const row = (app: LauncherApp, index: number) => (
    <li
      key={app.name}
      id={`${listId}-${index}`}
      role="option"
      aria-selected={index === active}
      onPointerDown={(event) => event.preventDefault()}
      onClick={() => pick(app)}
      onMouseMove={() => setActive(index)}
      className="cursor-pointer truncate rounded-[var(--radius-sm)] px-2.5 py-1.5 text-[length:var(--text-sm)]"
      style={{
        background: index === active ? "var(--accent-dim)" : "transparent",
        color: index === active ? "var(--accent)" : "var(--text)",
      }}
    >
      {app.name}
      {app.comment && (
        <span className="ml-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {app.comment}
        </span>
      )}
    </li>
  );

  return (
    <div ref={root} className="relative">
      <Button
        onClick={() => (open ? close() : setOpen(true))}
        disabled={busy}
        title={`Open an app on workspace ${workspace}`}
      >
        Open app ▾
      </Button>
      {open && (
        <div className="glass-strong absolute left-0 z-30 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-[var(--radius)] p-1.5">
          <input
            ref={input}
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
            value={query}
            placeholder={`Open on workspace ${workspace}…`}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((i) => moveIndex(i, 1, flat.length));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((i) => moveIndex(i, -1, flat.length));
              } else if (event.key === "Enter") {
                event.preventDefault();
                pick(flat[active >= 0 ? active : 0]);
              } else if (event.key === "Escape") {
                event.preventDefault();
                close();
              }
            }}
            className="field-glow w-full rounded-[var(--radius-full)] border border-[var(--border-strong)] bg-[var(--surface-sunken)] px-3 py-1.5 text-[length:var(--text-sm)] outline-none placeholder:text-[var(--text-faint)]"
          />
          <ul id={listId} role="listbox" className="mt-1.5 max-h-64 overflow-y-auto">
            {flat.length === 0 && (
              <li className="px-2.5 py-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
                No app matches.
              </li>
            )}
            {groups.frequent.length > 0 && (
              <li role="presentation" className="px-2.5 pt-1 pb-0.5 text-[length:var(--text-xs)] font-semibold tracking-[0.1em] text-[var(--text-faint)] uppercase">
                Frequent
              </li>
            )}
            {groups.frequent.map((app, i) => row(app, i))}
            {groups.frequent.length > 0 && groups.rest.length > 0 && (
              <li role="presentation" className="px-2.5 pt-2 pb-0.5 text-[length:var(--text-xs)] font-semibold tracking-[0.1em] text-[var(--text-faint)] uppercase">
                All apps
              </li>
            )}
            {groups.rest.map((app, i) => row(app, groups.frequent.length + i))}
          </ul>
        </div>
      )}
    </div>
  );
}
