"use client";

/**
 * Live voice, as a side panel on the Console.
 *
 * Why a panel and not an overlay or a mode switch: the text console is
 * finished and must keep working untouched. An overlay would cover it and a
 * mode toggle would replace it. A drawer sits beside it, so you can type and
 * talk in the same sitting, and closing it leaves the console byte-identical
 * to how it was before.
 *
 * The spoken transcript is kept separate from the text conversation on
 * purpose. Server-side, a voice session runs on its own `Agent` with its own
 * history — folding both into one list would misrepresent what the model
 * actually saw in either.
 *
 * Audio never crosses the socket. The pipeline records and speaks on the
 * machine running the server, which is the machine you are sitting at, so this
 * panel is a control surface and a transcript rather than an audio client.
 */

import { useEffect, useRef } from "react";

import type { VoiceLine, VoicePhase } from "@/lib/protocol";
import { Button, Chip } from "./ui";
import { Icon } from "./icons";

interface VoiceReadiness {
  stt_backend: string;
  stt_model: string;
  tts_available: boolean;
  live_configured: boolean;
}

const PHASE: Record<VoicePhase, { label: string; colour: string; hint: string }> = {
  off: { label: "Off", colour: "var(--text-faint)", hint: "Voice is not running." },
  starting: {
    label: "Starting",
    colour: "var(--moderate)",
    hint: "Loading the speech model — this takes a few seconds the first time.",
  },
  idle: { label: "Ready", colour: "var(--accent)", hint: "Waiting for the next turn." },
  listening: { label: "Listening", colour: "var(--accent)", hint: "Speak now." },
  thinking: { label: "Thinking", colour: "var(--moderate)", hint: "Working on it." },
  speaking: { label: "Speaking", colour: "var(--accent)", hint: "Answering out loud." },
  error: { label: "Problem", colour: "var(--critical)", hint: "" },
};

const ROLE_STYLE: Record<VoiceLine["role"], { prefix: string; colour: string }> = {
  you: { prefix: "you", colour: "var(--text)" },
  brahma: { prefix: "brahma", colour: "var(--accent)" },
  tool: { prefix: "tool", colour: "var(--moderate)" },
  note: { prefix: "", colour: "var(--text-faint)" },
};

/** Animated level bars. Decorative — the real mic level is not streamed. */
function Waveform({ active, colour }: { active: boolean; colour: string }) {
  return (
    <div className="flex h-8 items-end gap-[3px]" aria-hidden>
      {[0, 1, 2, 3, 4, 5, 6].map((i) => (
        <span
          key={i}
          className="w-[3px] rounded-[var(--radius-full)]"
          style={{
            // A base height always, animated around rather than from zero.
            // Driving height purely from the keyframes meant the bars vanished
            // whenever the animation was not running — under reduced-motion,
            // or in a paused compositor.
            height: active ? "12px" : "4px",
            background: colour,
            opacity: active ? 1 : 0.35,
            animation: active
              ? `wave 900ms ease-in-out ${i * 90}ms infinite`
              : undefined,
          }}
        />
      ))}
    </div>
  );
}

export function VoicePanel({
  open,
  onClose,
  phase,
  lines,
  partial,
  mode,
  error,
  readiness,
  connected,
  onStart,
  onStop,
  onRestart,
  onClear,
}: {
  open: boolean;
  onClose: () => void;
  phase: VoicePhase;
  lines: VoiceLine[];
  partial: VoiceLine | null;
  mode: "local" | "live";
  error: string;
  readiness: VoiceReadiness | null;
  connected: boolean;
  onStart: (mode: "local" | "live") => void;
  onStop: () => void;
  onRestart: () => void;
  onClear: () => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines, partial]);

  if (!open) return null;

  const state = PHASE[phase];
  const running = phase !== "off" && phase !== "error";
  const sttReady = Boolean(readiness?.stt_backend) && Boolean(readiness?.tts_available);
  const liveReady = Boolean(readiness?.live_configured);

  return (
    <aside
      className="flex w-full shrink-0 flex-col border-l border-[var(--border)] bg-[var(--surface)] lg:w-[22rem]"
      style={{ animation: "panel-in var(--motion-base) var(--ease)" }}
      aria-label="Live voice"
    >
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[var(--border)] px-4">
        <span className="text-[length:var(--text-sm)] font-semibold">Live voice</span>
        <span
          className="ml-auto flex items-center gap-1.5 text-[length:var(--text-xs)]"
          style={{ color: state.colour }}
        >
          <span
            aria-hidden
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{
              background: state.colour,
              animation:
                phase === "listening" || phase === "starting"
                  ? "pulse 1.1s ease-in-out infinite"
                  : undefined,
            }}
          />
          {state.label}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close voice panel"
          className="ml-1 text-[var(--text-faint)] hover:text-[var(--text)]"
        >
          <Icon name="close" size={15} />
        </button>
      </header>

      {/* -- state -------------------------------------------------------- */}
      <div className="shrink-0 border-b border-[var(--border)] px-4 py-4">
        <div className="flex items-center gap-3">
          <Waveform
            active={phase === "listening" || phase === "speaking"}
            colour={state.colour}
          />
          <p className="min-w-0 flex-1 text-[length:var(--text-xs)] text-[var(--text-muted)]">
            {error || state.hint}
          </p>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          {running ? (
            <>
              <Button variant="danger" onClick={onStop}>
                Stop listening
              </Button>
              {/* Recovery without doing stop-then-start by hand, which was the
                  only way out of a stalled session. */}
              <Button onClick={onRestart} title="Stop and start again">
                Restart
              </Button>
            </>
          ) : (
            <>
              {/* Live first, and primary, because it is the default the
                  toggle picks. On-device stays available as the free path. */}
              <Button
                variant={liveReady ? "primary" : "default"}
                disabled={!connected || !liveReady}
                onClick={() => onStart("live")}
                title={
                  liveReady
                    ? "Gemini native audio — ~7s to first word, metered"
                    : "Needs a Gemini API key"
                }
              >
                Start live
              </Button>
              <Button
                variant={liveReady ? "default" : "primary"}
                disabled={!connected || !sttReady}
                onClick={() => onStart("local")}
                title={
                  sttReady
                    ? "Runs entirely on this machine — free, ~19s to first word"
                    : "Speech recognition or playback is unavailable"
                }
              >
                Start on-device
              </Button>
            </>
          )}
          {lines.length > 0 && !running && (
            <Button variant="ghost" onClick={onClear}>
              Clear
            </Button>
          )}
        </div>

        {!sttReady && readiness && (
          <p
            className="mt-2 text-[length:var(--text-xs)]"
            style={{ color: "var(--moderate)" }}
          >
            {!readiness.stt_backend && "No speech recognition available. "}
            {!readiness.tts_available && "No playback available — install mpv."}
          </p>
        )}
      </div>

      {/* -- transcript --------------------------------------------------- */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {lines.length === 0 && !partial ? (
          <div className="pt-6 text-center">
            <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
              Nothing spoken yet.
            </p>
            <p className="mx-auto mt-2 max-w-[15rem] text-[length:var(--text-xs)] text-[var(--text-faint)]">
              Voice uses this machine&apos;s microphone and speakers. Destructive
              actions still stop and ask before running.
            </p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {[...lines, ...(partial ? [partial] : [])].map((line) => {
              const style = ROLE_STYLE[line.role];
              return (
                <div key={line.id} className="text-[length:var(--text-sm)]">
                  {style.prefix && (
                    <span
                      className="mr-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                      style={{ color: style.colour }}
                    >
                      {style.prefix}
                    </span>
                  )}
                  <span
                    className="whitespace-pre-wrap"
                    style={{
                      opacity: line.id < 0 ? 0.65 : 1,
                      color:
                        line.role === "note" ? "var(--text-faint)" : "var(--text)",
                      fontStyle: line.role === "note" ? "italic" : undefined,
                      fontSize:
                        line.role === "note" || line.role === "tool"
                          ? "var(--text-xs)"
                          : undefined,
                      fontFamily:
                        line.role === "tool" ? "var(--font-mono)" : undefined,
                    }}
                  >
                    {line.text}
                  </span>
                </div>
              );
            })}
            <div ref={endRef} />
          </div>
        )}
      </div>

      {/* -- footer ------------------------------------------------------- */}
      <footer className="shrink-0 border-t border-[var(--border)] px-4 py-3">
        <div className="flex items-center gap-2">
          <Chip active={mode === "local"} tone="var(--accent)">
            {mode === "live" ? "Gemini Live" : "On-device"}
          </Chip>
          <span className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
            {mode === "live"
              ? "~7s to first word · metered"
              : `~19s to first word · free${
                  readiness?.stt_model ? ` · ${readiness.stt_model}` : ""
                }`}
          </span>
        </div>
        <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          Say “stop listening” or press Stop to end the session.
        </p>
      </footer>
    </aside>
  );
}
