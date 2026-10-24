// Pure logic for the terminal command popup (no React).
//
// Commands go through `run_command`, whose result is prose:
//   $ cmd \n [timed out after Ns] \n <stdout> \n [stderr]\n<stderr> \n [exit N]
// The parser is tolerant: anything that does not look like that (a refusal, a
// missing directory) comes back as a plain note with no exit code.

export interface OutputLine {
  stream: "stdout" | "stderr" | "note";
  text: string;
}

export interface ParsedResult {
  lines: OutputLine[];
  exit: number | null;
  timedOut: boolean;
}

export const RUN_TIMEOUT_SECONDS = 30;

export function parseResult(raw: string, command: string): ParsedResult {
  let text = raw.replace(/\r\n/g, "\n");
  const header = `$ ${command.trim()}`;
  const looksLikeRun = text.startsWith(header + "\n") || text === header;
  if (!looksLikeRun) {
    return { lines: toLines(text, "note"), exit: null, timedOut: false };
  }
  text = text.slice(header.length).replace(/^\n/, "");

  let exit: number | null = null;
  const tail = /\n?\[exit (-?\d+)\]\s*$/.exec(text);
  if (tail) {
    exit = Number(tail[1]);
    text = text.slice(0, tail.index);
  }
  let timedOut = false;
  const to = /^\[timed out after \d+s\]\n?/.exec(text);
  if (to) {
    timedOut = true;
    text = text.slice(to[0].length);
  }
  if (text === "(no output)") text = "";

  // stderr always follows stdout in the skill's format. A stdout that itself
  // contains "[stderr]" on its own line would be split wrongly; accepted, the
  // alternative is a structured endpoint that bypasses the confirmed path.
  const marker = text.indexOf("[stderr]\n");
  const out = marker < 0 ? text : text.slice(0, marker);
  const err = marker < 0 ? "" : text.slice(marker + "[stderr]\n".length);
  const lines = [...toLines(out, "stdout"), ...toLines(err, "stderr")];
  if (timedOut) lines.push({ stream: "note", text: "timed out" });
  return { lines, exit, timedOut };
}

function toLines(text: string, stream: OutputLine["stream"]): OutputLine[] {
  if (!text) return [];
  return text.replace(/\n$/, "").split("\n").map((t) => ({ stream, text: t }));
}

export const COLLAPSE_LINES = 200;

// Long output is cut with a "show more" so one `find /` cannot freeze the page.
export function capLines<T>(lines: readonly T[], expanded: boolean): { shown: T[]; hidden: number } {
  if (expanded || lines.length <= COLLAPSE_LINES) return { shown: [...lines], hidden: 0 };
  return { shown: lines.slice(0, COLLAPSE_LINES), hidden: lines.length - COLLAPSE_LINES };
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

// Shell-style history: Up walks to older commands, Down back to the draft.
export interface History {
  items: string[];
  cursor: number; // items.length = the live draft
  draft: string;
}

export const HISTORY_MAX = 100;

export function newHistory(): History {
  return { items: [], cursor: 0, draft: "" };
}

export function pushHistory(h: History, command: string): History {
  const c = command.trim();
  if (!c) return { ...h, cursor: h.items.length, draft: "" };
  const items = h.items[h.items.length - 1] === c ? h.items : [...h.items, c];
  const trimmed = items.slice(-HISTORY_MAX);
  return { items: trimmed, cursor: trimmed.length, draft: "" };
}

export function historyStep(
  h: History,
  dir: "up" | "down",
  current: string,
): { history: History; value: string } {
  const atDraft = h.cursor >= h.items.length;
  const draft = atDraft ? current : h.draft;
  if (dir === "up") {
    if (h.items.length === 0 || h.cursor === 0) return { history: { ...h, draft }, value: current };
    const cursor = h.cursor - 1;
    return { history: { ...h, cursor, draft }, value: h.items[cursor] };
  }
  if (atDraft) return { history: h, value: current };
  const cursor = h.cursor + 1;
  return {
    history: { ...h, cursor, draft },
    value: cursor >= h.items.length ? draft : h.items[cursor],
  };
}
