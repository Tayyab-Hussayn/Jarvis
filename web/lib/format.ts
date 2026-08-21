/** Small display helpers. No state, no side effects. */

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function bytes(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) return "—";
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${UNITS[unit]}`;
}

export function percent(value: number | undefined): string {
  return value === undefined ? "—" : `${value.toFixed(0)}%`;
}

export function duration(seconds: number | undefined): string {
  if (seconds === undefined) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export function clockTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? iso
    : parsed.toLocaleTimeString(undefined, { hour12: false });
}

/** Compact one-line preview of tool arguments. */
export function previewArgs(args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .slice(0, 3)
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}=${text && text.length > 28 ? `${text.slice(0, 28)}…` : text}`;
    });
  const extra = Object.keys(args).length - parts.length;
  return parts.join(", ") + (extra > 0 ? `, +${extra}` : "");
}

export function classNames(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}
