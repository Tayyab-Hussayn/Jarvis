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
