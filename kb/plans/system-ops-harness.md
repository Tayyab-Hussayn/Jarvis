---
type: plan
owner: orchestrator
updated: 2026-11-03
---

# System-ops harness upgrade (owner-approved 2026-11-03)

Goal: system-ops becomes a Claude-Code-grade system agent. It plans, verifies, can undo, pushes back, and proposes improvements. This is built inside the current Kravein design: `@skill` functions, `safety.enforce()` in skills, profiles in `kravein/agents/`, the proposals queue and the tasks manager. There is no new framework and no second agent stack.

Sources: three research reports, made 2026-11-03 and summarised here (raw reports lived in a session scratchpad):
- Claude Code docs;
- static analysis of the Claude Code 2.1.316 bundle, done with strings and grep because REA could not unpack the Bun ELF;
- a map of Kravein's system-ops.

## Gaps found
- There is no todo/plan tool.
- Read-before-edit is in the prompt only.
- There is no `replace_all`, no multi-edit and no diff.
- `search_text` is plain text only.
- The shell does not keep its cwd, does not spill large output to a file, and kills a command at its timeout.
- There are no checkpoints and no sandbox.
- system-ops is capped at 6 rounds.
- Sub-agents cannot propose.
- **Security:** `read_file` and shell `cat` read secret files with no check.

## Claude Code behaviour to borrow
- **Executing actions with care:** weigh reversibility and blast radius; confirm before anything destructive or shared; an approval covers only its own scope; never take a destructive shortcut past an obstacle; investigate before deleting.
- **Delivering work:** state a concern in 1–2 sentences, then continue; ask only blocking questions; when declining, offer the nearest alternative.
- **Tool design:** errors written for the model; spill large output to a file with a preview; a TodoWrite list with exactly one item in progress.

## Phases
0. **Secret-read guard.** `safety.check_read` covers read_file and the argv of every command. Adds `Agent.tool_state`. engine #211.
1. **Tools.**
   - a. read_file with line numbers and a read record; edit_file with read-before-edit, `replace_all`, multi-edit and a diff; search_text with regex, context and pagination; find_files sorted by mtime. skills #212.
   - b. Shell: persistent cwd per agent run, output spilled to a file, process-group kill. skills, after 1a.
2. **Loop.**
   - `update_plan` todo tool (code enforces exactly one in_progress), shown in the web task tray.
   - system-ops rounds raised to about 25 under a token budget; running out returns a partial report.
   - Checkpoints before every agent file change, plus `undo_changes` behind a card.
   - Owner instruction file `~/.config/kravein/KRAVEIN.md`, read-only for the agent.
   - A command that times out moves to a background task.
3. **Companion.**
   - The SKILL.md and base prompt get the care and delivery rules.
   - The report gets a CONCERNS/IMPROVEMENTS section.
   - system-ops may `propose`; the Orchestrator discusses the proposal with the owner, and only the owner's card decides.
4. **Sandbox.** bwrap read-only with no network, for inspection and dry runs. Falls back to today's behaviour when bwrap is missing.

**Eval:** qa #213 adds outcome-based tasks in an isolated HOME. The baseline is taken before the upgrade lands and re-run after each phase.

**Not doing:** a hooks system, nested agents, an LLM classifier, a bypass mode.

**Open owner decision:** system-ops model. It is flash-lite today; I recommended a non-lite flash for this agent only.
