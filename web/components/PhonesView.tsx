"use client";

/**
 * Pair and manage phones.
 *
 * The pairing offer is a credential: it is drawn in this page only, from the
 * server's own response, and never sent anywhere. An offer the phone cannot
 * reach (loopback host) is shown as an instruction, never as a QR that scans
 * fine and then can never connect.
 *
 * Every request ends in a visible state: `finally` clears each busy flag, so
 * a failed call cannot leave a button spinning.
 */

import { useEffect, useMemo, useState } from "react";

import { formatCountdown, offerPayload, offerState, secondsLeft, shortFingerprint, type PhoneDevice, type PhoneOffer } from "@/lib/phones";
import { qrMatrix } from "@/lib/qr";
import { api } from "@/lib/session";
import { useResource } from "@/lib/useApi";
import { Button, Empty, Field, Panel, SectionTitle, Spinner } from "./ui";
import { Screen } from "./Shell";

const QUIET = 4;   // modules of white border the QR spec requires

function QrCode({ text }: { text: string }) {
  const matrix = useMemo(() => qrMatrix(text), [text]);
  const size = matrix.length + QUIET * 2;
  const path = useMemo(() => {
    const parts: string[] = [];
    matrix.forEach((row, r) =>
      row.forEach((dark, c) => {
        if (dark) parts.push(`M${c + QUIET} ${r + QUIET}h1v1h-1z`);
      }),
    );
    return parts.join("");
  }, [matrix]);
  return (
    // White ground in every theme: scanners need dark-on-light.
    <svg
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      role="img"
      aria-label="Pairing QR code"
      className="h-56 w-56 rounded-[var(--radius-md)]"
      style={{ background: "#fff" }}
    >
      <path d={path} fill="#000" />
    </svg>
  );
}

function errText(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

export function PhonesView() {
  const [offer, setOffer] = useState<PhoneOffer | null>(null);
  const [reachable, setReachable] = useState(true);
  const [offerBusy, setOfferBusy] = useState(false);
  const [offerError, setOfferError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [removing, setRemoving] = useState<PhoneDevice | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [revokeError, setRevokeError] = useState("");

  const state = offerState(offer, reachable, now);
  // Poll while a QR is on screen so the new phone appears without a reload.
  const devices = useResource<{ devices: PhoneDevice[] }>("/api/phones", state === "live" ? 2000 : 0);

  useEffect(() => {
    if (state !== "live") return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [state]);

  async function newOffer() {
    setOfferBusy(true);
    setOfferError("");
    try {
      const res = await api<{ offer: PhoneOffer; reachable_from_lan: boolean }>("/api/phone/offer", {
        method: "POST",
      });
      setOffer(res.offer);
      setReachable(res.reachable_from_lan);
      setNow(Date.now());
    } catch (exc) {
      setOfferError(errText(exc));
    } finally {
      setOfferBusy(false);
    }
  }

  async function revoke(device: PhoneDevice) {
    setRevokeBusy(true);
    setRevokeError("");
    try {
      await api(`/api/phones/${encodeURIComponent(device.device_id)}`, { method: "DELETE" });
      setRemoving(null);
      devices.reload();
    } catch (exc) {
      setRevokeError(errText(exc));
    } finally {
      setRevokeBusy(false);
    }
  }

  const list = devices.data?.devices ?? [];

  return (
    <Screen subtitle="Pair the Kravein Connect app and manage the phones Kravein can control.">
      <div className="flex flex-col gap-6">
        <Panel
          title="Pair a phone"
          actions={
            <Button variant="primary" onClick={() => void newOffer()} disabled={offerBusy}>
              {offerBusy ? "Working…" : state === "none" ? "Pair a phone" : "New code"}
            </Button>
          }
        >
          {offerError && (
            <p role="alert" className="mb-3 text-[length:var(--text-sm)] text-[var(--critical)]">
              Could not create a pairing code: {offerError}
            </p>
          )}
          {state === "none" && (
            <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
              Press “Pair a phone”, then scan the code with the Kravein Connect app. A code lasts two
              minutes and works once.
            </p>
          )}
          {state === "unreachable" && (
            <p role="alert" className="text-[length:var(--text-sm)] text-[var(--critical)]">
              This server is only listening on this computer, so a phone cannot reach it. Restart it
              with <code className="font-[family-name:var(--font-mono)]">kravein serve --lan --host &lt;wifi-ip&gt;</code>{" "}
              and try again.
            </p>
          )}
          {(state === "live" || state === "expired") && offer && (
            <div className="flex flex-wrap items-start gap-6">
              <div className="relative">
                <div style={state === "expired" ? { opacity: 0.12, filter: "grayscale(1)" } : undefined}>
                  <QrCode text={offerPayload(offer)} />
                </div>
                {state === "expired" && (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <Button variant="primary" onClick={() => void newOffer()} disabled={offerBusy}>
                      New code
                    </Button>
                  </div>
                )}
              </div>
              <dl className="flex min-w-[16rem] flex-1 flex-col gap-2">
                <p className="text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  Or enter these in the app by hand:
                </p>
                <Field label="Host" value={offer.host} />
                <Field label="Port" value={String(offer.port)} />
                <Field label="Code" value={offer.code} />
                {offer.cert_sha256 && (
                  <Field label="Certificate" value={shortFingerprint(offer.cert_sha256)} />
                )}
                <Field
                  label="Expires in"
                  value={state === "live" ? formatCountdown(secondsLeft(offer.expires_at, now)) : "expired"}
                  tone={state === "expired" ? "var(--critical)" : undefined}
                />
              </dl>
            </div>
          )}
        </Panel>

        <section>
          <SectionTitle count={list.length}>Paired phones</SectionTitle>
          {devices.error && (
            <p role="alert" className="mb-3 text-[length:var(--text-sm)] text-[var(--critical)]">
              Could not load phones: {devices.error}{" "}
              <Button variant="ghost" onClick={devices.reload}>Retry</Button>
            </p>
          )}
          {revokeError && (
            <p role="alert" className="mb-3 text-[length:var(--text-sm)] text-[var(--critical)]">
              Could not remove the phone: {revokeError}
            </p>
          )}
          {devices.loading && !devices.data ? (
            <Spinner label="Loading…" />
          ) : list.length === 0 && !devices.error ? (
            <Empty>No phones paired yet.</Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {list.map((device) => (
                <li key={device.device_id}>
                  <Panel>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-[length:var(--text-sm)] font-medium">
                          {device.name || "Unnamed phone"}
                        </p>
                        <p className="truncate font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] text-[var(--text-faint)]">
                          {device.device_id} · {device.connected ? "connected" : `last seen ${device.last_seen || "never"}`}
                        </p>
                      </div>
                      {removing?.device_id === device.device_id ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[length:var(--text-sm)]">
                            Remove “{device.name || device.device_id}”? It will be disconnected.
                          </span>
                          <Button variant="danger" onClick={() => void revoke(device)} disabled={revokeBusy}>
                            {revokeBusy ? "Removing…" : "Remove"}
                          </Button>
                          <Button variant="ghost" onClick={() => setRemoving(null)} disabled={revokeBusy}>
                            Cancel
                          </Button>
                        </div>
                      ) : (
                        <Button
                          variant="ghost"
                          onClick={() => {
                            setRevokeError("");
                            setRemoving(device);
                          }}
                        >
                          Remove
                        </Button>
                      )}
                    </div>
                  </Panel>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Screen>
  );
}
