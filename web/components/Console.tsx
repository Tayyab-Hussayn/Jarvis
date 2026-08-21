"use client";

import { useEffect, useRef, useState } from "react";

import type { Message } from "@/lib/protocol";
import type { Connection } from "@/lib/useBrahma";
import { TurnGraph } from "./TurnGraph";
import { Button, Empty } from "./ui";

const SUGGESTIONS = [
  "What is using the most disk space in my home directory?",
  "Which processes are eating CPU right now?",
  "List my open windows",
  "Are there any package updates?",
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
  onAsk,
  onRespond,
  onReset,
}: {
  messages: Message[];
  busy: boolean;
  connection: Connection;
  onAsk: (text: string) => void;
  onRespond: (confirmId: string, approve: boolean) => void;
  onReset: () => void;
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

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollerRef} className="flex-1 space-y-8 overflow-y-auto pr-1 pb-4">
        {turns.length === 0 ? (
          <div className="flex h-full flex-col justify-center">
            <Empty>
              Ask Brahma something. It has real control over this machine —
              destructive actions stop and ask you first.
            </Empty>
            <div className="mx-auto flex max-w-xl flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((text) => (
                <button
                  key={text}
                  type="button"
                  disabled={offline}
                  onClick={() => onAsk(text)}
                  className="rounded-[var(--radius-full)] border border-[var(--border)] px-3 py-1.5 text-[length:var(--text-xs)] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-40"
                  style={{ transition: "all var(--motion-fast) var(--ease)" }}
                >
                  {text}
                </button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((message) => (
            <TurnGraph
              key={message.id}
              message={message}
              request={requestFor(messages, message.id)}
              onRespond={onRespond}
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <div className="pt-2">
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
