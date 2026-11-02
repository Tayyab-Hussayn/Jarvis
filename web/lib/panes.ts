/**
 * Dock modules and the `#name` deep link.
 *
 * Pure, so the rules are testable without a DOM: which hashes open which pane,
 * which legacy hashes still work (the old rail used `#/name`), and how an agent
 * detail pane is addressed (`#agent/<name>`).
 */

export type ModuleId =
  | "windows"
  | "apps"
  | "files"
  | "screen"
  | "system"
  | "control"
  | "habits"
  | "schedule"
  | "phones"
  | "brain"
  | "activity"
  | "settings"
  | "proposals";

export interface ModuleInfo {
  id: ModuleId;
  label: string;
  /** Icon name from components/icons.tsx. */
  icon: string;
  /** Extra words the palette matches on, and extra hashes that open it. */
  aliases: string[];
  /** Not built yet: the dock shows the slot but it opens nothing. */
  soon?: boolean;
}

export const MODULES: ModuleInfo[] = [
  { id: "windows", label: "Windows", icon: "windows", aliases: ["workspaces"] },
  { id: "apps", label: "Apps", icon: "apps", aliases: ["launcher"] },
  { id: "files", label: "Files", icon: "files", aliases: [] },
  { id: "screen", label: "Screen", icon: "screen", aliases: ["screenshot"] },
  { id: "system", label: "System", icon: "system", aliases: ["vitals"] },
  { id: "control", label: "Control", icon: "control", aliases: ["volume", "brightness"] },
  { id: "habits", label: "Routines", icon: "habits", aliases: ["routines", "habit"] },
  { id: "schedule", label: "Jobs", icon: "schedule", aliases: ["jobs", "scheduled"] },
  { id: "phones", label: "Phones", icon: "phones", aliases: ["android", "pair"] },
  { id: "proposals", label: "Proposals", icon: "proposals", aliases: ["suggestions"] },
  { id: "brain", label: "Memory", icon: "brain", aliases: ["memory", "brain"] },
  { id: "activity", label: "Audit", icon: "activity", aliases: ["audit", "activity", "log"] },
  { id: "settings", label: "Settings", icon: "settings", aliases: ["policy", "confirmation"] },
];

const BY_ID = new Map<string, ModuleInfo>(MODULES.map((m) => [m.id, m]));
const BY_ALIAS = new Map<string, ModuleInfo>(
  MODULES.flatMap((m) => m.aliases.map((a) => [a, m] as const)),
);

export type Route =
  | { kind: "home" }
  | { kind: "module"; id: ModuleId }
  | { kind: "agent"; name: string };

/** Agent names come from SKILL.md folders; anything else is not a route. */
const AGENT_NAME = /^[a-z][a-z0-9_-]{0,31}$/;

/**
 * `#settings`, `#/settings` (old rail links), `#agent/research`. Anything
 * unknown, empty, or a module that is not built yet is home: a stale bookmark
 * must never leave the page blank.
 */
export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "").trim().toLowerCase();
  if (!raw || raw === "console") return { kind: "home" };

  if (raw.startsWith("agent/")) {
    const name = raw.slice("agent/".length);
    return AGENT_NAME.test(name) ? { kind: "agent", name } : { kind: "home" };
  }

  const found = BY_ID.get(raw) ?? BY_ALIAS.get(raw);
  if (!found || found.soon) return { kind: "home" };
  return { kind: "module", id: found.id };
}

export function routeHash(route: Route): string {
  switch (route.kind) {
    case "home":
      return "";
    case "module":
      return `#${route.id}`;
    case "agent":
      return `#agent/${route.name}`;
  }
}

export function sameRoute(a: Route, b: Route): boolean {
  return routeHash(a) === routeHash(b);
}

export function moduleLabel(id: ModuleId): string {
  return BY_ID.get(id)?.label ?? id;
}

/**
 * Does this key press close the open pane? Esc does, unless something that
 * owns Esc more specifically already took it (the palette, an open select),
 * which marks the event `defaultPrevented`.
 */
export function escapeClosesPane(event: {
  key: string;
  defaultPrevented: boolean;
  isComposing?: boolean;
}): boolean {
  return event.key === "Escape" && !event.defaultPrevented && !event.isComposing;
}

/** Where the pane returns focus: the control that opened it, if still there. */
export function focusReturnTarget<T extends { isConnected: boolean }>(
  opener: T | null,
  fallback: T | null,
): T | null {
  return opener && opener.isConnected ? opener : fallback;
}

/** Left/right (or up/down) roving focus over the enabled dock items. */
export function roveIndex(
  current: number,
  key: string,
  count: number,
): number | null {
  if (count <= 0) return null;
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (current + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
