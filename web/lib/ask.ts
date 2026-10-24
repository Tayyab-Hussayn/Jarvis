/**
 * `/?ask=<text>` opens the chat with the composer pre-filled, so the Android
 * app can share text or a link into Kravein. The text is only ever placed in
 * the composer (a plain <textarea> value, never rendered as HTML/markdown) and
 * never sent: the owner presses Send. Whatever a link carries is untrusted
 * input, so it is capped and stripped of control characters.
 */

export const ASK_MAX_CHARS = 4000;

// Control characters other than tab/newline: they can't be typed into the
// composer and only make a shared payload confusing.
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;

/** Clean a raw `ask` value; returns "" when nothing usable is left. */
export function cleanAsk(raw: string | null): string {
  if (!raw) return "";
  const text = raw.replace(/\r\n?/g, "\n").replace(CONTROL, "");
  if (!text.trim()) return "";
  // Count code points, not UTF-16 units, so the cap never splits an emoji.
  const points = Array.from(text);
  return points.length > ASK_MAX_CHARS
    ? points.slice(0, ASK_MAX_CHARS).join("")
    : text;
}

/**
 * Take `ask` out of `url` (mutating it, like `token`). Returns the cleaned
 * text, or null when the URL had no `ask` parameter at all.
 */
export function extractAsk(url: URL): string | null {
  if (!url.searchParams.has("ask")) return null;
  const text = cleanAsk(url.searchParams.get("ask"));
  url.searchParams.delete("ask");
  return text;
}
