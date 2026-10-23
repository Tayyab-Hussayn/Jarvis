import qrcode from "qrcode-generator";

/**
 * Build the QR module matrix in the browser. No network, no external service:
 * the pairing code is a credential and must not leave this page.
 * Error correction M is a phone-camera-over-a-laptop-screen compromise.
 */
export function qrMatrix(text: string): boolean[][] {
  const qr = qrcode(0, "M");   // type 0 = pick the smallest version that fits
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, r) =>
    Array.from({ length: n }, (_, c) => qr.isDark(r, c)),
  );
}
