"use client";

import { useEffect, useRef, useState } from "react";

import type { Message, VoiceLine, VoicePhase } from "@/lib/protocol";
import type { Connection } from "@/lib/useBrahma";
import { ModeToggle, isVoiceLive } from "./ModeToggle";
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

/**
 * Pairs each assistant turn with the request that produced it, so the graph
 * can render the request as its root node. Turn ids are `t<n>` and the user
 * message that triggered it is `t<n>-user`.
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
  voicePhase,
  voiceStopping,
  voiceLines,
  voiceReady,
  voiceUnavailableReason,
  onSelectText,
  onSelectVoice,
}: {
  messages: Message[];
  busy: boolean;
  connection: Connection;
  skills?: number;
  onAsk: (text: string) => void;
  onRespond: (confirmId: string, approve: boolean) => void;
  onReset: () => void;
  voicePhase: VoicePhase;
  voiceStopping: boolean;
  voiceLines: VoiceLine[];
  voiceReady: boolean;
  voiceUnavailableReason?: string;
  onSelectText: () => void;
  onSelectVoice: () => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Follow the stream, but only when already at the bottom — yanking the view
  // down while the user reads an earlier tool result is the classic annoyance.
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
  // Only the tail is worth showing in the hero; the panel holds the full log.
  const recentVoice = voiceLines.slice(-4);

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto pr-1">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center">
            <div className="w-full max-w-md text-center">
              <h2 className="text-[length:var(--text-xl)] font-semibold tracking-[-0.01em]">
                What should Brahma do?
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
                  shows the spoken exchange instead, because a list of things to
                  click is the wrong prompt when the point is to talk. */}
              {voiceLive ? (
                <div className="mt-5 min-h-[7.5rem] rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3.5 text-left">
                  {recentVoice.length === 0 ? (
                    <p className="text-center text-[length:var(--text-sm)] text-[var(--text-faint)]">
                      {voicePhase === "starting"
                        ? "Loading the speech model…"
                        : "Listening. Say something."}
                    </p>
                  ) : (
                    <div className="space-y-1.5">
                      {recentVoice.map((line) => (
                        <p
                          key={line.id}
                          className="text-[length:var(--text-sm)]"
                          style={{
                            color:
                              line.role === "note"
                                ? "var(--text-faint)"
                                : "var(--text)",
                            fontStyle: line.role === "note" ? "italic" : undefined,
                          }}
                        >
                          {line.role !== "note" && (
                            <span
                              className="mr-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                              style={{
                                color:
                                  line.role === "brahma"
                                    ? "var(--accent)"
                                    : "var(--text-muted)",
                              }}
                            >
                              {line.role}
                            </span>
                          )}
                          {line.text}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      disabled={offline}
                      onClick={() => onAsk(item.prompt)}
                      className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3.5 py-2.5 text-left text-[length:var(--text-sm)] text-[var(--text)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)] hover:text-[var(--accent)] disabled:opacity-40"
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

      <div className="border-t border-[var(--border)] pt-4">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            rows={1}
            disabled={offline}
            placeholder={offline ? "Not connected" : "Ask, or describe a task…"}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter is a newline. A destructive request is
              // still gated by its confirmation card, so this is safe.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            className="max-h-40 min-h-[2.75rem] flex-1 resize-none rounded-[var(--radius-lg)] border border-[var(--border-strong)] bg-[var(--surface)] px-4 py-2.5 text-[length:var(--text-base)] outline-none placeholder:text-[var(--text-faint)] focus:border-[var(--accent)] disabled:opacity-50"
            style={{ transition: "border-color var(--motion-fast) var(--ease)" }}
          />
          <Button
            variant="primary"
            onClick={submit}
            disabled={busy || offline || !draft.trim()}
          >
            {busy ? "Working…" : "Send"}
          </Button>
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
