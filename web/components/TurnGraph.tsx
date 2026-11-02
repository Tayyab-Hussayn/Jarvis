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

type NodeKind = "request" | "tool" | "agent" | "confirm" | "answer" | "error" | "stopped" | "lost";

interface GraphNode {
  key: string;
  kind: NodeKind;
  tool?: ToolCallView;
  /** A sub-agent's calls, collapsed under one label. */
  group?: { agent: string; delegation: string; tools: ToolCallView[] };
  confirm?: ConfirmView;
  text?: string;
}

function buildNodes(message: Message, request: string): GraphNode[] {
  const nodes: GraphNode[] = [];
  if (request) nodes.push({ key: "req", kind: "request", text: request });

  // A delegate's tool calls arrive tagged with its name (engine #139). They
  // fold into one expandable node per delegation so a long errand reads as a
  // single labelled step, not as the Orchestrator's own calls.
  const groups = new Map<string, GraphNode>();
  for (const tool of message.tools ?? []) {
    if (tool.agent) {
      const gkey = `${tool.agent}:${tool.delegation ?? ""}`;
      const existing = groups.get(gkey);
      if (existing) {
        existing.group!.tools.push(tool);
      } else {
        const node: GraphNode = {
          key: `ag-${gkey}`,
          kind: "agent",
          group: { agent: tool.agent, delegation: tool.delegation ?? "", tools: [tool] },
        };
        groups.set(gkey, node);
        nodes.push(node);
      }
      continue;
    }
    nodes.push({ key: `tool-${tool.callId}`, kind: "tool", tool });
  }
  for (const confirm of message.confirms ?? []) {
    nodes.push({ key: `cf-${confirm.confirmId}`, kind: "confirm", confirm });
  }
  if (message.text) nodes.push({ key: "ans", kind: "answer", text: message.text });
  if (message.error) nodes.push({ key: "err", kind: "error", text: message.error });
  // A stop is not a failure — it did what was asked — so it gets its own
  // neutral node rather than the red error treatment.
  if (message.interrupted && !message.text) {
    nodes.push({
      key: "lost",
      kind: "lost",
      text: "Connection lost — result unknown. The request may have completed; check before repeating it.",
    });
  }
  if (message.cancelled) {
    nodes.push({ key: "stop", kind: "stopped", text: "Stopped at your request." });
  }
  return nodes;
}

/** The agent has been asked and has produced nothing yet. */
export function Thinking({ label = "Thinking" }: { label?: string }) {
  return (
    <span
      role="status"
      className="inline-flex items-center gap-2 text-[length:var(--text-sm)] text-[var(--text-muted)]"
    >
      <span aria-hidden className="inline-flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "var(--accent)",
              boxShadow: "var(--glow-soft)",
              animation: `think 1.1s ease-in-out ${i * 150}ms infinite`,
            }}
          />
        ))}
      </span>
      {label}
    </span>
  );
}

/* -- nodes ------------------------------------------------------------ */

function ToolNode({ tool }: { tool: ToolCallView }) {
  const [open, setOpen] = useState(false);
  // Voice reports a call as one rendered string, so there is no args object to
  // preview. Falling back to that text keeps the arguments on screen instead
  // of showing a bare tool name for every spoken call.
  const args = previewArgs(tool.args) || (tool.argsText ?? "");

  return (
    <div
      className="glass w-[min(24rem,100%)] overflow-hidden rounded-[var(--radius)]"
      style={{ animation: "node-in var(--motion-base) var(--ease-out)" }}
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
            background: tool.done && !tool.lost ? "var(--accent)" : "var(--moderate)",
            boxShadow: `0 0 8px ${tool.done && !tool.lost ? "var(--accent)" : "var(--moderate)"}`,
            animation: tool.done ? undefined : "pulse 1.2s ease-in-out infinite",
          }}
        />
        <span className="truncate font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
          {tool.name}
        </span>
        <span className="ml-auto shrink-0 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {tool.lost ? "unknown" : tool.done ? (open ? "hide" : "show") : "running"}
        </span>
      </button>

      {args && !open && (
        <p className="truncate px-3 pb-2 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {args}
        </p>
      )}

      {open && (
        <div className="border-t border-[var(--border)] px-3 py-2">
          {Object.keys(tool.args).length === 0 && tool.argsText && (
            <>
              <div className="text-[length:var(--text-xs)] uppercase tracking-[0.08em] text-[var(--text-faint)]">
                Arguments
              </div>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
                {tool.argsText}
              </pre>
            </>
          )}
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

/** One sub-agent's activity: a labelled, collapsed strip that expands to its calls. */
function AgentGroupNode({ group }: { group: NonNullable<GraphNode["group"]> }) {
  const [open, setOpen] = useState(false);
  const running = group.tools.some((t) => !t.done && !t.lost);
  const lost = group.tools.some((t) => t.lost);
  const last = group.tools[group.tools.length - 1];
  const status = running ? "working" : lost ? "unknown" : "done";
  const steps = `${group.tools.length} ${group.tools.length === 1 ? "step" : "steps"}`;

  return (
    <div
      className="w-[min(30rem,100%)] overflow-hidden rounded-[var(--radius)] border"
      style={{
        borderColor: running ? "var(--accent-line)" : "var(--border)",
        background: "var(--surface)",
        animation: "node-in var(--motion-base) var(--ease-out)",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={`${group.agent} sub-agent, ${steps}, ${status}. ${open ? "Collapse" : "Expand"}`}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: running ? "var(--laser)" : "var(--steel)",
            boxShadow: running ? "var(--glow-soft)" : undefined,
            animation: running ? "pulse 1.2s ease-in-out infinite" : undefined,
          }}
        />
        <span
          className="shrink-0 text-[length:var(--text-xs)] font-semibold uppercase"
          style={{ letterSpacing: "var(--tracking-label)" }}
        >
          {group.agent}
        </span>
        <span className="min-w-0 flex-1 truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-muted)]">
          {running ? last.name : steps}
        </span>
        <span className="shrink-0 text-[length:var(--text-xs)] text-[var(--text-faint)]">
          {status} · {open ? "hide" : "show"}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--border)] p-2">
          {group.tools.map((tool) => (
            <ToolNode key={tool.callId} tool={tool} />
          ))}
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
          className="line-clamp-4 w-[min(30rem,100%)] border-l-2 py-1 pl-3 text-[length:var(--text-base)] whitespace-pre-wrap"
          title={node.text}
          style={{
            borderColor: "var(--accent)",
            color: "var(--text-strong)",
            animation: "node-in var(--motion-base) var(--ease-out)",
          }}
        >
          {node.text}
        </div>
      );

    case "tool":
      return <ToolNode tool={node.tool!} />;

    case "agent":
      return <AgentGroupNode group={node.group!} />;

    case "confirm":
      return (
        <div className="w-[min(30rem,100%)]">
          <ConfirmCard confirm={node.confirm!} onRespond={onRespond} />
        </div>
      );

    case "answer":
      return (
        <div
          className="glass w-[min(38rem,100%)] rounded-[var(--radius-lg)] px-4 py-3 text-[length:var(--text-base)]"
          style={{ animation: "node-in var(--motion-slow) var(--ease-out)" }}
        >
          <Markdown>{node.text ?? ""}</Markdown>
        </div>
      );

    case "lost":
      return (
        <div
          className="w-[min(38rem,100%)] rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
          style={{ borderLeftColor: "var(--moderate)", color: "var(--text-muted)" }}
        >
          {node.text}
        </div>
      );

    case "stopped":
      return (
        <div
          className="w-[min(38rem,100%)] rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
          style={{
            borderLeftColor: "var(--moderate)",
            color: "var(--text-muted)",
          }}
        >
          {node.text} Anything already running finished; nothing further was
          started.
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
      <Thinking />
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
                style={{ filter: "drop-shadow(0 0 3px var(--accent-line))" }}
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
            // The owner's own message stays pinned at the top while its turn
            // scrolls under it (it releases when the turn ends, since the
            // sticky range is this turn's container).
            className={
              node.kind === "request"
                ? "sticky top-0 z-[2] pb-2"
                : asGraph
                  ? "ml-9"
                  : undefined
            }
            style={
              node.kind === "request"
                ? { background: "linear-gradient(var(--bg) 70%, transparent)" }
                : undefined
            }
          >
            <Node node={node} onRespond={onRespond} />
          </div>
        ))}
      </div>

      {message.pending && !message.text && !message.error && !message.cancelled &&
        !(message.confirms ?? []).some((c) => c.status === "pending") && (
          <div className={asGraph ? "ml-9 mt-4" : "mt-3"}>
            <Thinking />
          </div>
        )}

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
