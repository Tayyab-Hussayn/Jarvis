"use client";

/**
 * The Text / Voice switch in the Console hero.
 *
 * The important part is not the pixels, it is where the state comes from.
 * This control renders `voicePhase`, which is the phase the *server* last
 * reported — it never keeps its own idea of which mode is active. That matters
 * because the same session can be started and stopped from the side panel, and
 * two independent flags would eventually disagree. Here they cannot: there is
 * only one flag, and it is the server's.
 *
 * The consequence is that switching to Voice does not light up instantly. It
 * lights up when the microphone is actually open. A toggle that flipped
 * immediately and then failed to start would be lying, and this is a control
 * surface for something that can hear you.
 */

import type { VoicePhase } from "@/lib/protocol";
import { isVoiceLive } from "@/lib/protocol";
import { Icon } from "./icons";

export type ChatMode = "text" | "voice";

const LIVE_LABEL: Partial<Record<VoicePhase, string>> = {
  starting: "Starting…",
  idle: "Ready",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

function Half({
  active,
  disabled,
  icon,
  label,
  hint,
  onClick,
  title,
}: {
  active: boolean;
  disabled: boolean;
  icon: "console" | "mic";
  label: string;
  hint: string;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      role="radio"
      aria-checked={active}
      className="relative z-10 flex flex-1 items-center justify-center gap-3 rounded-[var(--radius-lg)] px-5 py-3.5 disabled:cursor-not-allowed disabled:opacity-40"
      style={{
        transition: "color var(--motion-base) var(--ease)",
        color: active ? "var(--accent-contrast)" : "var(--text-muted)",
      }}
    >
      <Icon name={icon} size={20} className="shrink-0" />
      <span className="text-left">
        <span className="block text-[length:var(--text-base)] font-semibold leading-tight">
          {label}
        </span>
        <span
          className="block text-[length:var(--text-xs)] leading-tight"
          style={{ opacity: active ? 0.75 : 1 }}
        >
          {hint}
        </span>
      </span>
    </button>
  );
}

export function ModeToggle({
  phase,
  stopping,
  offline,
  voiceReady,
  unavailableReason,
  onSelectText,
  onSelectVoice,
}: {
  phase: VoicePhase;
  stopping: boolean;
  offline: boolean;
  voiceReady: boolean;
  unavailableReason?: string;
  onSelectText: () => void;
  onSelectVoice: () => void;
}) {
  const live = isVoiceLive(phase);
  const mode: ChatMode = live ? "voice" : "text";
  // Mid-transition the session is neither fully on nor off; taking another
  // command would race the one already in flight.
  const busy = phase === "starting" || stopping;

  const voiceHint = stopping
    ? "Stopping…"
    : live
      ? (LIVE_LABEL[phase] ?? "Live")
      : voiceReady
        ? "Speak to Kravein"
        : "Unavailable";

  return (
    <div className="mx-auto w-full max-w-md">
      <div
        role="radiogroup"
        aria-label="Chat mode"
        className="relative flex glass rounded-[var(--radius-xl)] p-1.5"
        style={{ boxShadow: "var(--shadow-card)" }}
      >
        {/* The sliding indicator. One element that moves, rather than two that
            change colour, so the transition reads as a single control. */}
        <span
          aria-hidden
          className="absolute inset-y-1.5 rounded-[var(--radius-lg)]"
          style={{
            left: mode === "text" ? "0.375rem" : "50%",
            width: "calc(50% - 0.375rem)",
            background: "var(--accent)",
            transition: "left var(--motion-base) var(--ease)",
            opacity: busy ? 0.55 : 1,
          }}
        />

        <Half
          active={mode === "text"}
          disabled={busy}
          icon="console"
          label="Text"
          hint="Type a request"
          onClick={onSelectText}
          title={live ? "Stop the voice session" : undefined}
        />
        <Half
          active={mode === "voice"}
          disabled={busy || offline || (!live && !voiceReady)}
          icon="mic"
          label="Voice"
          hint={voiceHint}
          onClick={onSelectVoice}
          title={
            !voiceReady && !live
              ? unavailableReason || "Voice is unavailable on this machine"
              : undefined
          }
        />
      </div>

      <p className="mt-2.5 h-4 text-center text-[length:var(--text-xs)] text-[var(--text-faint)]">
        {offline
          ? "Not connected."
          : busy
            ? "One moment…"
            : live
              ? "Voice is live — you can still type below."
              : voiceReady
                ? "Or switch to voice and just talk."
                : unavailableReason || ""}
      </p>
    </div>
  );
}
