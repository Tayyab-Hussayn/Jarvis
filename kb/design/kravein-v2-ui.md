---
type: design
owner: orchestrator
updated: 2026-11-02
---

# Kravein v2 UI: "Command Constellation"

Owner brief 2026-11-02: a premium, futuristic, agentic UI that looks nothing like a template. **No traditional sidebar.** The Orchestrator and its sub-agents are visible with a live state each, linked by line connectors, with details on click. Crazy-beautiful but reliable, and **no current feature may break**.

The owner picked the palette from reference image 3 ("ye pyara lag raha hai"): obsidian, chrome and laser red.

## Palette (one token source)
| Token | Hex | Use |
|---|---|---|
| `--obsidian` | #080808 | canvas |
| `--charcoal` | #17171C | raised surfaces, panes |
| `--graphite` | #22232A | borders on dark, inputs |
| `--steel` | #929DA7 | secondary text, idle lines |
| `--chrome` | #CDCBCA | primary text, chrome highlights |
| `--laser` | #D41514 | primary accent: live work, focus, primary buttons |
| `--coral` | #FF4B32 | hot highlight: waiting for the owner, attention |
| `--forest` | #06110C | deep ambient tint in gradients only (depth, never flat fills) |

- Dark-first. A light "chrome" theme is optional later; do not block on it.
- Text contrast must reach WCAG AA on every surface.
- Red is reserved for *live* and *action*. It is never decoration on idle things.

## Look
- **Liquid chrome + glass on obsidian**: panes are dark smoked glass (backdrop blur, 1px chrome hairline, inner highlight). Ambient volumetric red light sits behind the active agent. Add subtle film grain.
- **Editorial typography**, like the references: a bold, wide display face for headings (e.g. Unbounded or Syne), a clean grotesk for UI (e.g. Inter Tight or Geist), and a mono for tool output.
  - Self-host the fonts through `next/font` so the LAN/offline UI never fetches from Google at runtime.
- Motion is purposeful: pulses travel along connectors, nodes breathe when idle, and a laser sweep runs while working.
  - All motion respects `prefers-reduced-motion`.
  - Use CSS/SVG and the Web Animations API only; no heavy animation library.
  - Effects degrade gracefully on a low-end phone.

## Layout (no sidebar)
1. **HUD bar** (thin, top): the KRAVEIN wordmark, connection state, active model, a confirmation-mode chip (ask/decline/auto with expiry) and the clock.
2. **Constellation** (hero): the Orchestrator core sits at the centre as a chrome orb with an inner laser glow. Sub-agent nodes (system-ops, research, reviewer, later growth) orbit it, joined by SVG connectors.
   - When the Orchestrator delegates, a light pulse travels along the connector to the agent.
   - The report pulse travels back.
3. **Transmission stream**: the conversation, styled as a stream, not chat bubbles. Sub-agent activity appears inline, collapsed and labelled by agent, and expands on demand.
4. **Composer capsule**: floating at the bottom centre, with text, a mic button for live voice, and stop.
5. **Dock**: a floating chrome capsule with module icons: Workspaces, Apps, Phones, Routines, Jobs, Proposals, Memory, Audit, Settings. A module opens as a **pane** (a glass sheet over the canvas) instead of a sidebar route. `Ctrl/Cmd+K` opens a command palette over every module and action.
6. **Agent detail**: click a node to open a pane with:
   - identity and purpose (from its SKILL.md);
   - model;
   - current state and task;
   - a live step timeline (tool calls with status);
   - the last report (outcome / summary / changed / evidence);
   - recent delegations.
7. **Phone width (360–412 px)**: the constellation becomes a compact horizontal agent strip at the top. The dock becomes the bottom bar, panes become bottom sheets, and nothing scrolls horizontally. The Android app shows this UI under "More".

## Agent states (each must be unmistakable at a glance, and work in reduced motion)
| State | Visual |
|---|---|
| idle | dim chrome node, slow breathing glow, steel connector |
| thinking | steel ring orbiting the node |
| working | laser-red core, a sweep around the ring, connector live with travelling pulses, current tool name under the node |
| waiting for owner | coral pulse plus a badge; click jumps to the confirmation |
| done | brief white flash, then back to idle; the last outcome shows on the node |
| failed / declined | crimson outline plus an outcome badge |

## Data
- Today, sub-agent tool frames already carry `data.agent` and `data.delegation` (#139). Web can derive working/idle from those plus turn start/end.
- An engine follow-up adds `GET /api/agents` (profiles: name, purpose, model, tool count) and an `agent_state` frame (state, task, current tool, delegation, last report, waiting-for-confirmation). The web reads them when present and falls back to derivation.

## Non-negotiables
- Every existing view and action keeps working:
  - chat, confirmations (always reachable, never hidden behind a pane), stop;
  - voice (browser and phone mic);
  - workspaces, launcher, terminal popup;
  - phones and QR pairing;
  - routines, jobs, settings, confirmation policy, audit.
- The web test suite stays green, and new components get tests.
- Accessibility:
  - keyboard navigation of the constellation, dock and panes;
  - focus rings in laser red;
  - ARIA labels on nodes;
  - states never conveyed by colour alone.
- Performance: no layout thrash from the constellation, which renders with SVG + CSS transforms, and the first load stays light.
