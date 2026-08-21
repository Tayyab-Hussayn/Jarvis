"use client";

/**
 * Voice chain status.
 *
 * Read-only for now: starting a voice session from the browser would need the
 * page's own microphone, which is a separate design problem from what the
 * local `brahma voice` pipeline already does well. What this screen is for is
 * answering "is voice going to work, and which path will it take" before you
 * commit to a session.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/session";
import { Button, Empty, Panel } from "./ui";

interface VoiceSnapshot {
  stt_backend: string;
  stt_status: string;
  stt_model: string;
  tts_available: boolean;
  live_configured: boolean;
  mic: {
    floor: number;
    peak: number;
    clipped_percent: number;
    dc_offset: number;
    threshold: number;
    clamped: boolean;
    healthy: boolean;
  } | null;
}

function Field({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-[var(--text-muted)]">{label}</dt>
      <dd
        className="text-right font-[family-name:var(--font-mono)] text-[var(--text-xs)]"
        style={tone ? { color: tone } : undefined}
      >
        {value}
      </dd>
    </div>
  );
}

export function VoiceView() {
  const [voice, setVoice] = useState<VoiceSnapshot | null>(null);
  const [probing, setProbing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (probe: boolean) => {
    if (probe) setProbing(true);
    try {
      setVoice(await api<VoiceSnapshot>(`/api/voice?probe=${probe}`));
      setError("");
    } catch (exc) {
      setError(String(exc));
    } finally {
      setProbing(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  if (error) return <Empty>Could not read voice state: {error}</Empty>;
  if (!voice) return <Empty>Checking the voice chain…</Empty>;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Pipeline">
        <dl className="space-y-1.5 text-[var(--text-sm)]">
          <Field label="Speech to text" value={voice.stt_backend || "unavailable"} />
          <Field label="Model" value={voice.stt_model || "—"} />
          <Field
            label="Text to speech"
            value={voice.tts_available ? "edge-tts" : "unavailable"}
            tone={voice.tts_available ? "var(--safe)" : "var(--critical)"}
          />
          <Field
            label="Gemini Live"
            value={voice.live_configured ? "key present" : "no key"}
            tone={voice.live_configured ? "var(--safe)" : "var(--text-faint)"}
          />
        </dl>
        {voice.stt_status && (
          <p className="mt-3 text-[var(--text-xs)] text-[var(--text-muted)]">
            {voice.stt_status}
          </p>
        )}
      </Panel>

      <Panel
        title="Microphone"
        actions={
          <Button onClick={() => void load(true)} disabled={probing}>
            {probing ? "Recording…" : "Test mic"}
          </Button>
        }
      >
        {voice.mic ? (
          <dl className="space-y-1.5 text-[var(--text-sm)]">
            <Field
              label="Status"
              value={voice.mic.healthy ? "healthy" : "noisy"}
              tone={voice.mic.healthy ? "var(--safe)" : "var(--moderate)"}
            />
            <Field label="Noise floor" value={voice.mic.floor.toFixed(4)} />
            <Field label="Peak" value={voice.mic.peak.toFixed(4)} />
            <Field label="VAD threshold" value={voice.mic.threshold.toFixed(4)} />
            <Field label="Clipped" value={`${voice.mic.clipped_percent}%`} />
            {voice.mic.clamped && (
              <p className="pt-2 text-[var(--text-xs)] text-[var(--moderate)]">
                The threshold was clamped — the room is loud enough that
                auto-calibration would otherwise stop hearing you.
              </p>
            )}
          </dl>
        ) : (
          <p className="text-[var(--text-sm)] text-[var(--text-muted)]">
            Testing records a short clip from the default input device. Nothing
            is recorded until you ask.
          </p>
        )}
      </Panel>

      <Panel title="Which path to use" className="lg:col-span-2">
        <div className="space-y-3 text-[var(--text-sm)] text-[var(--text-muted)]">
          <p>
            <code className="font-[family-name:var(--font-mono)] text-[var(--text)]">
              brahma voice
            </code>{" "}
            runs entirely on this machine and costs nothing. Measured at about
            19s from the end of your sentence to the first spoken word.
          </p>
          <p>
            <code className="font-[family-name:var(--font-mono)] text-[var(--text)]">
              brahma voice live
            </code>{" "}
            uses Gemini native audio — about 7s, because it transcribes while
            you are still speaking. It is metered per second of audio,
            including silence, and it runs on Gemini, which has been the less
            reliable provider here.
          </p>
        </div>
      </Panel>
    </div>
  );
}
