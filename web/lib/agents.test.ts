import assert from "node:assert/strict";
import { test } from "node:test";

import {
  initialModel,
  isTransient,
  needsAttention,
  outcomeState,
  parseDelegateReport,
  reduce,
  type AgentEvent,
  type AgentsModel,
} from "./agents.ts";

function run(events: AgentEvent[], start: AgentsModel = initialModel()): AgentsModel {
  let t = 1000;
  return events.reduce((m, e) => reduce(m, e, (t += 100)), start);
}

const REPORT = (agent: string, outcome: string, summary = "Did the thing.") =>
  `[${agent} report #ab12cd34] outcome: ${outcome}\nSummary: ${summary}\nTools used: shell`;

test("starts with the Orchestrator and the three known sub-agents, all idle", () => {
  const m = initialModel();
  assert.deepEqual(m.order, ["orchestrator", "system-ops", "research", "reviewer"]);
  assert.ok(Object.values(m.agents).every((a) => a.state === "idle"));
});

test("a plain turn: thinking, working on a tool, back to thinking, done, then idle", () => {
  let m = run([{ t: "ask_sent", text: "what is using my disk?" }, { t: "turn_start", id: "t1" }]);
  assert.equal(m.agents.orchestrator.state, "thinking");
  assert.equal(m.agents.orchestrator.task, "what is using my disk?");

  m = run([{ t: "tool_call", id: "t1", call_id: "c1", name: "disk_usage", args: { path: "/" } }], m);
  assert.equal(m.agents.orchestrator.state, "working");
  assert.equal(m.agents.orchestrator.tool, "disk_usage");
  assert.equal(m.agents.orchestrator.steps[0].status, "running");
  assert.equal(m.agents.orchestrator.steps[0].args, "path=/");

  m = run([{ t: "tool_result", id: "t1", call_id: "c1", name: "disk_usage", result: "ok" }], m);
  assert.equal(m.agents.orchestrator.state, "thinking");
  assert.equal(m.agents.orchestrator.steps[0].status, "ok");
  assert.ok((m.agents.orchestrator.steps[0].durationMs ?? -1) >= 0);

  m = run([{ t: "turn_end", id: "t1", iterations: 2 }], m);
  assert.equal(m.agents.orchestrator.state, "done");
  assert.ok(isTransient(m.agents.orchestrator.state));

  m = run([{ t: "settle" }], m);
  assert.equal(m.agents.orchestrator.state, "idle");
});

test("a delegated turn lights the sub-agent and pulses out, then back on its report", () => {
  let m = run([
    { t: "turn_start", id: "t1" },
    {
      t: "tool_call", id: "t1", call_id: "c1", name: "delegate",
      args: { agent: "system-ops", task: "Find the biggest folders under ~" },
    },
  ]);
  assert.equal(m.agents.orchestrator.state, "working");
  assert.equal(m.agents["system-ops"].state, "thinking");
  assert.equal(m.agents["system-ops"].task, "Find the biggest folders under ~");
  assert.equal(m.agents["system-ops"].outSeq, 1);
  assert.equal(m.agents["system-ops"].backSeq, 0);

  // The sub-agent's own tool frames carry its name (engine #139).
  m = run([
    { t: "tool_call", id: "t1", call_id: "d1:c9", name: "run_command", args: { command: "du -sh *" }, agent: "system-ops", delegation: "d1" },
  ], m);
  assert.equal(m.agents["system-ops"].state, "working");
  assert.equal(m.agents["system-ops"].tool, "run_command");
  assert.equal(m.agents.orchestrator.tool, "delegate", "the Orchestrator keeps its own tool");

  m = run([
    { t: "tool_result", id: "t1", call_id: "d1:c9", name: "run_command", result: "12G .cache", agent: "system-ops", delegation: "d1" },
  ], m);
  assert.equal(m.agents["system-ops"].steps[0].status, "ok");

  m = run([
    { t: "tool_result", id: "t1", call_id: "c1", name: "delegate", result: REPORT("system-ops", "done", "~/.cache is 12G.") },
  ], m);
  assert.equal(m.agents["system-ops"].state, "done");
  assert.equal(m.agents["system-ops"].backSeq, 1);
  assert.deepEqual(m.agents["system-ops"].report, { outcome: "done", summary: "~/.cache is 12G." });
  assert.equal(m.agents["system-ops"].delegations[0].outcome, "done");
  assert.equal(m.agents.orchestrator.state, "thinking");
  assert.equal(m.agents.research.state, "idle", "only the delegate lights up");
});

test("declined and failed reports map to their own states, not to done", () => {
  assert.equal(outcomeState("declined"), "declined");
  assert.equal(outcomeState("needs-approval"), "declined");
  assert.equal(outcomeState("failed"), "failed");
  assert.equal(outcomeState("partial"), "done");
  const m = run([
    { t: "tool_call", id: "t", call_id: "c", name: "delegate", args: { agent: "research", task: "x" } },
    { t: "tool_result", id: "t", call_id: "c", name: "delegate", result: REPORT("research", "failed", "No network.") },
  ]);
  assert.equal(m.agents.research.state, "failed");
  assert.ok(needsAttention(m.agents.research));
});

test("a confirmation puts the asking agent in waiting and an answer restores it", () => {
  let m = run([
    { t: "turn_start", id: "t1" },
    { t: "tool_call", id: "t1", call_id: "c1", name: "delegate", args: { agent: "system-ops", task: "stop nginx" } },
    { t: "tool_call", id: "t1", call_id: "d:c2", name: "service_control", args: {}, agent: "system-ops", delegation: "d" },
    { t: "confirm_request", id: "t1", confirm_id: "k1", action: "system-ops: stop nginx", detail: "", danger: "DESTRUCTIVE", timeout: 120 },
  ]);
  assert.equal(m.agents["system-ops"].state, "waiting");
  assert.notEqual(m.agents.orchestrator.state, "waiting");
  assert.ok(needsAttention(m.agents["system-ops"]));

  m = run([{ t: "confirm_resolved", id: "t1", confirm_id: "k1", outcome: "approved", by: "web" }], m);
  assert.equal(m.agents["system-ops"].state, "working");
});

test("an unprefixed confirmation belongs to the Orchestrator", () => {
  const m = run([
    { t: "turn_start", id: "t1" },
    { t: "confirm_request", id: "t1", confirm_id: "k", action: "delete file", detail: "", danger: "DESTRUCTIVE", timeout: 60 },
  ]);
  assert.equal(m.agents.orchestrator.state, "waiting");
  const after = run([{ t: "confirm_expired", id: "t1", confirm_id: "k" }], m);
  assert.equal(after.agents.orchestrator.state, "thinking");
});

test("a turn error fails the Orchestrator; ending the turn cuts sub-agents short", () => {
  let m = run([
    { t: "turn_start", id: "t1" },
    { t: "tool_call", id: "t1", call_id: "c1", name: "delegate", args: { agent: "research", task: "x" } },
  ]);
  m = run([{ t: "turn_end", id: "t1", iterations: 1, error: "model down" }], m);
  assert.equal(m.agents.orchestrator.state, "failed");
  assert.equal(m.agents.research.state, "idle");
  assert.equal(m.agents.orchestrator.steps[0].status, "lost");
});

test("a stopped turn is not a failure", () => {
  const m = run([
    { t: "turn_start", id: "t1" },
    { t: "turn_end", id: "t1", iterations: 1, cancelled: true },
  ]);
  assert.equal(m.agents.orchestrator.state, "idle");
});

test("a dropped socket idles everything (the result is unknown, nothing is working)", () => {
  const m = run([
    { t: "turn_start", id: "t1" },
    { t: "tool_call", id: "t1", call_id: "c1", name: "shell", args: {} },
    { t: "disconnected" },
  ]);
  assert.ok(Object.values(m.agents).every((a) => a.state === "idle"));
});

test("an agent_state frame is authoritative and stops the derivation", () => {
  let m = run([
    { t: "agent_state", agent: "orchestrator", state: "working", tool: "delegate", task: "tidy downloads", since: 5 },
    { t: "agent_state", agent: "system-ops", state: "working", tool: "move_files", delegation: "d1", since: 6 },
  ]);
  assert.equal(m.authoritative, true);
  assert.equal(m.agents["system-ops"].state, "working");
  assert.equal(m.agents["system-ops"].tool, "move_files");
  assert.equal(m.agents["system-ops"].outSeq, 1);
  assert.equal(m.agents.orchestrator.task, "tidy downloads");

  // A derived frame no longer moves state, but still records the step.
  m = run([{ t: "tool_call", id: "t", call_id: "c", name: "shell", args: {} }], m);
  assert.equal(m.agents.orchestrator.tool, "delegate");
  assert.equal(m.agents.orchestrator.steps.length, 1);

  m = run([{ t: "settle" }], m);
  assert.equal(m.agents["system-ops"].state, "working", "settle only ends outcomes, never live work");

  m = run([
    { t: "agent_state", agent: "system-ops", state: "done", report: { outcome: "done", summary: "Moved 4 files." }, since: 9 },
  ], m);
  assert.equal(m.agents["system-ops"].state, "done");
  assert.equal(m.agents["system-ops"].backSeq, 1);
  assert.equal(m.agents["system-ops"].report?.summary, "Moved 4 files.");
});

test("the server's done+idle pair still shows the outcome until settle", () => {
  let m = run([
    { t: "agent_state", agent: "system-ops", state: "working", since: 1 },
    { t: "agent_state", agent: "system-ops", state: "done", report: { outcome: "done", summary: "ok" }, since: 2 },
    { t: "agent_state", agent: "system-ops", state: "idle", since: 2 },
  ]);
  assert.equal(m.agents["system-ops"].state, "done", "outcome is held, not skipped");
  assert.equal(m.agents["system-ops"].report?.summary, "ok", "idle keeps the report");
  m = run([{ t: "settle" }], m);
  assert.equal(m.agents["system-ops"].state, "idle");
  assert.equal(m.agents["system-ops"].report?.summary, "ok");
});

test("malformed agent_state frames are ignored", () => {
  const before = initialModel();
  for (const bad of [
    { t: "agent_state", state: "working" },
    { t: "agent_state", agent: "research", state: "exploding" },
    { t: "agent_state", agent: "research" },
  ] as AgentEvent[]) {
    assert.equal(reduce(before, bad, 1), before);
  }
});

test("agent_state for a new sub-agent adds a node; profiles fill in identity", () => {
  let m = run([{ t: "agent_state", agent: "growth", state: "thinking", since: 1 }]);
  assert.ok(m.order.includes("growth"));
  m = run([
    {
      t: "profiles",
      orchestratorEnabled: true,
      agents: [
        { name: "orchestrator", purpose: "Plans.", model: "gemini:flash", tools: 9, role: "orchestrator" },
        { name: "research", purpose: "Reads.", model: "zen:deepseek", tools: 12 },
      ],
    },
  ], m);
  assert.equal(m.orchestratorEnabled, true);
  assert.equal(m.agents.research.model, "zen:deepseek");
  assert.equal(m.agents.research.tools, 12);
  assert.equal(m.agents.orchestrator.purpose, "Plans.");
});

test("with the switch off the single default agent is the Orchestrator", () => {
  const m = run([
    { t: "profiles", orchestratorEnabled: false, agents: [{ name: "default", purpose: "Does everything." }] },
  ]);
  assert.equal(m.orchestratorEnabled, false);
  assert.equal(m.agents.orchestrator.purpose, "Does everything.");
});

test("a snapshot applies every state at once", () => {
  const m = run([
    { t: "snapshot", states: [
      { agent: "orchestrator", state: "thinking" },
      { agent: "reviewer", state: "working", tool: "read_file" },
    ] },
  ]);
  assert.equal(m.agents.reviewer.state, "working");
  assert.equal(m.agents.orchestrator.state, "thinking");
  assert.equal(m.authoritative, false, "only a live agent_state frame takes over");
});

test("a late snapshot never overwrites what the socket already said", () => {
  const live = run([
    { t: "turn_start", id: "t1" },
    { t: "tool_call", id: "t1", call_id: "c1", name: "shell", args: {} },
  ]);
  // The snapshot was taken before the turn: everything idle.
  const after = run([
    { t: "snapshot", states: [
      { agent: "orchestrator", state: "idle" },
      { agent: "research", state: "idle" },
    ] },
  ], live);
  assert.equal(after.agents.orchestrator.state, "working");
  assert.equal(after.agents.orchestrator.tool, "shell");
  // An idle, untouched agent is still raised by a snapshot (reconnect case).
  const raised = run([{ t: "snapshot", states: [{ agent: "research", state: "working", tool: "web_search" }] }]);
  assert.equal(raised.agents.research.state, "working");
});

test("task text is trimmed to 200 characters and steps are capped", () => {
  let m = run([{ t: "ask_sent", text: "x".repeat(500) }]);
  assert.ok(m.agents.orchestrator.task.length <= 200);
  const calls: AgentEvent[] = Array.from({ length: 60 }, (_, i) => ({
    t: "tool_call", id: "t", call_id: `c${i}`, name: "shell", args: {},
  }));
  m = run(calls, m);
  assert.equal(m.agents.orchestrator.steps.length, 40);
});

test("replay_start drops what the agents did but keeps who they are", () => {
  let m = run([
    { t: "profiles", agents: [{ name: "research", purpose: "Reads.", model: "m", tools: 3 }] },
    { t: "tool_call", id: "t", call_id: "c", name: "delegate", args: { agent: "research", task: "x" } },
    { t: "agent_state", agent: "research", state: "working", since: 1 },
  ]);
  m = run([{ t: "replay_start" }], m);
  assert.equal(m.agents.research.purpose, "Reads.");
  assert.equal(m.agents.research.tools, 3);
  assert.equal(m.agents.research.state, "idle");
  assert.equal(m.agents.orchestrator.steps.length, 0);
  assert.equal(m.authoritative, false);
});

test("parseDelegateReport reads the head and summary, and refuses other text", () => {
  assert.deepEqual(parseDelegateReport(REPORT("research", "partial", "Half.")), {
    agent: "research", id: "ab12cd34", outcome: "partial", summary: "Half.",
  });
  assert.equal(parseDelegateReport("Unknown sub-agent 'x'."), null);
});
