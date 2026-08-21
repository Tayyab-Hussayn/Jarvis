"use client";

/**
 * Configures the *assistant*. Control configures the machine.
 *
 * Key material never reaches this page — the server redacts it to `***`, and
 * `_deep_merge` drops that placeholder on write so saving cannot overwrite a
 * real key with the mask.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/session";
import { Button, Empty, Field, Panel } from "./ui";
import { Screen } from "./Shell";

type Settings = Record<string, unknown>;

function flatten(value: unknown, prefix = ""): [string, string][] {
  if (value === null || value === undefined) return [[prefix, "—"]];
  if (typeof value !== "object") return [[prefix, String(value)]];
  if (Array.isArray(value)) return [[prefix, value.length ? value.join(", ") : "—"]];
  return Object.entries(value as Settings).flatMap(([key, inner]) =>
    flatten(inner, prefix ? `${prefix}.${key}` : key),
  );
}

export function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api<{ settings: Settings }>("/api/settings");
      setSettings(data.settings);
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <Screen title="Settings">
        <Empty>Could not read settings: {error}</Empty>
      </Screen>
    );
  }
  if (!settings) {
    return (
      <Screen title="Settings">
        <Empty>Loading…</Empty>
      </Screen>
    );
  }

  const groups = Object.entries(settings).filter(
    ([, value]) => typeof value === "object" && value !== null && !Array.isArray(value),
  );
  const scalars = Object.entries(settings).filter(
    ([, value]) => typeof value !== "object" || value === null || Array.isArray(value),
  );

  return (
    <Screen
      title="Settings"
      subtitle="How the assistant itself is configured. Edited at ~/.config/brahma-echo/settings.json."
      actions={<Button onClick={() => void load()}>Reload</Button>}
    >
      <div className="grid gap-4 overflow-y-auto pb-6 lg:grid-cols-2">
        {scalars.length > 0 && (
          <Panel title="General">
            <dl className="space-y-1.5">
              {scalars.flatMap(([key, value]) =>
                flatten(value, key).map(([path, shown]) => (
                  <Field key={path} label={path} value={shown} />
                )),
              )}
            </dl>
          </Panel>
        )}

        {groups.map(([group, value]) => (
          <Panel key={group} title={group}>
            <dl className="space-y-1.5">
              {flatten(value).map(([path, shown]) => (
                <Field
                  key={path}
                  label={path}
                  value={shown}
                  tone={shown === "***" ? "var(--moderate)" : undefined}
                />
              ))}
            </dl>
          </Panel>
        ))}

        <Panel title="Editing" className="lg:col-span-2">
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
            This screen is read-only for now. Values marked{" "}
            <span style={{ color: "var(--moderate)" }}>***</span> are credentials
            the server refuses to send to the browser at all. Edit
            settings.json directly, or ask Brahma in the Console — it has skills
            for the model and provider.
          </p>
        </Panel>
      </div>
    </Screen>
  );
}
