"use client";

import { useEffect, useState } from "react";

import { useSkill, type RunSkill } from "@/lib/useApi";
import type { SystemSnapshot } from "@/lib/protocol";
import { Button, Input, Panel, Stat } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

/**
 * Controls the *machine* — audio, display, network, power, notifications.
 * Distinct from Settings, which configures the assistant itself.
 */

function Slider({
  label,
  value,
  onCommit,
  disabled,
}: {
  label: string;
  value: number;
  onCommit: (value: number) => void;
  disabled?: boolean;
}) {
  const [local, setLocal] = useState(value);

  // Follow the real value when it changes underneath us (something else
  // changed the volume), but not while the user is mid-drag.
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!dragging) setLocal(value);
  }, [value, dragging]);

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[length:var(--text-sm)] text-[var(--text-muted)]">{label}</span>
        <span className="tabular-nums text-[length:var(--text-sm)]">{local}%</span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        value={local}
        disabled={disabled}
        onChange={(event) => {
          setDragging(true);
          setLocal(Number(event.target.value));
        }}
        onMouseUp={() => {
          setDragging(false);
          onCommit(local);
        }}
        onTouchEnd={() => {
          setDragging(false);
          onCommit(local);
        }}
        onKeyUp={() => onCommit(local)}
        className="w-full accent-[var(--accent)]"
        aria-label={label}
      />
    </div>
  );
}

export function ControlView({
  run,
  system,
}: {
  run: RunSkill;
  system: SystemSnapshot | null;
}) {
  const skill = useSkill(run);
  const [volume, setVolume] = useState(50);
  const [brightness, setBrightness] = useState(80);
  const [loaded, setLoaded] = useState(false);
  const [note, setNote] = useState("");
  const [remindIn, setRemindIn] = useState("10m");

  // Read the real levels once. Skills return prose ("Volume is 30%."), so the
  // number is pulled out with a narrow match and simply ignored if absent —
  // the slider still works, it just starts from a default.
  useEffect(() => {
    if (loaded) return;
    setLoaded(true);
    void (async () => {
      const vol = await run("get_volume", {}).catch(() => "");
      const match = /(\d{1,3})\s*%/.exec(vol);
      if (match) setVolume(Number(match[1]));
      const bright = await run("get_brightness", {}).catch(() => "");
      const bmatch = /(\d{1,3})\s*%/.exec(bright);
      if (bmatch) setBrightness(Number(bmatch[1]));
    })();
  }, [loaded, run]);

  const battery = system?.battery;

  return (
    <Screen
      subtitle="Audio, display, network and power. Shutdown and logout always confirm."
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Battery"
            value={battery ? Math.round(battery.percent) : "—"}
            unit={battery ? "%" : undefined}
            detail={battery ? (battery.plugged ? "charging" : "on battery") : "no battery"}
            percent={battery?.percent}
            tone={battery && battery.percent < 20 && !battery.plugged ? "critical" : "accent"}
          />
          <Stat label="Volume" value={volume} unit="%" percent={volume} />
          <Stat label="Brightness" value={brightness} unit="%" percent={brightness} />
          <Stat
            label="Memory"
            value={system?.memory ? Math.round(system.memory.percent) : "—"}
            unit={system?.memory ? "%" : undefined}
            percent={system?.memory?.percent}
            tone={(system?.memory?.percent ?? 0) > 85 ? "moderate" : "accent"}
          />
        </div>

        <SkillOutput state={skill} />

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Audio">
            <Slider
              label="Volume"
              value={volume}
              onCommit={(v) => {
                setVolume(v);
                void skill.call("set_volume", { percent: v });
              }}
            />
            <ActionRow>
              <Button onClick={() => void skill.call("mute_audio", {})}>Mute</Button>
              <Button onClick={() => void skill.call("mute_microphone", {})}>Mute mic</Button>
              <Button onClick={() => void skill.call("list_audio_devices", {})}>Devices</Button>
              <Button onClick={() => void skill.call("now_playing", {})}>Now playing</Button>
            </ActionRow>
            <div className="mt-3">
              <ActionRow label="Media">
                <Button onClick={() => void skill.call("media_control", { command: "previous" })}>
                  ⏮
                </Button>
                <Button onClick={() => void skill.call("media_control", { command: "play-pause" })}>
                  ⏯
                </Button>
                <Button onClick={() => void skill.call("media_control", { command: "next" })}>
                  ⏭
                </Button>
              </ActionRow>
            </div>
          </Panel>

          <Panel title="Display">
            <Slider
              label="Brightness"
              value={brightness}
              onCommit={(v) => {
                setBrightness(v);
                void skill.call("set_brightness", { percent: v });
              }}
            />
            <ActionRow>
              <Button onClick={() => void skill.call("set_dark_mode", {})}>Toggle dark</Button>
              <Button onClick={() => void skill.call("list_monitors", {})}>Monitors</Button>
              <Button onClick={() => void skill.call("blank_screen", {})}>Blank</Button>
            </ActionRow>
          </Panel>

          <Panel title="Network">
            <ActionRow label="Wi-Fi">
              <Button onClick={() => void skill.call("list_wifi_networks", {})}>Scan</Button>
              <Button onClick={() => void skill.call("wifi_control", { action: "on" })}>On</Button>
              <Button onClick={() => void skill.call("wifi_control", { action: "off" })}>Off</Button>
            </ActionRow>
            <div className="mt-3">
              <ActionRow label="Bluetooth">
                <Button onClick={() => void skill.call("bluetooth_control", { action: "status" })}>
                  Status
                </Button>
                <Button onClick={() => void skill.call("bluetooth_control", { action: "on" })}>
                  On
                </Button>
                <Button onClick={() => void skill.call("bluetooth_control", { action: "off" })}>
                  Off
                </Button>
              </ActionRow>
            </div>
          </Panel>

          <Panel title="Power">
            <ActionRow>
              <Button onClick={() => void skill.call("battery_status", {})}>Battery</Button>
              <Button onClick={() => void skill.call("lock_screen", {})}>Lock</Button>
              <Button onClick={() => void skill.call("suspend_system", {})}>Suspend</Button>
            </ActionRow>
            <div className="mt-3">
              <ActionRow label="Ends your session">
                <Button variant="danger" onClick={() => void skill.call("log_out", {})}>
                  Log out
                </Button>
                <Button variant="danger" onClick={() => void skill.call("reboot_system", {})}>
                  Reboot
                </Button>
                <Button variant="danger" onClick={() => void skill.call("shutdown_system", {})}>
                  Shut down
                </Button>
              </ActionRow>
            </div>
            <p className="mt-3 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              These are critical actions — each asks twice before running.
            </p>
          </Panel>

          <Panel title="Notifications" className="lg:col-span-2">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={note}
                onChange={setNote}
                placeholder="Message…"
                className="min-w-0 flex-1"
              />
              <Input
                value={remindIn}
                onChange={setRemindIn}
                placeholder="10m"
                className="w-24"
              />
              <Button
                disabled={!note}
                onClick={() => void skill.call("send_notification", { message: note })}
              >
                Notify now
              </Button>
              <Button
                variant="primary"
                disabled={!note}
                onClick={() =>
                  void skill.call("set_reminder", { message: note, delay: remindIn })
                }
              >
                Remind me
              </Button>
            </div>
            <ActionRow>
              <Button onClick={() => void skill.call("list_reminders", {})}>Reminders</Button>
              <Button onClick={() => void skill.call("recent_notifications", {})}>Recent</Button>
              <Button onClick={() => void skill.call("notification_status", {})}>Status</Button>
              <Button onClick={() => void skill.call("stop_speaking", {})}>Stop speaking</Button>
            </ActionRow>
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
