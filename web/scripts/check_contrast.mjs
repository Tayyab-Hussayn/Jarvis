// WCAG AA check for the token pairs that carry text, in both themes.
// Reads app/tokens.css so the numbers cannot drift from the palette.
//   node scripts/check_contrast.mjs        (exit 1 on any failure)
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../app/tokens.css", import.meta.url), "utf8");
const dark = css.slice(css.indexOf(":root {"), css.indexOf("@theme"));
const light = css.slice(css.indexOf("prefers-color-scheme: light"));

function vars(block) {
  const out = {};
  for (const m of block.matchAll(/--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]] = m[2];
  return out;
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

// [foreground, background, minimum]. 4.5 for text; the accent as a non-text
// fill/line only needs 3.
const PAIRS = [];
for (const fg of ["text", "text-muted", "text-faint", "accent", "accent-2", "moderate", "destructive", "critical"]) {
  for (const bg of ["bg", "surface", "surface-raised"]) PAIRS.push([fg, bg, 4.5]);
}
PAIRS.push(
  ["accent-contrast", "accent", 4.5],
  ["critical-contrast", "critical", 4.5],
  ["panel-text", "panel", 4.5],
  ["panel-muted", "panel", 4.5],
  ["panel-faint", "panel", 4.5],
);

let failed = 0;
for (const [name, block] of [["dark", dark], ["light", light]]) {
  const t = { ...vars(dark), ...(name === "light" ? vars(block) : {}) };
  for (const [fg, bg, min] of PAIRS) {
    if (!t[fg] || !t[bg]) continue;
    const r = ratio(t[fg], t[bg]);
    if (r < min) {
      failed += 1;
      console.log(`FAIL ${name}: ${fg} ${t[fg]} on ${bg} ${t[bg]} = ${r.toFixed(2)} (< ${min})`);
    }
  }
}
console.log(failed ? `${failed} contrast failure(s)` : "contrast ok (AA, both themes)");
process.exit(failed ? 1 : 0);
