// Pure logic for the Windows view (no React), so it is testable with node --test.

export interface WindowTarget {
  id: string;
}

// Focus/Close must name the exact window. Matching by app name hit an arbitrary
// one of several same-app windows (three `foot` terminals), so the id is the
// only target we send; the server refuses a stale id instead of name-matching.
export function windowArgs(win: WindowTarget): { id: string } {
  return { id: win.id };
}

// "~" for the home directory, and a long path keeps its last two segments so
// several terminals of one app stay distinguishable on a narrow card. The full
// path goes in the tooltip.
export function shortCwd(cwd: string | null | undefined, home?: string): string {
  if (!cwd) return "";
  let path = cwd;
  if (home && home !== "/" && (path === home || path.startsWith(home + "/"))) {
    path = "~" + path.slice(home.length);
  }
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 3) return path;
  const lead = path.startsWith("~") ? "~" : "";
  return `${lead}/…/${parts.slice(-2).join("/")}`;
}

// The server cannot know the browser's home, but every home directory it sends
// is /home/<user> or /root, so derive it from the path itself.
export function homeOf(cwd: string | null | undefined): string | undefined {
  if (!cwd) return undefined;
  const m = /^(\/home\/[^/]+|\/root)(?=\/|$)/.exec(cwd);
  return m ? m[1] : undefined;
}
