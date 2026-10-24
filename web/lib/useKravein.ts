"use client";

/**
 * The websocket client, as a hook.
 *
 * Owns the socket, the reconnect policy, and the conversation view model.
 * Everything the UI renders comes out of here; no component talks to the
 * socket directly.
 *
 * Two kinds of work share one socket:
 *
 *   ask  — a model turn. Frames build up a Message in `messages`.
 *   run  — a panel invoking one skill directly. Frames resolve a promise, and
 *          any confirmation surfaces in `panelConfirms` so it can be rendered
 *          as a global overlay rather than buried in the Console.
 *
 * Turn ids carry the distinction (`t…` vs `p…`), so a single frame handler
 * routes both without the server needing to care.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ClientFrame,
  JobEvent,
  RoutineEvent,
  ConfirmView,
  Message,
  ServerFrame,
  ToolCallView,
  VoicePhase,
} from "./protocol";
import { settleConfirm } from "./confirm";
import { isVoiceLive } from "./protocol";
import { captureToken, getToken, wsUrl } from "./session";

export type Connection =
  | "connecting"
  | "open"
  | "reconnecting"
  | "offline"
  | "unauthorized";

export interface Hello {
  assistantName: string;
  skills: number;
  provider: string;
  model: string;
  environment: string;
}

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 15_000;
const PING_INTERVAL_MS = 25_000;

/** Close code 1008 is our auth rejection — retrying a bad token is pointless. */
const POLICY_VIOLATION = 1008;

/** How long to wait for the server to confirm a stop before releasing the UI. */
const STOP_CONFIRM_TIMEOUT_MS = 8000;

interface PendingRun {
  resolve: (result: string) => void;
  reject: (reason: Error) => void;
}

export function useKravein() {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [hello, setHello] = useState<Hello | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  // Set optimistically on click and cleared by the server's reply. Stopping is
  // not instant: the loop finishes whatever tool is already running before it
  // reaches the next check.
  const [cancelling, setCancelling] = useState(false);
  const [panelConfirms, setPanelConfirms] = useState<ConfirmView[]>([]);
  // A spoken exchange is a conversation, so it goes into `messages` alongside
  // the typed one and renders through the same template. It used to be held in
  // a parallel `voiceLines` array feeding a separate transcript panel, which
  // meant two rendering paths, two scroll behaviours and two idioms for the
  // same thing. Server-side they are still separate Agents with separate
  // history — that is a fact about the model's context, not a reason for the
  // user to read two different-looking logs.
  const [voicePhase, setVoicePhase] = useState<VoicePhase>("off");
  const [voiceMode, setVoiceMode] = useState<"local" | "live">("local");
  const [voiceError, setVoiceError] = useState("");
  // Stopping is not instant — the loop may be mid-utterance and only releases
  // the microphone when it unwinds. Without this the UI would look frozen
  // between the click and the server's `stopped` frame.
  const [voiceStopping, setVoiceStopping] = useState(false);
  // Session lifecycle chatter — "connecting to …", "connected", "ended". Not
  // conversation, so it stays out of the transcript and shows as a status
  // line instead of masquerading as something Kravein said.
  const [voiceNotes, setVoiceNotes] = useState<string[]>([]);
  // Scheduled jobs fire from a separate process and push through the server.
  // They belong to no turn, so they are kept apart from `messages` rather than
  // being rendered as something the assistant said.
  const [jobEvents, setJobEvents] = useState<JobEvent[]>([]);
  // Routine reminders arrive the same way and for the same reason: a separate
  // process, no turn behind them. Kept apart from `messages` — a nag is not
  // something the assistant said in conversation.
  const [routineEvents, setRoutineEvents] = useState<RoutineEvent[]>([]);
  // The spoken turn currently being built, if any. Ids are `s…` so they cannot
  // collide with typed turns (`t…`), panel runs (`p…`) or the server's own
  // voice turn ids (`v…`, used for confirmation routing).
  const voiceTurn = useRef<{ id: string; tools: number } | null>(null);
  // Callbacks need the current phase without being rebuilt on every change.
  const phaseRef = useRef<VoicePhase>("off");
  phaseRef.current = voicePhase;

  const socketRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const seqRef = useRef(0);
  const runsRef = useRef(new Map<string, PendingRun>());
  const goneRef = useRef(false);
  // Turns that currently have a message in `messages` to attach frames to.
  // A confirmation for anything else has nowhere to render inline.
  const liveTurns = useRef(new Set<string>());

  const isPanelTurn = (id: string | undefined) => Boolean(id?.startsWith("p"));

  /* -- view-model updates -------------------------------------------- */

  const patchTurn = useCallback(
    (turnId: string, fn: (message: Message) => Message) => {
      setMessages((current) =>
        current.map((message) =>
          message.id === turnId && message.role === "assistant" ? fn(message) : message,
        ),
      );
    },
    [],
  );

  const handle = useCallback(
    (frame: ServerFrame) => {
      // Panel runs never touch the conversation. They resolve a promise and,
      // if the skill is destructive, raise a global confirmation instead.
      // `error` frames carry an optional id, so narrow it once here rather
      // than re-asserting at each use.
      const turnId = "id" in frame && frame.id ? frame.id : "";

      // A confirmation resolved anywhere (phone notification, another tab,
      // cancel, timeout) closes its card here too, wherever the card lives —
      // inline or in the overlay — so it never stays open and unaware.
      if (frame.t === "confirm_resolved") {
        const settle = (confirm: ConfirmView) =>
          settleConfirm(confirm, frame.confirm_id, frame.outcome, frame.by);
        setMessages((current) =>
          current.map((message) => ({
            ...message,
            confirms: (message.confirms ?? []).map(settle),
          })),
        );
        setPanelConfirms((current) => current.map(settle));
        // The outcome is shown briefly, then the overlay card goes away.
        setTimeout(() => {
          setPanelConfirms((current) =>
            current.filter((confirm) => confirm.confirmId !== frame.confirm_id),
          );
        }, 4000);
        return;
      }

      if (isPanelTurn(turnId)) {
        switch (frame.t) {
          case "confirm_request":
            setPanelConfirms((current) => [
              ...current,
              {
                confirmId: frame.confirm_id,
                action: frame.action,
                detail: frame.detail,
                danger: frame.danger,
                expiresAt: Date.now() + frame.timeout * 1000,
                status: "pending",
              },
            ]);
            break;
          case "confirm_expired":
            setPanelConfirms((current) =>
              current.map((confirm) =>
                confirm.confirmId === frame.confirm_id
                  ? { ...confirm, status: "expired" }
                  : confirm,
              ),
            );
            break;
          case "tool_result":
            runsRef.current.get(turnId)?.resolve(frame.result);
            break;
          case "turn_end":
            runsRef.current.delete(turnId);
            setBusy(false);
            break;
          case "error":
            runsRef.current.get(turnId)?.reject(new Error(frame.message));
            runsRef.current.delete(turnId);
            setBusy(false);
            break;
        }
        return;
      }

      switch (frame.t) {
        case "hello":
          setHello({
            assistantName: frame.assistant_name,
            skills: frame.skills,
            provider: frame.provider,
            model: frame.model,
            environment: frame.environment,
          });
          break;

        case "turn_start":
          setBusy(true);
          liveTurns.current.add(frame.id);
          setMessages((current) => [
            ...current,
            {
              id: frame.id,
              role: "assistant",
              text: "",
              pending: true,
              tools: [],
              thinking: [],
              confirms: [],
            },
          ]);
          break;

        case "thinking":
          if (!frame.text.trim()) break;
          patchTurn(frame.id, (message) => ({
            ...message,
            thinking: [...(message.thinking ?? []), frame.text],
          }));
          break;

        case "tool_call":
          patchTurn(frame.id, (message) => ({
            ...message,
            tools: [
              ...(message.tools ?? []),
              {
                callId: frame.call_id,
                name: frame.name,
                args: frame.args,
                display: frame.display,
                done: false,
              } satisfies ToolCallView,
            ],
          }));
          break;

        case "tool_result":
          patchTurn(frame.id, (message) => ({
            ...message,
            tools: (message.tools ?? []).map((tool) =>
              tool.callId === frame.call_id
                ? { ...tool, result: frame.result, done: true }
                : tool,
            ),
          }));
          break;

        case "confirm_request": {
          const card: ConfirmView = {
            confirmId: frame.confirm_id,
            action: frame.action,
            detail: frame.detail,
            danger: frame.danger,
            expiresAt: Date.now() + frame.timeout * 1000,
            status: "pending",
          };
          // A confirmation blocks a worker thread until it is answered, so it
          // must never be dropped. Voice sessions carry a turn id that has no
          // message behind it, and the inline branch silently discarded those
          // — the card never appeared, nothing could answer it, and the
          // session sat frozen until it timed out and denied. Anything without
          // a live turn goes to the overlay instead.
          if (liveTurns.current.has(frame.id)) {
            patchTurn(frame.id, (message) => ({
              ...message,
              confirms: [...(message.confirms ?? []), card],
            }));
          } else {
            setPanelConfirms((current) => [...current, card]);
          }
          break;
        }

        case "confirm_expired":
          if (liveTurns.current.has(frame.id)) {
            patchTurn(frame.id, (message) => ({
              ...message,
              confirms: (message.confirms ?? []).map((confirm) =>
                confirm.confirmId === frame.confirm_id && confirm.status === "pending"
                  ? { ...confirm, status: "expired" }
                  : confirm,
              ),
            }));
          } else {
            setPanelConfirms((current) =>
              current.map((confirm) =>
                confirm.confirmId === frame.confirm_id
                  ? { ...confirm, status: "expired" }
                  : confirm,
              ),
            );
          }
          break;

        case "answer":
          patchTurn(frame.id, (message) => ({ ...message, text: frame.text }));
          break;

        case "turn_end":
          setBusy(false);
          setCancelling(false);
          liveTurns.current.delete(frame.id);
          patchTurn(frame.id, (message) => ({
            ...message,
            pending: false,
            // A stop is not a failure: it did exactly what was asked. Keeping
            // it out of `error` stops it rendering in the red error node.
            error: frame.cancelled ? undefined : frame.error || undefined,
            cancelled: frame.cancelled || undefined,
            iterations: frame.iterations,
          }));
          break;

        case "job_event":
          setJobEvents((current) => [
            {
              id: frame.id, title: frame.title, text: frame.text,
              failed: frame.failed, at: Date.now(),
            },
            ...current,
          ].slice(0, 20));
          break;

        case "routine_event":
          setRoutineEvents((current) => [
            {
              routine: frame.routine, step: frame.step, label: frame.label,
              text: frame.text, nag: frame.nag, at: Date.now(),
            },
            ...current,
          ].slice(0, 20));
          break;

        case "cancelled":
          // The server always answers, even with nothing running, so the
          // optimistic "Stopping…" flag can never wedge. `turn_end` follows
          // when the loop actually unwinds.
          if (!frame.id) setCancelling(false);
          break;

        case "error":
          if (frame.id) {
            patchTurn(frame.id, (message) => ({ ...message, error: frame.message }));
          } else {
            setMessages((current) => [
              ...current,
              {
                id: `err-${Date.now()}`,
                role: "assistant",
                text: "",
                error: frame.message,
              },
            ]);
          }
          break;

        case "reset_ok":
          setMessages([]);
          // The spoken turn's messages went with them, so the ref must not
          // keep pointing at one.
          voiceTurn.current = null;
          break;

        case "voice_state": {
          const note = (text: string) => {
            if (!text.trim()) return;
            setVoiceNotes((current) => [...current, text].slice(-6));
          };

          // A spoken turn is opened lazily by whichever frame first carries
          // content, and closed by `listening` — which both pipelines emit at
          // a turn boundary, though at opposite ends of it. Local emits it
          // before capturing the next utterance; live emits it after
          // turn_complete. Either way it means "the previous exchange is
          // over", which is the only thing this needs from it.
          const openTurn = (): string => {
            const existing = voiceTurn.current;
            if (existing) return existing.id;
            seqRef.current += 1;
            const id = `s${seqRef.current}`;
            voiceTurn.current = { id, tools: 0 };
            // A failure belonged to the turn that caused it. Left standing it
            // would sit in the status strip for the rest of the session,
            // describing something that already finished.
            setVoiceError("");
            setMessages((current) => [
              ...current,
              { id: `${id}-user`, role: "user", text: "" },
              {
                id,
                role: "assistant",
                text: "",
                pending: true,
                tools: [],
                thinking: [],
                confirms: [],
              },
            ]);
            return id;
          };

          const closeTurn = () => {
            const turn = voiceTurn.current;
            if (!turn) return;
            voiceTurn.current = null;
            // Deliberately no `iterations`: the voice pipelines do not report
            // a round count, and TurnGraph's footer only renders when one is
            // present. Inventing a number to fill the slot would be a
            // plausible-looking lie about what the model actually did.
            patchTurn(turn.id, (message) => ({ ...message, pending: false }));
          };

          // The user's words arrive as a growing string, so each delta
          // rewrites the request in place. This is why the message is created
          // on the first partial rather than when transcription completes:
          // input transcription lags the audio and only finalises at the turn
          // boundary, so appending it at the end would file the question
          // *after* the answer it prompted.
          const setRequest = (text: string) => {
            if (!text.trim()) return;
            const id = openTurn();
            setMessages((current) =>
              current.map((message) =>
                message.id === `${id}-user` ? { ...message, text } : message,
              ),
            );
          };

          const setAnswer = (text: string) => {
            if (!text.trim()) return;
            patchTurn(openTurn(), (message) => ({ ...message, text }));
          };

          const addTool = (text: string) => {
            if (!text.trim()) return;
            const id = openTurn();
            const turn = voiceTurn.current;
            if (!turn) return;
            turn.tools += 1;
            // Voice reports a call already rendered as `name(k='v')`, not as
            // a name plus an args object, so it is split rather than parsed —
            // reconstructing an args object from that string would be
            // guesswork presented as structure.
            const open = text.indexOf("(");
            const name = (open > 0 ? text.slice(0, open) : text).trim();
            const argsText =
              open > 0 ? text.slice(open + 1).replace(/\)\s*$/, "").trim() : "";
            patchTurn(id, (message) => ({
              ...message,
              tools: [
                ...(message.tools ?? []),
                {
                  callId: `${id}-c${turn.tools}`,
                  name,
                  args: {},
                  argsText,
                  display: text,
                  done: false,
                } satisfies ToolCallView,
              ],
            }));
          };

          const finishTool = (result: string) => {
            const turn = voiceTurn.current;
            if (!turn) return;
            patchTurn(turn.id, (message) => {
              const tools = [...(message.tools ?? [])];
              for (let i = tools.length - 1; i >= 0; i -= 1) {
                if (!tools[i].done) {
                  tools[i] = { ...tools[i], result, done: true };
                  break;
                }
              }
              return { ...message, tools };
            });
          };

          switch (frame.phase) {
            case "started":
              setVoicePhase("idle");
              setVoiceError("");
              // The mode comes from the server, not from what we asked for.
              // It resolves "auto" and can decline a preference that cannot
              // run, so trusting the request would let the strip label the
              // session with a pipeline it is not in.
              if (frame.text === "live" || frame.text === "local") {
                setVoiceMode(frame.text);
              }
              // Last session's chatter is not this session's status.
              setVoiceNotes([`Voice session started (${frame.text}).`]);
              break;
            case "stopped":
              setVoicePhase("off");
              setVoiceStopping(false);
              closeTurn();
              note("Voice session ended.");
              break;
            case "listening":
              setVoicePhase("listening");
              closeTurn();
              break;
            case "heard_partial":
              setRequest(frame.text);
              break;
            case "heard":
              setVoicePhase("thinking");
              setRequest(frame.text);
              break;
            case "thinking":
              // Local emits this with the command text, which is the same
              // words `heard` already delivered. Phase only.
              setVoicePhase("thinking");
              break;
            case "answer_partial":
              setAnswer(frame.text);
              break;
            case "answer":
              setAnswer(frame.text);
              break;
            case "speaking":
              // Phase only — never rendered.
              //
              // The local pipeline emits `answer` with the full reply and then
              // `speaking` with a shortened version for the speech synthesiser.
              // Rendering both would show the same reply twice, once truncated.
              setVoicePhase("speaking");
              break;
            case "tool":
              addTool(frame.text);
              break;
            case "tool_result":
              // Previously dropped on the floor: the old transcript had no
              // place to put a result, so a spoken tool call showed the call
              // and never what it returned.
              finishTool(frame.text);
              break;
            case "info":
              note(frame.text);
              break;
            case "error":
              setVoiceStopping(false);
              setVoiceError(frame.text);
              // A tool that failed or timed out is an error *in a turn*, not a
              // dead session — live.py emits both through this one phase. Only
              // treat it as fatal when there is no turn to blame, or the
              // toggle would report the whole session broken because one tool
              // took too long.
              if (voiceTurn.current) {
                patchTurn(voiceTurn.current.id, (message) => ({
                  ...message,
                  error: frame.text,
                }));
              } else {
                setVoicePhase("error");
                note(frame.text);
              }
              break;
            default:
              break;
          }
          break;
        }

        case "pong":
          break;
      }
    },
    [patchTurn],
  );

  /* -- stopping invariants -------------------------------------------- */

  // You cannot be "stopping" something already stopped. This reconciles the
  // flag against the phase no matter which order they arrive in, which is the
  // property that actually prevents a stuck control — the frame-based clear
  // alone depended on a message that might never come.
  useEffect(() => {
    if (voiceStopping && !isVoiceLive(voicePhase)) setVoiceStopping(false);
  }, [voiceStopping, voicePhase]);

  // And if the session really is live but never confirms, give the user their
  // control back rather than leaving it disabled. Reporting honestly that the
  // stop was not acknowledged beats silently pretending it worked.
  useEffect(() => {
    if (!voiceStopping) return;
    const timer = setTimeout(() => {
      setVoiceStopping(false);
      setVoiceError(
        "The voice session did not confirm it stopped. Try again, or reload.",
      );
    }, STOP_CONFIRM_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [voiceStopping]);

  /* -- connection ----------------------------------------------------- */

  const connect = useCallback(() => {
    if (goneRef.current) return;
    if (!getToken()) {
      setConnection("unauthorized");
      return;
    }

    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl());
    } catch {
      setConnection("offline");
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      attemptsRef.current = 0;
      setConnection("open");
      pingTimerRef.current = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ t: "ping" } satisfies ClientFrame));
        }
      }, PING_INTERVAL_MS);
    };

    socket.onmessage = (event) => {
      try {
        handle(JSON.parse(event.data) as ServerFrame);
      } catch {
        // A frame we cannot parse is not worth tearing the socket down for.
      }
    };

    socket.onclose = (event) => {
      if (pingTimerRef.current) clearInterval(pingTimerRef.current);
      socketRef.current = null;
      setBusy(false);

      // Nothing will ever resolve these now.
      for (const pending of runsRef.current.values()) {
        pending.reject(new Error("Connection lost"));
      }
      runsRef.current.clear();
      // The server stops the voice loop when the socket closes, so the UI must
      // not keep claiming it is listening. An in-flight spoken turn is settled
      // rather than left pending — it will never receive its closing frame.
      setVoicePhase("off");
      setVoiceStopping(false);
      const spoken = voiceTurn.current;
      if (spoken) {
        voiceTurn.current = null;
        setMessages((current) =>
          current.map((message) =>
            message.id === spoken.id ? { ...message, pending: false } : message,
          ),
        );
      }

      if (goneRef.current) return;

      if (event.code === POLICY_VIOLATION) {
        setConnection("unauthorized");
        return;
      }

      attemptsRef.current += 1;
      setConnection(attemptsRef.current > 3 ? "offline" : "reconnecting");
      const delay = Math.min(
        RECONNECT_BASE_MS * 2 ** (attemptsRef.current - 1),
        RECONNECT_MAX_MS,
      );
      retryTimerRef.current = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      // `onclose` always follows; reconnect is handled there so it is not
      // scheduled twice.
    };
  }, [handle]);

  useEffect(() => {
    goneRef.current = false;
    captureToken();
    connect();
    return () => {
      goneRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (pingTimerRef.current) clearInterval(pingTimerRef.current);
      socketRef.current?.close();
    };
  }, [connect]);

  /* -- actions -------------------------------------------------------- */

  const send = useCallback((frame: ClientFrame) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(frame));
      return true;
    }
    return false;
  }, []);

  const ask = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;
      seqRef.current += 1;
      const id = `t${seqRef.current}`;
      setMessages((current) => [
        ...current,
        { id: `${id}-user`, role: "user", text: trimmed },
      ]);
      if (!send({ t: "ask", id, text: trimmed })) {
        setMessages((current) => [
          ...current,
          { id: `${id}-err`, role: "assistant", text: "", error: "Not connected." },
        ]);
      }
    },
    [busy, send],
  );

  /**
   * Invoke one skill directly. Resolves with the skill's textual result.
   *
   * Rejects rather than hanging when the socket is down or another request is
   * in flight, so a panel can show a real error instead of a spinner that
   * never stops.
   */
  const run = useCallback(
    (skill: string, args: Record<string, unknown> = {}): Promise<string> => {
      seqRef.current += 1;
      const id = `p${seqRef.current}`;
      return new Promise<string>((resolve, reject) => {
        if (!send({ t: "run", id, skill, args })) {
          reject(new Error("Not connected"));
          return;
        }
        setBusy(true);
        runsRef.current.set(id, { resolve, reject });
      });
    },
    [send],
  );

  const respondToConfirm = useCallback(
    (confirmId: string, approve: boolean) => {
      send({ t: "confirm_reply", confirm_id: confirmId, approve });
      const settle = (confirm: ConfirmView) =>
        confirm.confirmId === confirmId && confirm.status === "pending"
          ? { ...confirm, status: approve ? ("approved" as const) : ("denied" as const) }
          : confirm;

      setMessages((current) =>
        current.map((message) => ({
          ...message,
          confirms: (message.confirms ?? []).map(settle),
        })),
      );
      setPanelConfirms((current) => current.map(settle));
    },
    [send],
  );

  const dismissPanelConfirm = useCallback((confirmId: string) => {
    setPanelConfirms((current) =>
      current.filter((confirm) => confirm.confirmId !== confirmId),
    );
  }, []);

  const cancel = useCallback(() => {
    if (!busy) return;
    setCancelling(true);
    if (!send({ t: "cancel" })) setCancelling(false);
  }, [busy, send]);

  const reset = useCallback(() => {
    if (busy) return;
    if (!send({ t: "reset" })) setMessages([]);
  }, [busy, send]);

  // `mode` is a preference. The server resolves it against what is actually
  // installed and keyed, and answers with a `started` frame naming the
  // pipeline it really ran — which is where `voiceMode` comes from. Setting it
  // here would be a guess the UI then presented as fact.
  const startVoice = useCallback(
    (mode: "local" | "live" | "auto" = "auto") => {
      setVoiceError("");
      setVoiceStopping(false);
      setVoicePhase("starting");
      if (!send({ t: "voice_start", mode })) {
        setVoicePhase("error");
        setVoiceError("Not connected.");
      }
    },
    [send],
  );

  const stopVoice = useCallback(() => {
    // Nothing to stop. Sending anyway used to leave the UI showing
    // "Stopping…" for good: the server had no session to end, so it emitted
    // no `stopped` frame, and the flag that gates the toggle is cleared by
    // that frame.
    if (!isVoiceLive(phaseRef.current)) {
      setVoiceStopping(false);
      return;
    }
    setVoiceStopping(true);
    send({ t: "voice_stop" });
    // Do not flip to "off" here: the server confirms with a `stopped` frame
    // once the loop has actually released the microphone. Claiming it stopped
    // before that would be a lie the user could hear.
  }, [send]);

  // Recovery without tearing the session down by hand. Waits for the server's
  // `stopped` before starting again, so the microphone is actually released
  // first rather than two sessions racing for it.
  const restartVoice = useCallback(
    (mode: "local" | "live" | "auto" = "auto") => {
      const wasLive = isVoiceLive(phaseRef.current);
      if (!wasLive) {
        startVoice(mode);
        return;
      }
      setVoiceStopping(true);
      send({ t: "voice_stop" });
      const started = Date.now();
      const poll = setInterval(() => {
        if (!isVoiceLive(phaseRef.current)) {
          clearInterval(poll);
          startVoice(mode);
        } else if (Date.now() - started > STOP_CONFIRM_TIMEOUT_MS) {
          clearInterval(poll);
          setVoiceStopping(false);
          setVoiceError("Could not restart — the session never confirmed it stopped.");
        }
      }, 250);
    },
    [send, startVoice],
  );

  // A failure outlives the session that caused it — that is the point, so the
  // reason voice stopped does not vanish the instant the phase flips to off.
  // It therefore needs an explicit way to go.
  const dismissVoiceError = useCallback(() => setVoiceError(""), []);

  return {
    connection,
    hello,
    messages,
    busy,
    panelConfirms,
    ask,
    run,
    cancel,
    // Derived, not reconciled. "Cancelling" is only meaningful while a turn is
    // running, so anding it with `busy` makes that an invariant of the value
    // itself rather than something an effect has to keep true. The voice
    // toggle needed a reconciling effect for exactly this and wedged twice
    // before it was right; there is nothing here left to wedge.
    cancelling: cancelling && busy,
    jobEvents,
    routineEvents,
    respondToConfirm,
    dismissPanelConfirm,
    reset,
    voicePhase,
    voiceMode,
    voiceError,
    voiceStopping,
    voiceNotes,
    startVoice,
    stopVoice,
    restartVoice,
    dismissVoiceError,
  };
}
