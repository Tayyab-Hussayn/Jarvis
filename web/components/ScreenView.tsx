"use client";

import { useState } from "react";

import { useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Input, Panel } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

/**
 * Screen, input and the web — the things that look at or reach outside the
 * machine's own state.
 *
 * Screenshots report their saved path rather than rendering inline. Showing
 * the image would need an endpoint that reads arbitrary files, and a
 * loopback-bound arbitrary-file-read is not worth a thumbnail.
 */
export function ScreenView({ run }: { run: RunSkill }) {
  const skill = useSkill(run);
  const [question, setQuestion] = useState("");
  const [search, setSearch] = useState("");
  const [city, setCity] = useState("");
  const [clip, setClip] = useState("");
  const [keys, setKeys] = useState("");

  return (
    <Screen
      title="Screen & Web"
      subtitle="Look at the display, drive input, search the web."
    >
      <div className="space-y-4 overflow-y-auto pb-6">
        <Panel title="Ask about what's on screen">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={question}
              onChange={setQuestion}
              placeholder="What error is this dialog showing?"
              className="min-w-0 flex-1"
              onEnter={() =>
                question && void skill.call("look_at_screen", { question })
              }
            />
            <Button
              variant="primary"
              disabled={!question}
              onClick={() => void skill.call("look_at_screen", { question })}
            >
              Look
            </Button>
            <Button
              disabled={!question}
              onClick={() =>
                void skill.call("look_at_screen", { question, active_window: true })
              }
            >
              Active window
            </Button>
          </div>
          <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
            Vision runs through Gemini and needs an API key configured.
          </p>
        </Panel>

        <SkillOutput state={skill} />

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Capture">
            <ActionRow>
              <Button onClick={() => void skill.call("take_screenshot", {})}>
                Full screen
              </Button>
              <Button onClick={() => void skill.call("take_screenshot", { active_window: true })}>
                Active window
              </Button>
              <Button onClick={() => void skill.call("take_screenshot", { region: true })}>
                Select region
              </Button>
              <Button onClick={() => void skill.call("screen_info", {})}>Screen info</Button>
            </ActionRow>
          </Panel>

          <Panel title="Clipboard">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={clip}
                onChange={setClip}
                placeholder="Text to copy…"
                className="min-w-0 flex-1"
              />
              <Button
                disabled={!clip}
                onClick={() => void skill.call("write_clipboard", { text: clip })}
              >
                Copy
              </Button>
              <Button onClick={() => void skill.call("read_clipboard", {})}>Read</Button>
            </div>
          </Panel>

          <Panel title="Input">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={keys}
                onChange={setKeys}
                placeholder="ctrl+shift+t"
                mono
                className="min-w-0 flex-1"
              />
              <Button
                disabled={!keys}
                onClick={() => void skill.call("press_keys", { keys })}
              >
                Press
              </Button>
              <Button onClick={() => void skill.call("input_status", {})}>Status</Button>
            </div>
            <p className="mt-2 text-[length:var(--text-xs)] text-[var(--text-faint)]">
              Synthetic input needs ydotool on Wayland or xdotool on X11.
            </p>
          </Panel>

          <Panel title="Wallpaper & media">
            <ActionRow>
              <Button onClick={() => void skill.call("wallpaper_status", {})}>
                Current wallpaper
              </Button>
              <Button onClick={() => void skill.call("find_video", { query: "" })}>
                Find video
              </Button>
            </ActionRow>
          </Panel>

          <Panel title="Web" className="lg:col-span-2">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                value={search}
                onChange={setSearch}
                placeholder="Search the web…"
                className="min-w-0 flex-1"
                onEnter={() => search && void skill.call("web_search", { query: search })}
              />
              <Button
                variant="primary"
                disabled={!search}
                onClick={() => void skill.call("web_search", { query: search })}
              >
                Search
              </Button>
              <Button
                disabled={!search}
                onClick={() => void skill.call("search_and_open", { query: search })}
              >
                Open first result
              </Button>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Input
                value={city}
                onChange={setCity}
                placeholder="Weather for…"
                className="w-56"
                onEnter={() => void skill.call("weather", city ? { city } : {})}
              />
              <Button onClick={() => void skill.call("weather", city ? { city } : {})}>
                Weather
              </Button>
              <Button onClick={() => void skill.call("default_browser", {})}>
                Default browser
              </Button>
            </div>
          </Panel>
        </div>
      </div>
    </Screen>
  );
}
