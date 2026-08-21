/**
 * The wire protocol, mirrored from `brahma/events.py` and `brahma/server.py`.
 *
 * Kept as a discriminated union on `t` so adding a frame server-side and
 * forgetting to handle it here is a compile error, not a silent no-op.
 */

export type Danger = "SAFE" | "MODERATE" | "DESTRUCTIVE" | "CRITICAL";

/* -- server -> client ------------------------------------------------ */

export type ServerFrame =
  | { t: "hello"; assistant_name: string; skills: number; provider: string; model: string; environment: string }
  | { t: "turn_start"; id: string }
  | { t: "thinking"; id: string; text: string }
  | { t: "tool_call"; id: string; call_id: string; name: string; args: Record<string, unknown>; display: string }
  | { t: "tool_result"; id: string; call_id: string; name: string; result: string }
  | { t: "confirm_request"; id: string; confirm_id: string; action: string; detail: string; danger: Danger; timeout: number }
  | { t: "confirm_expired"; id: string; confirm_id: string }
  | { t: "answer"; id: string; text: string }
  | { t: "turn_end"; id: string; iterations: number; tool_calls?: string[]; error?: string }
  | { t: "error"; id?: string; message: string }
  | { t: "reset_ok" }
  | { t: "pong" };

/* -- client -> server ------------------------------------------------ */

export type ClientFrame =
  | { t: "ask"; id: string; text: string }
  | { t: "confirm_reply"; confirm_id: string; approve: boolean }
  | { t: "reset" }
  | { t: "ping" };

/* -- REST ------------------------------------------------------------ */

export interface ProviderState {
  name: string;
  available: boolean;
  cooling: boolean;
}

export interface StateSnapshot {
  assistant_name: string;
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
}

export interface ConfirmView {
  confirmId: string;
  action: string;
  detail: string;
  danger: Danger;
  /** Epoch ms when the server stops waiting and denies. */
  expiresAt: number;
  status: "pending" | "approved" | "denied" | "expired";
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
}
