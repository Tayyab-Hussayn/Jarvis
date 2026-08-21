"use client";

import { useEffect, useRef, useState } from "react";

import type { Message } from "@/lib/protocol";
import type { Connection } from "@/lib/useBrahma";
import { ConfirmCard } from "./ConfirmCard";
import { ToolCallCard, useExpandTools } from "./ToolCallCard";
import { Button, Empty } from "./ui";

function MessageBlock({
  message,
  expandTools,
  onRespond,
}: {
  message: Message;
  expandTools: boolean;
  onRespond: (confirmId: string, approve: boolean) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-[var(--accent-dim)] px-3.5 py-2 text-[var(--text-sm)] whitespace-pre-wrap">
          {message.text}
        </div>
      </div>
    );
  }

  const thinking = message.thinking ?? [];
  const tools = message.tools ?? [];
  const confirms = message.confirms ?? [];
  const silent = !message.text && !message.error && tools.length === 0 && message.pending;

  return (
    <div className="space-y-2">
      {thinking.map((text, index) => (
        <p
          key={`think-${index}`}
          className="text-[var(--text-sm)] italic text-[var(--text-muted)]"
        >
          {text}
        </p>
      ))}

      {tools.map((tool) => (
        <ToolCallCard key={tool.callId} tool={tool} defaultOpen={expandTools} />
      ))}

      {confirms.map((confirm) => (
        <ConfirmCard key={confirm.confirmId} confirm={confirm} onRespond={onRespond} />
      ))}

      {silent && (
        <p className="text-[var(--text-sm)] text-[var(--text-faint)]">Thinking…</p>
      )}

      {message.text && (
        <div className="whitespace-pre-wrap text-[var(--text-base)]">{message.text}</div>
      )}

      {message.error && (
        <div
          className="rounded-[var(--radius)] border-l-2 px-3 py-2 text-[var(--text-sm)]"
          style={{
            borderLeftColor: "var(--critical)",
            background: "var(--critical-dim)",
            color: "var(--critical)",
          }}
        >
          {message.error}
        </div>
      )}

      {!message.pending && message.iterations !== undefined && tools.length > 0 && (
        <p className="text-[var(--text-xs)] text-[var(--text-faint)]">
          {tools.length} tool {tools.length === 1 ? "call" : "calls"} ·{" "}
          {message.iterations} {message.iterations === 1 ? "round" : "rounds"}
        </p>
      )}
    </div>
  );
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
  const [expandTools, setExpandTools] = useExpandTools();
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Follow the stream, but only when the user is already at the bottom —
  // yanking the view down while they are reading an earlier tool result is
  // the classic chat-UI annoyance.
  const scrollerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const distance = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (distance < 160) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = () => {
    if (!draft.trim() || busy) return;
    onAsk(draft);
    setDraft("");
  };

  const offline = connection !== "open";

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-1 pb-2">
        <label className="flex cursor-pointer items-center gap-2 text-[var(--text-xs)] text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={expandTools}
            onChange={(event) => setExpandTools(event.target.checked)}
            className="accent-[var(--accent)]"
          />
          Always expand tool calls
        </label>
        <Button onClick={onReset} disabled={busy || messages.length === 0}>
          Clear
        </Button>
      </div>

      <div ref={scrollerRef} className="flex-1 space-y-5 overflow-y-auto py-5 pr-1">
        {messages.length === 0 ? (
          <Empty>
            Ask Brahma something. It has real control over this machine —
            destructive actions will stop and ask you first.
          </Empty>
        ) : (
          messages.map((message) => (
            <MessageBlock
              key={message.id}
              message={message}
              expandTools={expandTools}
              onRespond={onRespond}
            />
          ))
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-[var(--border)] pt-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
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
            className="max-h-40 min-h-[2.5rem] flex-1 resize-none rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-[var(--text-base)] outline-none placeholder:text-[var(--text-faint)] disabled:opacity-50"
          />
          <Button variant="primary" onClick={submit} disabled={busy || offline || !draft.trim()}>
            {busy ? "Working…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
