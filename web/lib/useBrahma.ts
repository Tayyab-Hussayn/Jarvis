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
  ConfirmView,
  Message,
  ServerFrame,
  ToolCallView,
  VoiceLine,
  VoicePhase,
} from "./protocol";
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

export function useBrahma() {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [hello, setHello] = useState<Hello | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [panelConfirms, setPanelConfirms] = useState<ConfirmView[]>([]);
  // Voice state is deliberately separate from `messages`. The spoken
  // conversation runs on its own Agent server-side, so mixing the two
  // transcripts would misrepresent both.
  const [voicePhase, setVoicePhase] = useState<VoicePhase>("off");
  const [voiceLines, setVoiceLines] = useState<VoiceLine[]>([]);
  const [voiceMode, setVoiceMode] = useState<"local" | "live">("local");
  const [voiceError, setVoiceError] = useState("");
  // Stopping is not instant — the loop may be mid-utterance and only releases
  // the microphone when it unwinds. Without this the UI would look frozen
  // between the click and the server's `stopped` frame.
  const [voiceStopping, setVoiceStopping] = useState(false);
  // The utterance currently being transcribed. Held apart from `voiceLines`
  // so each delta replaces it instead of appending — streaming transcription
  // arrives a syllable at a time, which previously produced one line per
  // fragment.
  const [voicePartial, setVoicePartial] = useState<VoiceLine | null>(null);
  const voiceSeq = useRef(0);
  // Ids of the lines currently being streamed into, so deltas rewrite them
  // rather than appending a new line each time.
  const openUser = useRef<number | null>(null);
  const openBrahma = useRef<number | null>(null);
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
          liveTurns.current.delete(frame.id);
          patchTurn(frame.id, (message) => ({
            ...message,
            pending: false,
            error: frame.error || undefined,
            iterations: frame.iterations,
          }));
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
          break;

        case "voice_state": {
          const push = (role: VoiceLine["role"], text: string) => {
            if (!text.trim()) return;
            voiceSeq.current += 1;
            setVoiceLines((current) =>
              [...current, { id: voiceSeq.current, role, text }].slice(-80),
            );
          };

          // An in-progress line lives in `voiceLines` from its first delta and
          // is rewritten in place, rather than being tacked on at the end when
          // it finishes. Appending on completion put the user's sentence
          // *after* the reply it prompted, because input transcription lags
          // the audio and only completes at the turn boundary.
          const stream = (role: "you" | "brahma", text: string, final: boolean) => {
            const slot = role === "you" ? openUser : openBrahma;
            if (!text.trim()) {
              if (final) slot.current = null;
              return;
            }
            if (slot.current === null) {
              voiceSeq.current += 1;
              slot.current = voiceSeq.current;
              const id = slot.current;
              setVoiceLines((current) =>
                [...current, { id, role, text }].slice(-80),
              );
            } else {
              const id = slot.current;
              setVoiceLines((current) =>
                current.map((line) =>
                  line.id === id ? { ...line, text } : line,
                ),
              );
            }
            if (final) slot.current = null;
          };
          switch (frame.phase) {
            case "started":
              setVoicePhase("idle");
              setVoiceError("");
              push("note", `Voice session started (${frame.text}).`);
              break;
            case "stopped":
              setVoicePhase("off");
              setVoiceStopping(false);
              setVoicePartial(null);
              openUser.current = null;
              openBrahma.current = null;
              push("note", "Voice session ended.");
              break;
            case "listening":
              setVoicePhase("listening");
              break;
            case "started":
              break;
            case "heard_partial":
              stream("you", frame.text, false);
              break;
            case "heard":
              setVoicePhase("thinking");
              stream("you", frame.text, true);
              break;
            case "answer_partial":
              stream("brahma", frame.text, false);
              break;
            case "answer":
              stream("brahma", frame.text, true);
              break;
            case "speaking":
              // Phase only — never rendered as a line.
              //
              // The local pipeline emits `answer` with the full reply and then
              // `speaking` with a shortened version for the speech synthesiser.
              // Rendering both would show the same reply twice, once truncated.
              // Live now emits `answer`/`answer_partial` of its own, so there
              // is nothing left for this branch to carry.
              setVoicePhase("speaking");
              break;
            case "tool":
              push("tool", frame.text);
              break;
            case "info":
              push("note", frame.text);
              break;
            case "error":
              setVoicePhase("error");
              setVoiceStopping(false);
              setVoiceError(frame.text);
              push("note", frame.text);
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
      // not keep claiming it is listening.
      setVoicePhase("off");
      setVoiceStopping(false);

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

  const reset = useCallback(() => {
    if (busy) return;
    if (!send({ t: "reset" })) setMessages([]);
  }, [busy, send]);

  const startVoice = useCallback(
    (mode: "local" | "live" = "local") => {
      setVoiceMode(mode);
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

  const clearVoiceLines = useCallback(() => {
    setVoiceLines([]);
    setVoicePartial(null);
    openUser.current = null;
    openBrahma.current = null;
  }, []);

  return {
    connection,
    hello,
    messages,
    busy,
    panelConfirms,
    ask,
    run,
    respondToConfirm,
    dismissPanelConfirm,
    reset,
    voicePhase,
    voiceLines,
    voiceMode,
    voiceError,
    voiceStopping,
    voicePartial,
    startVoice,
    stopVoice,
    clearVoiceLines,
  };
}
