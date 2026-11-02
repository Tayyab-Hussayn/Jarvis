/**
 * Which spoken user line a `heard_partial` / `heard` frame belongs to.
 *
 * The server tags these frames with a `turn`. Live transcription can finalise
 * (or, for the deferred English line, be *replaced*) after the model already
 * answered and after `listening` closed the turn. Without the tag the client
 * treated that late frame as the start of a new turn: the same sentence showed
 * twice, the second copy under the reply, above an empty pending answer.
 *
 * Keyed by turn, a frame rewrites its own line in place no matter when it
 * arrives, and an unknown turn (reconnect, replay) renders exactly once.
 */

export type HeardRoute =
  /** Rewrite the existing user line `id` (client message id, e.g. `s3-user`). */
  | { kind: "rewrite"; id: string }
  /** Attach the server turn to the open spoken turn and write its line. */
  | { kind: "bind-open" }
  /** No open turn can take it: close any open one and start a new turn. */
  | { kind: "new-turn" }
  /** Late frame for a turn we never saw and no turn is open: line only. */
  | { kind: "orphan" };

export interface HeardState {
  /** Client id (`s…`) of the spoken turn being built, or null. */
  open: string | null;
  /** Server turn bound to the open turn, if it has been bound. */
  openServerTurn: string | null;
  /** Server turn -> client user-line message id, for every turn seen. */
  lines: ReadonlyMap<string, string>;
}

export function turnKey(turn: unknown): string | null {
  if (typeof turn === "number" && Number.isFinite(turn)) return String(turn);
  if (typeof turn === "string" && turn !== "") return turn;
  return null;
}

/**
 * A server turn id scoped to the voice session it came from.
 *
 * The server numbers turns per Live session (`lt1`, `lt2`, …) and starts again
 * at `lt1` for every new session. The client's turn→line map outlives a
 * session, so the second session's first sentence looked like a "known" turn
 * and rewrote the *first session's* line in place: the owner's new spoken
 * lines never appeared. Scoping the key by session keeps ids unique across
 * restarts while a late `replace` from the previous session still finds its
 * own line.
 */
export function scopedTurn(session: number, turn: unknown): string | null {
  const key = turnKey(turn);
  return key === null ? null : `${session}:${key}`;
}

export function routeHeard(state: HeardState, turn: unknown): HeardRoute {
  const key = turnKey(turn);
  if (key === null) {
    // Legacy / local pipeline: no tag, so the open turn is the only anchor.
    return state.open ? { kind: "bind-open" } : { kind: "new-turn" };
  }
  const known = state.lines.get(key);
  if (known) return { kind: "rewrite", id: known };
  if (state.open && state.openServerTurn === null) return { kind: "bind-open" };
  // An open turn already bound to a different server turn means the next
  // utterance began before `listening` — a new turn, not a rewrite.
  if (state.open) return { kind: "new-turn" };
  return { kind: "orphan" };
}
