// WCAG AA check for the token pairs that carry text. Dark only (v2 ships one
// theme). Reads app/tokens.css so the numbers cannot drift from the palette;
// `--a: var(--b)` aliases are resolved.
//   node scripts/check_contrast.mjs        (exit 1 on any failure)
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/tokens.css", import.meta.url), "utf8");
const root = css.slice(css.indexOf(":root {"), css.indexOf("@theme"));

const raw = {};
for (const m of root.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) raw[m[1]] = m[2].trim();
function resolve(name, depth = 0) {
  const value = raw[name];
  if (!value || depth > 5) return undefined;
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return value;
  const alias = value.match(/^var\(--([a-z0-9-]+)\)$/);
  return alias ? resolve(alias[1], depth + 1) : undefined;
}

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

// [foreground, background, minimum]. 4.5 for text; non-text agent-state
// strokes need 3.
const PAIRS = [];
for (const fg of [
  "text", "text-strong", "text-muted", "text-faint", "accent", "accent-2",
  "moderate", "destructive", "critical", "coral", "state-failed", "state-working-text",
]) {
  for (const bg of ["bg", "surface", "surface-raised"]) PAIRS.push([fg, bg, 4.5]);
}
for (const fg of ["laser", "state-idle", "state-waiting", "state-done"]) {
  for (const bg of ["bg", "surface-raised"]) PAIRS.push([fg, bg, 3]);
}
PAIRS.push(
  ["accent-contrast", "accent", 4.5],
  ["gradient-accent-contrast", "laser", 4.5],
  ["critical-contrast", "critical", 4.5],
  ["panel-text", "panel", 4.5],
  ["panel-muted", "panel", 4.5],
  ["panel-faint", "panel", 4.5],
);

let failed = 0;
for (const [fg, bg, min] of PAIRS) {
  const a = resolve(fg);
  const b = resolve(bg);
  if (!a || !b) {
    failed += 1;
    console.log(`FAIL token missing or not a hex: ${fg}=${a} ${bg}=${b}`);
    continue;
  }
  const r = ratio(a, b);
  if (r < min) {
    failed += 1;
    console.log(`FAIL ${fg} ${a} on ${bg} ${b} = ${r.toFixed(2)} (< ${min})`);
  }
}
console.log(failed ? `${failed} contrast failure(s)` : `contrast ok (AA, ${PAIRS.length} pairs)`);
process.exit(failed ? 1 : 0);
