"use client";

/**
 * Configures the *assistant*. Control configures the machine.
 *
 * This screen used to be read-only, which quietly undermined the whole
 * premise — an assistant you cannot configure from its own interface is not
 * one you are in control of. Scalar values are now editable in place.
 *
 * Key material never reaches the browser: the server redacts it to `***`, and
 * `_deep_merge` drops that placeholder on write so saving cannot overwrite a
 * real key with the mask. Redacted fields are rendered locked for the same
 * reason — there is nothing here to edit, only something to destroy.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/session";
import { Button, Chip, Empty, Input, Panel, SectionTitle } from "./ui";
import { Screen } from "./Shell";

type Json = string | number | boolean | null | Json[] | { [k: string]: Json };
type Settings = Record<string, Json>;

const ACRONYMS = new Set([
  "tts", "stt", "llm", "cpu", "gpu", "api", "url", "id", "ui", "os", "ip",
  "cli", "ttl", "vad",
]);

/** `breaker_cooldown_seconds` -> `Breaker cooldown seconds`, `tts_voice` -> `TTS voice`. */
function humanise(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .trim()
    .split(" ")
    .map((word, index) => {
      if (ACRONYMS.has(word.toLowerCase())) return word.toUpperCase();
      return index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word;
    })
    .join(" ");
}

const GROUP_NAMES: Record<string, string> = {
  llm: "Language model",
  stt: "Speech to text",
  tts: "Text to speech",
  ui: "Interface",
};

function groupName(key: string): string {
  return GROUP_NAMES[key] ?? humanise(key);
}

/**
 * Settings whose values are a fixed set rather than free text.
 *
 * This screen is otherwise deliberately generic — it renders whatever keys the
 * server sends, so a new setting needs no frontend change. An enum is the one
 * case where that is not good enough: a typo in a free-text field is accepted,
 * saved, and then silently ignored by whatever reads it. Keyed by dotted path,
 * because the *consumer* of the setting is what knows its domain.
 */
const CHOICES: Record<string, { value: string; label: string; hint: string }[]> = {
  "voice.mode": [
    {
      value: "auto",
      label: "Auto",
      hint: "Recommended — Live first, falling back to on-device if there is no Gemini key",
    },
    {
      value: "live",
      label: "Live only",
      hint: "Gemini native audio — ~7s to first word, metered. Refuses to start without a key rather than falling back",
    },
    {
      value: "local",
      label: "On-device only",
      hint: "Runs entirely on this machine — free, ~19s to first word, an utterance at a time",
    },
  ],
};

interface Row {
  path: string[];
  key: string;
  value: Json;
}

function ChoiceRow({
  row,
  choices,
  onSave,
  saving,
}: {
  row: Row;
  choices: { value: string; label: string; hint: string }[];
  onSave: (path: string[], value: Json) => void;
  saving: boolean;
}) {
  const current = String(row.value ?? "");
  const active = choices.find((c) => c.value === current);
  return (
    <div className="py-2">
      <div className="flex items-center justify-between gap-4">
        <span className="shrink-0 text-[length:var(--text-sm)] text-[var(--text-muted)]">
          {humanise(row.key)}
        </span>
        <div className="flex flex-wrap justify-end gap-1">
          {choices.map((choice) => (
            <button
              key={choice.value}
              type="button"
              disabled={saving}
              title={choice.hint}
              onClick={() => onSave(row.path, choice.value)}
              className="rounded-[var(--radius-sm)] border px-2 py-0.5 text-[length:var(--text-xs)] disabled:opacity-50"
              style={{
                transition: "all var(--motion-fast) var(--ease)",
                borderColor:
                  choice.value === current ? "var(--accent)" : "var(--border-strong)",
                background:
                  choice.value === current ? "var(--accent-dim)" : "transparent",
                color: choice.value === current ? "var(--accent)" : "var(--text-muted)",
              }}
            >
              {choice.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-right text-[length:var(--text-xs)] text-[var(--text-faint)]">
        {/* A value saved by hand that is not one of the choices must not look
            like one of them is selected. */}
        {active ? active.hint : `“${current}” is not a recognised value.`}
      </p>
    </div>
  );
}

/** Flatten to editable leaves, keeping the path so a patch can be rebuilt. */
function leaves(value: Json, path: string[] = []): Row[] {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([k, v]) => leaves(v, [...path, k]));
  }
  return [{ path, key: path[path.length - 1] ?? "", value }];
}

/** Rebuild a nested patch object from a dotted path. */
function nest(path: string[], value: Json): Settings {
  return path.reduceRight<Json>((acc, key) => ({ [key]: acc }), value) as Settings;
}

function displayValue(value: Json): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function EditableRow({
  row,
  onSave,
  saving,
}: {
  row: Row;
  onSave: (path: string[], value: Json) => void;
  saving: boolean;
}) {
  const original = displayValue(row.value);
  const [draft, setDraft] = useState(original);
  const [editing, setEditing] = useState(false);

  useEffect(() => setDraft(original), [original]);

  const locked = row.value === "***";
  const isBool = typeof row.value === "boolean";
  const isList = Array.isArray(row.value);
  const dirty = draft !== original;

  const commit = () => {
    if (!dirty) {
      setEditing(false);
      return;
    }
    let next: Json = draft;
    if (typeof row.value === "number") {
      const parsed = Number(draft);
      next = Number.isNaN(parsed) ? draft : parsed;
    } else if (isList) {
      next = draft.split(",").map((v) => v.trim()).filter(Boolean);
    }
    onSave(row.path, next);
    setEditing(false);
  };

  return (
    <div className="flex items-center justify-between gap-4 py-1.5">
      <span className="min-w-0 shrink-0 text-[length:var(--text-sm)] text-[var(--text-muted)]">
        {humanise(row.key)}
      </span>

      {locked ? (
        <span
          className="text-[length:var(--text-xs)]"
          style={{ color: "var(--moderate)" }}
          title="Stored on disk; never sent to the browser"
        >
          set · hidden
        </span>
      ) : isBool ? (
        <button
          type="button"
          disabled={saving}
          onClick={() => onSave(row.path, !row.value)}
          role="switch"
          aria-checked={row.value === true}
          aria-label={humanise(row.key)}
          className="relative h-5 w-9 shrink-0 rounded-[var(--radius-full)] disabled:opacity-50"
          style={{
            background: row.value ? "var(--accent)" : "var(--surface-sunken)",
            border: "1px solid var(--border-strong)",
            transition: "background var(--motion-fast) var(--ease)",
          }}
        >
          <span
            className="absolute top-1/2 block h-3.5 w-3.5 -translate-y-1/2 rounded-full"
            style={{
              left: row.value ? "calc(100% - 1.05rem)" : "0.15rem",
              background: row.value ? "var(--accent-contrast)" : "var(--text-faint)",
              transition: "left var(--motion-fast) var(--ease)",
            }}
          />
        </button>
      ) : editing ? (
        <span className="flex min-w-0 flex-1 items-center justify-end gap-2">
          <input
            autoFocus
            value={draft}
            disabled={saving}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setDraft(original);
                setEditing(false);
              }
            }}
            className="w-full max-w-xs rounded-[var(--radius-sm)] border border-[var(--accent)] bg-[var(--surface-sunken)] px-2 py-1 text-right font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] outline-none"
          />
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="Click to edit"
          className="min-w-0 truncate rounded-[var(--radius-sm)] border border-transparent px-2 py-0.5 text-right font-[family-name:var(--font-mono)] text-[length:var(--text-xs)] hover:border-[var(--border-strong)] hover:bg-[var(--surface-sunken)]"
          style={{
            transition: "all var(--motion-fast) var(--ease)",
            // An unset value rendered as blank space made the row look broken.
            color: original ? "var(--text)" : "var(--text-faint)",
          }}
        >
          {original || "not set"}
        </button>
      )}
    </div>
  );
}

export function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [search, setSearch] = useState("");

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

  const save = useCallback(
    async (path: string[], value: Json) => {
      setSaving(true);
      try {
        const data = await api<{ settings: Settings }>("/api/settings", {
          method: "POST",
          body: JSON.stringify(nest(path, value)),
        });
        setSettings(data.settings);
        setSaved(path.join("."));
        setError("");
        setTimeout(() => setSaved(""), 2000);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const groups = useMemo(() => {
    if (!settings) return [];
    const query = search.trim().toLowerCase();
    const out: { name: string; key: string; rows: Row[] }[] = [];

    const scalars = Object.entries(settings).filter(
      ([, v]) => v === null || typeof v !== "object" || Array.isArray(v),
    );
    if (scalars.length) {
      out.push({
        name: "General",
        key: "general",
        rows: scalars.flatMap(([k, v]) => leaves(v, [k])),
      });
    }
    for (const [k, v] of Object.entries(settings)) {
      if (v !== null && typeof v === "object" && !Array.isArray(v)) {
        out.push({ name: groupName(k), key: k, rows: leaves(v, [k]) });
      }
    }
    if (!query) return out;
    return out
      .map((g) => ({
        ...g,
        rows: g.rows.filter(
          (r) =>
            r.key.toLowerCase().includes(query) ||
            g.name.toLowerCase().includes(query),
        ),
      }))
      .filter((g) => g.rows.length > 0);
  }, [settings, search]);

  if (error && !settings) {
    return (
      <Screen>
        <Empty>Could not read settings: {error}</Empty>
      </Screen>
    );
  }
  if (!settings) {
    return (
      <Screen>
        <Empty>Loading…</Empty>
      </Screen>
    );
  }

  return (
    <Screen
      subtitle="Stored at ~/.config/kravein/settings.json. Changes save immediately."
      actions={
        <>
          {saved && (
            <Chip active tone="var(--accent)">
              saved {humanise(saved.split(".").pop() ?? "")}
            </Chip>
          )}
          <Input
            value={search}
            onChange={setSearch}
            placeholder="Search settings…"
            className="w-52"
          />
          <Button onClick={() => void load()}>Reload</Button>
        </>
      }
    >
      <div className="">
        {error && (
          <div
            className="mb-4 rounded-[var(--radius)] border-l-2 px-3 py-2 text-[length:var(--text-sm)]"
            style={{
              borderLeftColor: "var(--critical)",
              background: "var(--critical-dim)",
              color: "var(--critical)",
            }}
          >
            {error}
          </div>
        )}

        {groups.length === 0 ? (
          <Empty>Nothing matches “{search}”.</Empty>
        ) : (
          // `items-start` so a 2-row panel does not stretch to match a
          // 14-row one beside it, which left a large empty box.
          <div className="grid items-start gap-4 lg:grid-cols-2">
            {groups.map((group) => (
              <Panel key={group.key} title={group.name}>
                <div className="divide-y divide-[var(--border)]">
                  {group.rows.map((row) => {
                    const choices = CHOICES[row.path.join(".")];
                    return choices ? (
                      <ChoiceRow
                        key={row.path.join(".")}
                        row={row}
                        choices={choices}
                        onSave={save}
                        saving={saving}
                      />
                    ) : (
                      <EditableRow
                        key={row.path.join(".")}
                        row={row}
                        onSave={save}
                        saving={saving}
                      />
                    );
                  })}
                </div>
              </Panel>
            ))}
          </div>
        )}

        <div className="mt-4">
          <SectionTitle>Notes</SectionTitle>
          <p className="text-[length:var(--text-sm)] text-[var(--text-muted)]">
            Values shown as{" "}
            <span style={{ color: "var(--moderate)" }}>set · hidden</span> are
            credentials the server refuses to send to the browser. Editing them
            here is not possible by design; change them in{" "}
            <code className="font-[family-name:var(--font-mono)]">
              ~/.config/kravein/api_keys.json
            </code>
            . Some changes — provider and model in particular — take effect on the
            next request.
          </p>
        </div>
      </div>
    </Screen>
  );
}
