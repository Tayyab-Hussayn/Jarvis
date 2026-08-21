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
} from "./protocol";
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

  const socketRef = useRef<WebSocket | null>(null);
  const attemptsRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const seqRef = useRef(0);
  const runsRef = useRef(new Map<string, PendingRun>());
  const goneRef = useRef(false);

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

        case "confirm_request":
          patchTurn(frame.id, (message) => ({
            ...message,
            confirms: [
              ...(message.confirms ?? []),
              {
                confirmId: frame.confirm_id,
                action: frame.action,
                detail: frame.detail,
                danger: frame.danger,
                expiresAt: Date.now() + frame.timeout * 1000,
                status: "pending",
              } satisfies ConfirmView,
            ],
          }));
          break;

        case "confirm_expired":
          patchTurn(frame.id, (message) => ({
            ...message,
            confirms: (message.confirms ?? []).map((confirm) =>
              confirm.confirmId === frame.confirm_id && confirm.status === "pending"
                ? { ...confirm, status: "expired" }
                : confirm,
            ),
          }));
          break;

        case "answer":
          patchTurn(frame.id, (message) => ({ ...message, text: frame.text }));
          break;

        case "turn_end":
          setBusy(false);
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

        case "pong":
          break;
      }
    },
    [patchTurn],
  );

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
  };
}
