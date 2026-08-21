"use client";

/**
 * A turn rendered as a connected flow: request → tool calls → confirmations →
 * answer, with drawn connector lines between nodes.
 *
 * The point is legibility, not decoration. A ten-call turn as a flat list is a
 * wall; as a graph you can see at a glance where it branched, where it
 * stalled, and which node is still running.
 *
 * Connectors are drawn in a single absolutely-positioned SVG measured from the
 * real DOM rather than guessed from layout maths — node heights depend on
 * content, so any computed-geometry approach drifts the moment a result is
 * longer than expected. It re-measures on resize and whenever the node set
 * changes.
 *
 * Falls back to a plain stack when the turn is long or the viewport is narrow;
 * see COLLAPSE_AFTER.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { ConfirmView, Message, ToolCallView } from "@/lib/protocol";
import { previewArgs } from "@/lib/format";
import { ConfirmCard } from "./ConfirmCard";
import { Markdown } from "./Markdown";
import { DangerBadge } from "./ui";

/** Past this many nodes the graph stops helping and a list reads better. */
const COLLAPSE_AFTER = 12;
const NARROW_PX = 900;

type NodeKind = "request" | "tool" | "confirm" | "answer" | "error";

interface GraphNode {
  key: string;
  kind: NodeKind;
  tool?: ToolCallView;
  confirm?: ConfirmView;
  text?: string;
}

function buildNodes(message: Message, request: string): GraphNode[] {
  const nodes: GraphNode[] = [];
  if (request) nodes.push({ key: "req", kind: "request", text: request });

  for (const tool of message.tools ?? []) {
    nodes.push({ key: `tool-${tool.callId}`, kind: "tool", tool });
  }
  for (const confirm of message.confirms ?? []) {
    nodes.push({ key: `cf-${confirm.confirmId}`, kind: "confirm", confirm });
  }
  if (message.text) nodes.push({ key: "ans", kind: "answer", text: message.text });
  if (message.error) nodes.push({ key: "err", kind: "error", text: message.error });
  return nodes;
}

/* -- nodes ------------------------------------------------------------ */

function ToolNode({ tool }: { tool: ToolCallView }) {
  const [open, setOpen] = useState(false);
  const args = previewArgs(tool.args);

  return (
    <div
      className="w-[min(24rem,100%)] overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-raised)]"
      style={{ animation: "node-in var(--motion-base) var(--ease)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: tool.done ? "var(--accent)" : "var(--moderate)",
            animation: tool.done ? undefined : "pulse 1.2s ease-in-out infinite",
          }}
        />
        <span className="truncate font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
          {tool.name}
        </span>
        <span className="ml-auto shrink-0 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {tool.done ? (open ? "hide" : "show") : "running"}
        </span>
      </button>

      {args && !open && (
        <p className="truncate px-3 pb-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {args}
        </p>
      )}

      {open && (
        <div className="border-t border-[var(--border)] px-3 py-2">
          {Object.keys(tool.args).length > 0 && (
            <>
              <div className="text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
                Arguments
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </>
          )}
          {tool.done && (
            <>
              <div className="mt-2 text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
                Result
              </div>
              <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
                {tool.result || "(empty)"}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Node({
  node,
  onRespond,
}: {
  node: GraphNode;
  onRespond: (confirmId: string, approve: boolean) => void;
}) {
  switch (node.kind) {
    case "request":
      return (
        <div
          className="w-[min(30rem,100%)] rounded-[var(--radius-lg)] px-4 py-2.5 text-[length:var(--text-sm)] whitespace-pre-wrap"
          style={{ background: "var(--accent-dim)", color: "var(--text)" }}
        >
          {node.text}
        </div>
      );

    case "tool":
      return <ToolNode tool={node.tool!} />;

    case "confirm":
      return (
        <div className="w-[min(30rem,100%)]">
          <ConfirmCard confirm={node.confirm!} onRespond={onRespond} />
        </div>
      );

    case "answer":
      return (
        <div
          className="w-[min(38rem,100%)] rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-[length:var(--text-base)]"
          style={{ animation: "node-in var(--motion-base) var(--ease)" }}
        >
          <Markdown>{node.text ?? ""}</Markdown>
        </div>
      );

    case "error":
      return (
        <div
          className="w-[min(38rem,100%)] rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
          style={{
            borderLeftColor: "var(--critical)",
            background: "var(--critical-dim)",
            color: "var(--critical)",
          }}
        >
          {node.text}
        </div>
      );
  }
}

/* -- connectors ------------------------------------------------------- */

interface Edge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * Measure node positions from the DOM and derive elbow connectors.
 *
 * Runs in a layout effect so the lines land in the same frame as the nodes,
 * and re-runs on resize plus whenever the node count changes.
 */
function useEdges(count: number) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [size, setSize] = useState({ width: 0, height: 0 });

  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const base = container.getBoundingClientRect();
    const boxes = nodeRefs.current
      .slice(0, count)
      .map((el) => (el ? el.getBoundingClientRect() : null));

    const next: Edge[] = [];
    for (let i = 0; i < boxes.length - 1; i += 1) {
      const from = boxes[i];
      const to = boxes[i + 1];
      if (!from || !to) continue;
      next.push({
        x1: from.left - base.left + 18,
        y1: from.bottom - base.top,
        x2: to.left - base.left + 18,
        y2: to.top - base.top,
      });
    }
    setEdges(next);
    setSize({ width: base.width, height: base.height });
  }, [count]);

  useLayoutEffect(measure, [measure]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    // Node heights change as results stream in and disclosures toggle, so
    // observe the subtree rather than only the window.
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    for (const el of nodeRefs.current) if (el) observer.observe(el);
    return () => observer.disconnect();
  }, [measure, count]);

  return { containerRef, nodeRefs, edges, size, measure };
}

/* -- the graph -------------------------------------------------------- */

export function TurnGraph({
  message,
  request,
  onRespond,
}: {
  message: Message;
  request: string;
  onRespond: (confirmId: string, approve: boolean) => void;
}) {
  const nodes = buildNodes(message, request);
  const [narrow, setNarrow] = useState(false);

  useEffect(() => {
    const check = () => setNarrow(window.innerWidth < NARROW_PX);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const { containerRef, nodeRefs, edges, size } = useEdges(nodes.length);
  const asGraph = !narrow && nodes.length <= COLLAPSE_AFTER;

  if (nodes.length === 0) {
    return (
      <p className="text-[length:var(--text-sm)] text-[var(--text-faint)]">Thinking…</p>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      {asGraph && edges.length > 0 && (
        <svg
          className="pointer-events-none absolute inset-0"
          width={size.width}
          height={size.height}
          aria-hidden
        >
          {edges.map((edge, index) => {
            // Elbow: down from the source, across, then down into the target.
            const mid = edge.y1 + (edge.y2 - edge.y1) / 2;
            const d =
              edge.x1 === edge.x2
                ? `M ${edge.x1} ${edge.y1} L ${edge.x2} ${edge.y2}`
                : `M ${edge.x1} ${edge.y1} L ${edge.x1} ${mid} Q ${edge.x1} ${edge.y2} ${edge.x2 > edge.x1 ? edge.x1 + 12 : edge.x1 - 12} ${edge.y2} L ${edge.x2} ${edge.y2}`;
            return (
              <path
                key={index}
                d={d}
                fill="none"
                stroke="var(--accent-line)"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            );
          })}
        </svg>
      )}

      <div className={asGraph ? "flex flex-col gap-5" : "flex flex-col gap-3"}>
        {nodes.map((node, index) => (
          <div
            key={node.key}
            ref={(el) => {
              nodeRefs.current[index] = el;
            }}
            className={
              asGraph && node.kind !== "request" ? "ml-9" : undefined
            }
          >
            <Node node={node} onRespond={onRespond} />
          </div>
        ))}
      </div>

      {!asGraph && nodes.length > COLLAPSE_AFTER && (
        <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {nodes.length} steps — shown as a list because the graph stops being
          readable past {COLLAPSE_AFTER}.
        </p>
      )}

      {!message.pending && message.iterations !== undefined && (message.tools?.length ?? 0) > 0 && (
        <p className="mt-3 flex items-center gap-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          <span>
            {message.tools!.length} tool {message.tools!.length === 1 ? "call" : "calls"}
          </span>
          <span aria-hidden>·</span>
          <span>
            {message.iterations} {message.iterations === 1 ? "round" : "rounds"}
          </span>
          {(message.confirms?.length ?? 0) > 0 && (
            <>
              <span aria-hidden>·</span>
              <DangerBadge
                danger={
                  message.confirms!.some((c) => c.danger === "CRITICAL")
                    ? "CRITICAL"
                    : "DESTRUCTIVE"
                }
              />
            </>
          )}
        </p>
      )}
    </div>
  );
}
