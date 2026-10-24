"use client";

/**
 * One chat window, two ways of talking to it.
 *
 * Voice used to render into a separate side panel with its own plain-text
 * transcript — `you:` / `tool:` / `kravein:` lines that looked nothing like the
 * typed conversation sitting next to them. There is now a single rendering
 * path: spoken turns are built into the same `messages` state as typed ones
 * (see `useKravein`) and go through `TurnGraph` like everything else. Nothing
 * here knows or cares which mode produced a turn.
 *
 * What genuinely differs between the modes is control, not content — a spoken
 * session has to be started, stopped and switched between the on-device and
 * Live pipelines. Those controls live in the composer, because the hero that
 * used to hold them disappears the moment the first turn arrives, and losing
 * the Stop button mid-session is not acceptable for something holding a
 * microphone open.
 */

import { useEffect, useRef, useState } from "react";

import type { Message, VoicePhase } from "@/lib/protocol";
import type { Connection } from "@/lib/useKravein";
import { isVoiceLive } from "@/lib/protocol";
import { Icon } from "./icons";
import { ModeToggle } from "./ModeToggle";
import { TurnGraph } from "./TurnGraph";
import { Button } from "./ui";

// Four short prompts in an even 2x2 grid. The previous set were full
// sentences of uneven length and wrapped 3 + 1, leaving a ragged final row.
const SUGGESTIONS = [
  { label: "What's using my disk?", prompt: "What is using the most disk space in my home directory?" },
  { label: "What's eating CPU?", prompt: "Which processes are eating CPU right now?" },
  { label: "List open windows", prompt: "List my open windows" },
  { label: "Any package updates?", prompt: "Are there any package updates?" },
];

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

/** Animated level bars. Decorative — the real mic level is not streamed. */
function Waveform({ active, colour }: { active: boolean; colour: string }) {
  return (
    <div className="flex h-5 items-end gap-[3px]" aria-hidden>
      {[0, 1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className="w-[3px] rounded-[var(--radius-full)]"
          style={{
            // A base height always, animated around rather than from zero.
            // Driving height purely from the keyframes meant the bars vanished
            // whenever the animation was not running — under reduced-motion,
            // or in a paused compositor.
            height: active ? "10px" : "4px",
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

/**
 * Pairs each assistant turn with the request that produced it, so the graph
 * can render the request as its root node. Turn ids are `t<n>` for typed and
 * `s<n>` for spoken; either way the request is `<id>-user`.
 */
function requestFor(messages: Message[], turnId: string): string {
  return messages.find((m) => m.id === `${turnId}-user`)?.text ?? "";
}

export function Console({
  messages,
  busy,
  connection,
  skills,
  onAsk,
  onRespond,
  onReset,
  onCancel,
  cancelling,
  voicePhase,
  voiceStopping,
  voiceMode,
  voiceError,
  voiceNote,
  voiceReady,
  canSwitchPipeline,
  voiceUnavailableReason,
  onSelectText,
  onSelectVoice,
  onRestartVoice,
  onDismissVoiceError,
}: {
  messages: Message[];
  busy: boolean;
  connection: Connection;
  skills?: number;
  onAsk: (text: string) => void;
  onRespond: (confirmId: string, approve: boolean) => void;
  onReset: () => void;
  onCancel: () => void;
  cancelling: boolean;
  voicePhase: VoicePhase;
  voiceStopping: boolean;
  voiceMode: "local" | "live";
  voiceError: string;
  voiceNote: string;
  voiceReady: boolean;
  canSwitchPipeline: boolean;
  voiceUnavailableReason?: string;
  onSelectText: () => void;
  onSelectVoice: () => void;
  onRestartVoice: (mode: "local" | "live") => void;
  onDismissVoiceError: () => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Follow the stream, but only when already at the bottom — yanking the view
  // down while the user reads an earlier tool result is the classic annoyance.
  // Spoken turns land in the same list, so they inherit this for free.
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (distance < 180) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = () => {
    if (!draft.trim() || busy) return;
    onAsk(draft);
    setDraft("");
  };

  const offline = connection !== "open";
  const turns = messages.filter((m) => m.role === "assistant");
  const voiceLive = isVoiceLive(voicePhase);
  // A session that fails and then stops arrives as `error` followed by
  // `stopped`, which left the strip reading "Off" next to an error message —
  // two contradictory claims in one row. The failure outlives the phase, so it
  // is what gets reported.
  const failed = Boolean(voiceError) && !voiceLive;
  const state = failed ? PHASE.error : PHASE[voicePhase];

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto pr-1">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="w-full max-w-md text-center">
              <span
                aria-hidden
                className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full border text-[length:var(--text-xl)] font-semibold"
                style={{
                  borderColor: "var(--accent-line)",
                  background: "var(--accent-dim)",
                  color: "var(--accent)",
                  animation: "halo 3.2s ease-in-out infinite",
                }}
              >
                K
              </span>
              <h2 className="text-gradient text-[length:var(--text-2xl)] font-semibold tracking-[-0.03em]">
                What should Kravein do?
              </h2>
              <p className="mx-auto mt-2 max-w-sm text-[length:var(--text-sm)] text-[var(--text-muted)]">
                It has real control over this machine.
                {typeof skills === "number" ? ` ${skills} skills available.` : ""}{" "}
                Destructive actions stop and ask you first.
              </p>

              <div className="mt-6">
                <ModeToggle
                  phase={voicePhase}
                  stopping={voiceStopping}
                  offline={offline}
                  voiceReady={voiceReady}
                  unavailableReason={voiceUnavailableReason}
                  onSelectText={onSelectText}
                  onSelectVoice={onSelectVoice}
                />
              </div>

              {/* Text mode keeps exactly the suggestion grid it had. Voice mode
                  shows session state instead — a list of things to click is
                  the wrong prompt when the point is to talk, and the spoken
                  exchange itself now appears in the conversation above rather
                  than in a preview here. */}
              {voiceLive ? (
                <p className="mt-5 text-[length:var(--text-sm)] text-[var(--text-muted)]">
                  {voiceError || voiceNote || state.hint}
                </p>
              ) : (
                <div className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      disabled={offline}
                      onClick={() => onAsk(item.prompt)}
                      className="glass rounded-[var(--radius)] px-3.5 py-3 text-left text-[length:var(--text-sm)] text-[var(--text)] hover:border-[var(--accent-line)] hover:bg-[var(--accent-dim)] hover:text-[var(--accent)] hover:shadow-[var(--glow-soft)] disabled:opacity-40"
                      style={{ transition: "all var(--motion-fast) var(--ease)" }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-8 pb-4">
            {turns.map((message) => (
              <TurnGraph
                key={message.id}
                message={message}
                request={requestFor(messages, message.id)}
                onRespond={onRespond}
              />
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="pt-3">
        {/* The session control strip. Present for as long as the microphone is
            open, so Stop is never more than one click away — the hero that
            used to carry it is gone once the first turn lands. */}
        {(voiceLive || voiceError) && (
          <div
            className="glass mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius)] px-3 py-2"
            style={{ animation: "node-in var(--motion-base) var(--ease)" }}
          >
            <Waveform
              active={voicePhase === "listening" || voicePhase === "speaking"}
              colour={state.colour}
            />
            <span
              className="text-[length:var(--text-sm)] font-medium"
              style={{ color: state.colour }}
            >
              {voiceStopping ? "Stopping…" : state.label}
            </span>
            <span
              className="min-w-0 flex-1 text-[length:var(--text-xs)]"
              style={{ color: failed ? "var(--critical)" : "var(--text-faint)" }}
            >
              {/* Not truncated when it failed: a one-line explanation of why
                  voice stopped is the whole value of the row. */}
              {voiceError || voiceNote || state.hint}
            </span>

            {failed && (
              <button
                type="button"
                onClick={onDismissVoiceError}
                aria-label="Dismiss"
                className="shrink-0 rounded-[var(--radius-sm)] px-1.5 text-[var(--text-faint)] hover:text-[var(--text)]"
              >
                <Icon name="close" size={14} />
              </button>
            )}

            {voiceLive && (
              <>
                {/* Switching pipeline is a restart, not a setting — the
                    microphone has to be handed over. `restartVoice` waits for
                    the old session to confirm it released the device first. */}
                {canSwitchPipeline && (
                  <Button
                    variant="ghost"
                    onClick={() =>
                      onRestartVoice(voiceMode === "live" ? "local" : "live")
                    }
                    title={
                      voiceMode === "live"
                        ? "Switch to the on-device pipeline — free, slower"
                        : "Switch to Gemini Live — faster, metered"
                    }
                  >
                    {voiceMode === "live" ? "Switch to on-device" : "Switch to Live"}
                  </Button>
                )}
                <Button variant="danger" onClick={onSelectText} disabled={voiceStopping}>
                  {voiceStopping ? "Stopping…" : "Stop"}
                </Button>
              </>
            )}
          </div>
        )}

        <div className="flex items-end gap-2">
          {/* Start voice from the conversation itself. Without this the only
              way in is the hero, which is only on screen before the first
              turn. */}
          {!voiceLive && (
            <button
              type="button"
              disabled={offline || !voiceReady || voicePhase === "starting"}
              onClick={onSelectVoice}
              title={
                voiceReady
                  ? "Start a voice session"
                  : voiceUnavailableReason || "Voice is unavailable on this machine"
              }
              aria-label="Start a voice session"
              className="glass flex h-[2.75rem] w-[2.75rem] shrink-0 items-center justify-center rounded-[var(--radius-lg)] text-[var(--text-muted)] hover:border-[var(--accent-line)] hover:text-[var(--accent)] disabled:opacity-40 disabled:hover:text-[var(--text-muted)]"
              style={{ transition: "all var(--motion-fast) var(--ease)" }}
            >
              <Icon name="mic" size={18} />
            </button>
          )}

          <textarea
            value={draft}
            rows={1}
            disabled={offline}
            placeholder={
              offline
                ? "Not connected"
                : voiceLive
                  ? "Voice is live — or type instead…"
                  : "Ask, or describe a task…"
            }
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter is a newline. A destructive request is
              // still gated by its confirmation card, so this is safe.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            className="glass field-glow max-h-40 min-h-[2.75rem] flex-1 resize-none rounded-[var(--radius-lg)] px-4 py-2.5 text-[length:var(--text-base)] outline-none placeholder:text-[var(--text-faint)] disabled:opacity-50"
            style={{ transition: "border-color var(--motion-fast) var(--ease), box-shadow var(--motion-base) var(--ease)" }}
          />
          {/* One control, two jobs. While a turn is running the only useful
              action is stopping it, and a disabled "Working…" button was
              occupying exactly the spot a stop belongs in. */}
          {busy ? (
            <Button variant="danger" onClick={onCancel} disabled={cancelling}>
              {cancelling ? "Stopping…" : "Stop"}
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={submit}
              disabled={offline || !draft.trim()}
            >
              Send
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={onReset}
            disabled={busy || turns.length === 0}
            title="Clear the conversation"
          >
            Clear
          </Button>
        </div>
      </div>
    </div>
  );
}
