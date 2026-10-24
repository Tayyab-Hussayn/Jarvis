/**
 * Pure logic for the Phones panel, kept free of React so it can be tested
 * with `node --test` (there is no browser test runner here).
 */

/** The `offer` object from `POST /api/phone/offer`. Field set is the app's contract. */
export interface PhoneOffer {
  v: number;
  service: string;
  host: string;
  port: number;
  code: string;
  expires_at: number;
  /** "https" under `serve --lan` TLS; absent from older servers. */
  scheme?: "http" | "https";
  /** SHA-256 of the server certificate (64 lowercase hex); https only. */
  cert_sha256?: string;
}

/** `ab12 cd34 … 9f0e`: enough to compare by eye against the app, never the full pin. */
export function shortFingerprint(hex: string): string {
  return hex.length < 16 ? hex : `${hex.slice(0, 8).match(/.{4}/g)!.join(" ")} … ${hex.slice(-4)}`;
}

export interface PhoneDevice {
  device_id: string;
  name: string;
  paired_at: string;
  last_seen: string;
  connected: boolean;
  capabilities: string[];
}

/**
 * The QR payload is the offer object, verbatim. `PairingOfferParser` in the
 * app rejects unknown or missing fields, so nothing is added, renamed or dropped.
 */
export function offerPayload(offer: PhoneOffer): string {
  return JSON.stringify(offer);
}

/** Whole seconds until `expires_at` (unix seconds), never negative. */
export function secondsLeft(expiresAt: number, nowMs: number): number {
  return Math.max(0, Math.ceil(expiresAt - nowMs / 1000));
}

export type OfferState = "none" | "unreachable" | "live" | "expired";

/**
 * What the panel shows for the current offer. An unreachable offer never
 * renders a QR: a loopback address scans fine and can never connect.
 */
export function offerState(
  offer: PhoneOffer | null,
  reachable: boolean,
  nowMs: number,
): OfferState {
  if (!offer) return "none";
  if (!reachable) return "unreachable";
  return secondsLeft(offer.expires_at, nowMs) > 0 ? "live" : "expired";
}

export function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
