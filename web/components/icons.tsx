"use client";

/**
 * The icon set.
 *
 * Replaces the Unicode geometric glyphs the rail used to draw with (`▤ ▦ ▢ ◰`).
 * Those came from whatever installed font happened to cover each codepoint, so
 * they arrived at inconsistent optical sizes and stroke weights, and four of
 * the nine were near-identical squares.
 *
 * These are drawn on one 24-unit grid at a single 1.6 stroke weight, so they
 * sit together as a family. Each shape is chosen to be unmistakable at 20px —
 * distinctness matters more here than literal representation, because the rail
 * is scanned, not read.
 */

export type IconName =
  | "console"
  | "system"
  | "windows"
  | "files"
  | "apps"
  | "control"
  | "screen"
  | "activity"
  | "brain"
  | "settings"
  | "search"
  | "close"
  | "refresh"
  | "trash"
  | "chevron"
  | "mic"
  | "schedule"
  | "habits";

const PATHS: Record<IconName, React.ReactNode> = {
  // A checklist with a rising line through it: sequence plus progress, which
  // is what a routine is.
  habits: (
    <>
      <path d="M4 7h6M4 12h6M4 17h6" />
      <path d="m13 8 2.5 2.5L20 6" />
      <path d="M13 14.5 15.5 17 20 12.5" />
    </>
  ),
  // Clock with a hand — scheduled work.
  schedule: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l3 2" />
    </>
  ),
  // Chat bubble with a cursor — the agent conversation.
  console: (
    <>
      <path d="M20 14a2 2 0 0 1-2 2H8l-4 3V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z" />
      <path d="M8.5 10h5" />
    </>
  ),
  // Gauge — machine vitals.
  system: (
    <>
      <path d="M12 20a8 8 0 1 1 8-8" />
      <path d="M12 12l4-3" />
      <path d="M20 12h1M3 12h1M12 4V3" />
    </>
  ),
  // Overlapping frames — windows.
  windows: (
    <>
      <rect x="3" y="5" width="12" height="10" rx="2" />
      <path d="M9 19h10a2 2 0 0 0 2-2V9" />
    </>
  ),
  // Folder.
  files: <path d="M4 7a2 2 0 0 1 2-2h3l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" />,
  // Four tiles — the app grid.
  apps: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.6" />
      <rect x="13" y="4" width="7" height="7" rx="1.6" />
      <rect x="4" y="13" width="7" height="7" rx="1.6" />
      <rect x="13" y="13" width="7" height="7" rx="1.6" />
    </>
  ),
  // Sliders — machine controls.
  control: (
    <>
      <path d="M5 6h14M5 12h14M5 18h14" />
      <circle cx="9" cy="6" r="2" />
      <circle cx="15" cy="12" r="2" />
      <circle cx="8" cy="18" r="2" />
    </>
  ),
  // Monitor — the display and what is on it.
  screen: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M9 20h6M12 16v4" />
    </>
  ),
  // Pulse line — the audit trail.
  activity: <path d="M3 12h4l2.5-6 4 12 2.5-6h5" />,
  // Concentric nodes — the model.
  brain: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 5V3M12 21v-2M5 12H3M21 12h-2M7.4 7.4 6 6M18 18l-1.4-1.4M16.6 7.4 18 6M6 18l1.4-1.4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11a2 2 0 1 1 0 4 1.7 1.7 0 0 0-1.6 1z" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.5-4.5" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.5-5.8" />
      <path d="M20 4v5h-5" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" />
    </>
  ),
  chevron: <path d="m9 6 6 6-6 6" />,
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3M9 21h6" />
    </>
  ),
};

export function Icon({
  name,
  size = 20,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
