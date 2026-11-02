"use client";

/**
 * Settings → "Always-approved commands".
 *
 * Lists the rules created by a confirmation card's "Always allow" and lets the
 * owner revoke them. Read and delete only: the server has no create endpoint on
 * purpose, so the only way to grant one is to read a card.
 *
 * A revoke is removed from the list only after the server says it worked; a
 * failed one keeps the row and says why, so the page never claims a rule is
 * gone while it still applies.
 */

import { useCallback, useEffect, useState } from "react";

import { formatWhen, parseRules, usesLabel, withoutRule, type SafetyRule } from "@/lib/rules";
import { api } from "@/lib/session";
import { Button, Empty, Panel } from "./ui";

export function AlwaysRules() {
  const [rules, setRules] = useState<SafetyRule[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      setRules(parseRules(await api<unknown>("/api/safety/rules")));
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = useCallback(
    async (id: string) => {
      setBusy(id);
      try {
        await api<unknown>(`/api/safety/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
        setRules((current) => (current ? withoutRule(current, id) : current));
        setError("");
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
        // The row may already be gone (revoked in another tab): resync.
        void load();
      } finally {
        setBusy("");
      }
    },
    [load],
  );

  return (
    <Panel
      title="Always-approved commands"
      actions={<Button onClick={() => void load()}>Reload</Button>}
    >
      {error && (
        <p
          className="mb-2 text-[length:var(--text-sm)]"
          style={{ color: "var(--critical)" }}
          role="alert"
        >
          {error}
        </p>
      )}
      {rules === null ? (
        <Empty>{error ? "Could not read the rules." : "Loading…"}</Empty>
      ) : rules.length === 0 ? (
        <Empty>
          No commands are always approved. Choose “Always allow” on a confirmation card to add one.
        </Empty>
      ) : (
        <ul className="divide-y divide-[var(--border)]" data-always-rules="">
          {rules.map((rule) => (
            <li
              key={rule.id}
              className="flex flex-col gap-2 py-2.5 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0">
                <code className="break-all font-[family-name:var(--font-mono)] text-[length:var(--text-sm)]">
                  {rule.signature}
                </code>
                <p className="mt-0.5 text-[length:var(--text-xs)] text-[var(--text-faint)]">
                  added {formatWhen(rule.created_at)} · last used {formatWhen(rule.last_used)} ·{" "}
                  {usesLabel(rule.uses)}
                </p>
              </div>
              <Button
                variant="danger"
                disabled={busy === rule.id}
                onClick={() => void revoke(rule.id)}
                title={`Ask again before running: ${rule.signature}`}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
