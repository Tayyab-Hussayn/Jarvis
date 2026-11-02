import type { ConfirmView } from "./protocol";

export type ConfirmOutcome = "approved" | "denied" | "expired";

/**
 * Close a card the server says was resolved, whoever answered. Only a pending
 * card changes, so the click that resolved it locally is never overwritten,
 * and a late or repeated frame is harmless.
 */
export function settleConfirm(
  confirm: ConfirmView,
  confirmId: string,
  outcome: ConfirmOutcome,
  by: string,
): ConfirmView {
  if (confirm.confirmId !== confirmId || confirm.status !== "pending") return confirm;
  return { ...confirm, status: outcome, by };
}

/** The line a closed card shows: "Approved on phone", "Declined", "Timed out". */
export function outcomeLabel(confirm: ConfirmView): string {
  const where = confirm.by === "phone" ? " on phone" : "";
  switch (confirm.status) {
    case "approved":
      return `Approved${where}`;
    case "denied":
      return confirm.by === "phone" ? "Declined on phone" : "Declined";
    case "expired":
      return "Timed out — treated as denied";
    default:
      return "";
  }
}

/**
 * The cards the always-on-top overlay must show.
 *
 * Panel-run confirmations always live there. Chat confirmations normally sit
 * inline in the stream, but a pane covers the stream, and a hidden card parks
 * the worker thread until the 120 s timeout denies. So while a pane is open
 * every still-pending inline card is surfaced in the overlay as well. Resolved
 * inline cards are not: the stream shows how they ended.
 */
export function overlayConfirms(
  panelConfirms: ConfirmView[],
  messages: { confirms?: ConfirmView[] }[],
  paneOpen: boolean,
): ConfirmView[] {
  if (!paneOpen) return panelConfirms;
  const seen = new Set(panelConfirms.map((c) => c.confirmId));
  const inline: ConfirmView[] = [];
  for (const message of messages) {
    for (const confirm of message.confirms ?? []) {
      if (confirm.status === "pending" && !seen.has(confirm.confirmId)) {
        seen.add(confirm.confirmId);
        inline.push(confirm);
      }
    }
  }
  return [...panelConfirms, ...inline];
}
