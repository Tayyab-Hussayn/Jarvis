/**
 * The wire protocol, mirrored from `kravein/events.py` and `kravein/server.py`.
 *
 * Kept as a discriminated union on `t` so adding a frame server-side and
 * forgetting to handle it here is a compile error, not a silent no-op.
 */

export type Danger = "SAFE" | "MODERATE" | "DESTRUCTIVE" | "CRITICAL";

/* -- server -> client ------------------------------------------------ */

export type ServerFrame =
  | { t: "hello"; assistant_name: string; skills: number; provider: string; model: string; environment: string; local_client?: boolean }
  | { t: "turn_start"; id: string; skill?: string }
  | { t: "thinking"; id: string; text: string }
  | { t: "tool_call"; id: string; call_id: string; name: string; args: Record<string, unknown>; display: string }
  | { t: "tool_result"; id: string; call_id: string; name: string; result: string }
  | { t: "confirm_request"; id: string; confirm_id: string; action: string; detail: string; danger: Danger; timeout: number }
  | { t: "confirm_expired"; id: string; confirm_id: string }
  | { t: "confirm_resolved"; id: string; confirm_id: string; outcome: "approved" | "denied" | "expired"; by: string }
  | { t: "answer"; id: string; text: string }
  | { t: "turn_end"; id: string; iterations: number; tool_calls?: string[]; error?: string; cancelled?: boolean }
  | { t: "cancelled"; id?: string; pending_confirms?: number }
  // Pushed by the scheduled-jobs tick, which runs in its own process. Arrives
  // unsolicited, with no turn behind it.
  | { t: "job_event"; id: string; title: string; text: string; failed: boolean }
  // Pushed by the routine pass inside the same tick. A reminder that was
  // just sent, so an open Habits panel updates without waiting for a poll.
  | { t: "routine_event"; routine: string; step: string; label: string; text: string; nag: number }
  | { t: "error"; id?: string; message: string }
  | { t: "reset_ok" }
  | { t: "voice_state"; phase: string; text: string; audio?: "device" | "laptop" }
  // Device voice: the user talked over the model; drop queued playback. Model
  // audio itself arrives as binary frames (PCM16 mono 24 kHz), not JSON.
  | { t: "audio_clear" }
  | { t: "pong" };

/* -- client -> server ------------------------------------------------ */

export type ClientFrame =
  | { t: "ask"; id: string; text: string }
  // Panels invoke a skill directly — no model, no tokens. Confirmations still
  // route through the same bridge as a model-issued call.
  | { t: "run"; id: string; skill: string; args: Record<string, unknown> }
  | { t: "confirm_reply"; confirm_id: string; approve: boolean }
  // Stop a running turn. The agent loop checks a flag between tool rounds;
  // work already in flight still has to finish, so this is "start nothing
  // more", not "abort instantly".
  | { t: "cancel" }
  | { t: "reset" }
  // Voice runs server-side (same machine, loopback only), so the browser only
  // starts and stops it — audio never crosses the socket. `mode` is a
  // *preference*: only the server knows what is installed and keyed, so it
  // resolves "auto" and reports what actually started in the `started` frame.
  //
  // `audio: "device"` makes this page the microphone and speaker: it then sends
  // binary PCM16 mono 16 kHz frames on the same socket (no JSON), and needs
  // Live. Omitted or "laptop" is the server's own mic and speaker.
  | { t: "voice_start"; mode: "local" | "live" | "auto"; audio?: "device" | "laptop" }
  | { t: "voice_stop" }
  | { t: "ping" };

/* -- REST ------------------------------------------------------------ */

export interface ProviderState {
  name: string;
  available: boolean;
  cooling: boolean;
}

export interface StateSnapshot {
  assistant_name: string;
  hostname: string;
  skills: number;
  provider: string;
  model: string;
  fallback_model: string;
  provider_order: string[];
  providers: ProviderState[];
  environment: {
    distro: string;
    kernel: string;
    session_type: string;
    desktop: string;
    compositor: string;
    audio: string;
    package_manager: string;
    summary: string;
  };
}

export interface SystemSnapshot {
  available: boolean;
  cpu_percent?: number;
  cpu_count?: number;
  load?: number[];
  memory?: { total: number; used: number; percent: number };
  swap?: { total: number; used: number; percent: number };
  disk?: { total: number; used: number; percent: number };
  battery?: { percent: number; plugged: boolean };
  network?: { sent: number; received: number };
  uptime_seconds?: number;
}

export interface SkillInfo {
  name: string;
  description: string;
  category: string;
  danger: Danger;
  available: boolean;
  unavailable_reason: string;
  parameters: Record<string, unknown>;
  aliases: string[];
}

export interface AuditEntry {
  ts: string;
  session: string;
  event: string;
  [key: string]: unknown;
}

/* -- view model ------------------------------------------------------ */

export interface ToolCallView {
  callId: string;
  name: string;
  args: Record<string, unknown>;
  display: string;
  result?: string;
  done: boolean;
  /** The socket died while this ran; the result is unknown, not failed. */
  lost?: boolean;
  /** Arguments as text, for calls that arrive already rendered rather than
   *  structured. The voice pipeline reports `name(k='v')` as one string, so
   *  there is no object to preview — this keeps the arguments visible instead
   *  of inventing a fake `args` object to hold them. */
  argsText?: string;
}

export interface ConfirmView {
  confirmId: string;
  action: string;
  detail: string;
  danger: Danger;
  /** Epoch ms when the server stops waiting and denies. */
  expiresAt: number;
  status: "pending" | "approved" | "denied" | "expired";
  /** Who answered, when the server said ("phone", "web"); empty otherwise. */
  by?: string;
}

/** Phases in which a voice session is genuinely live.
 *  One definition, because the toggle and the hook must agree on it. */
export function isVoiceLive(phase: VoicePhase): boolean {
  return phase !== "off" && phase !== "error";
}

export type VoicePhase =
  | "off"
  | "starting"
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

export interface RoutineEvent {
  routine: string;
  step: string;
  label: string;
  text: string;
  nag: number;
  at: number;
}

export interface JobEvent {
  id: string;
  title: string;
  text: string;
  failed: boolean;
  at: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  thinking?: string[];
  tools?: ToolCallView[];
  confirms?: ConfirmView[];
  error?: string;
  iterations?: number;
  /** Assistant messages stay pending until `turn_end`. */
  pending?: boolean;
  /** The user stopped this turn. Rendered differently from a failure — it did
   *  what was asked, so it is not an error. */
  cancelled?: boolean;
  /** The connection dropped before this turn finished. */
  interrupted?: boolean;
}
