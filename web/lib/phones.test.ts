import assert from "node:assert/strict";
import { test } from "node:test";

import { formatCountdown, offerPayload, offerState, secondsLeft, shortFingerprint, type PhoneOffer } from "./phones.ts";
import { qrMatrix } from "./qr.ts";

const offer: PhoneOffer = {
  v: 1, service: "kravein", host: "192.168.1.20", port: 8787,
  code: "abc_DEF-123", expires_at: 1_000_120,
};

test("QR payload is exactly the offer JSON", () => {
  assert.deepEqual(JSON.parse(offerPayload(offer)), offer);
  assert.deepEqual(Object.keys(JSON.parse(offerPayload(offer))).sort(),
    ["code", "expires_at", "host", "port", "service", "v"]);
});

test("a TLS offer's QR payload still equals the offer, fingerprint included", () => {
  const tls: PhoneOffer = { ...offer, scheme: "https", cert_sha256: "ab".repeat(32) };
  assert.deepEqual(JSON.parse(offerPayload(tls)), tls);
  assert.equal(shortFingerprint(tls.cert_sha256!), "abab abab … abab");
});

test("countdown and expiry", () => {
  assert.equal(secondsLeft(offer.expires_at, 1_000_000_000), 120);
  assert.equal(secondsLeft(offer.expires_at, 1_000_119_500), 1);
  assert.equal(secondsLeft(offer.expires_at, 1_000_121_000), 0);
  assert.equal(formatCountdown(75), "1:15");
  assert.equal(offerState(offer, true, 1_000_000_000), "live");
  assert.equal(offerState(offer, true, 1_000_120_000), "expired");
  assert.equal(offerState(null, true, 0), "none");
});

test("an unreachable offer never reaches 'live', so no QR is drawn", () => {
  assert.equal(offerState(offer, false, 1_000_000_000), "unreachable");
});

test("QR matrix is square and holds a real offer", () => {
  const m = qrMatrix(offerPayload(offer));
  assert.ok(m.length >= 29 && m.every((row) => row.length === m.length));
  assert.ok(m.flat().some(Boolean) && m.flat().some((x) => !x));
});
