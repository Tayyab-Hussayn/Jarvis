// Pure logic for workspaces and the per-workspace app launcher (no React).

// Compositors list workspaces in creation order ("2, 1, 4, 3"). Numbers go
// first in numeric order, named ones ("special:magic", "web") after, A-Z.
export function sortWorkspaces(list: readonly string[]): string[] {
  const isNum = (s: string) => /^-?\d+$/.test(s);
  return [...list].sort((a, b) => {
    const an = isNum(a);
    const bn = isNum(b);
    if (an && bn) return Number(a) - Number(b);
    if (an) return -1;
    if (bn) return 1;
    return a.localeCompare(b);
  });
}

export interface LauncherApp {
  name: string;
  comment?: string;
  launches?: number;
}

export interface LauncherGroups {
  frequent: LauncherApp[];
  rest: LauncherApp[];
}

// Frequent apps (server-ranked, in the server's order) come first under their
// own heading, but only while the search box is empty: a search is a flat
// ranked list, so a typed name never hides behind a heading.
export function groupApps(
  apps: readonly LauncherApp[],
  frequent: readonly string[],
  query: string,
): LauncherGroups {
  const q = query.trim().toLowerCase();
  if (q) {
    const hits = apps.filter(
      (a) =>
        a.name.toLowerCase().includes(q) || (a.comment ?? "").toLowerCase().includes(q),
    );
    // Name matches before comment-only matches; prefix before substring.
    const score = (a: LauncherApp) => {
      const n = a.name.toLowerCase();
      return n.startsWith(q) ? 0 : n.includes(q) ? 1 : 2;
    };
    hits.sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
    return { frequent: [], rest: hits };
  }
  const byName = new Map(apps.map((a) => [a.name, a]));
  const top = frequent.map((n) => byName.get(n)).filter((a): a is LauncherApp => !!a);
  const topNames = new Set(top.map((a) => a.name));
  const rest = apps
    .filter((a) => !topNames.has(a.name))
    .sort((a, b) => a.name.localeCompare(b.name));
  return { frequent: top, rest };
}

// Keyboard order of the listbox: the same sequence the user sees.
export function flatten(groups: LauncherGroups): LauncherApp[] {
  return [...groups.frequent, ...groups.rest];
}

// Arrow keys wrap; -1 means "nothing highlighted yet".
export function moveIndex(index: number, delta: 1 | -1, length: number): number {
  if (length <= 0) return -1;
  if (index < 0) return delta === 1 ? 0 : length - 1;
  return (index + delta + length) % length;
}
