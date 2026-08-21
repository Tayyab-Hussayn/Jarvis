"use client";

import { useState } from "react";

import { useSkill, type RunSkill } from "@/lib/useApi";
import { Button, Input, Panel, SectionTitle } from "./ui";
import { ActionRow, SkillOutput } from "./SkillOutput";
import { Screen } from "./Shell";

const QUICK_PATHS = ["~", "~/Downloads", "~/Documents", "~/Pictures", "~/code", "/tmp"];

export function FilesView({ run }: { run: RunSkill }) {
  const skill = useSkill(run);
  const [path, setPath] = useState("~");
  const [query, setQuery] = useState("");
  const [docTitle, setDocTitle] = useState("");

  return (
    <Screen
      title="Files"
      subtitle="Browse, search and measure. Deletions and moves always ask first."
    >
      <div className="space-y-4 overflow-y-auto pb-6">
        <Panel title="Browse">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={path}
              onChange={setPath}
              placeholder="~/Downloads"
              mono
              className="min-w-0 flex-1"
              onEnter={() => void skill.call("list_files", { path })}
            />
            <Button variant="primary" onClick={() => void skill.call("list_files", { path })}>
              List
            </Button>
            <Button onClick={() => void skill.call("directory_size", { path, top: 15 })}>
              Sizes
            </Button>
            <Button onClick={() => void skill.call("open_path", { path })}>Open</Button>
          </div>
          <div className="no-scrollbar mt-3 flex gap-2 overflow-x-auto">
            {QUICK_PATHS.map((p) => (
              <Button key={p} variant="ghost" onClick={() => setPath(p)}>
                {p}
              </Button>
            ))}
          </div>
        </Panel>

        <Panel title="Find">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={query}
              onChange={setQuery}
              placeholder="*.pdf, invoice, screenshot…"
              className="min-w-0 flex-1"
              onEnter={() => query && void skill.call("find_files", { name: query, path })}
            />
            <Button
              variant="primary"
              disabled={!query}
              onClick={() => void skill.call("find_files", { name: query, path, max_results: 40 })}
            >
              Search {path}
            </Button>
          </div>
        </Panel>

        <SkillOutput state={skill} />

        <Panel title="Create a document">
          <p className="mb-3 text-[length:var(--text-sm)] text-[var(--text-muted)]">
            Documents with real structure are better asked for in the Console —
            the model writes the sections. These buttons create an empty file
            you can fill in.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={docTitle}
              onChange={setDocTitle}
              placeholder="Document title"
              className="min-w-0 flex-1"
            />
          </div>
          <ActionRow>
            <Button
              disabled={!docTitle}
              onClick={() =>
                void skill.call("create_document", {
                  title: docTitle,
                  sections: [{ heading: docTitle, body: "" }],
                })
              }
            >
              Word
            </Button>
            <Button
              disabled={!docTitle}
              onClick={() =>
                // create_spreadsheet has no `title` — the name comes from
                // `filename`, unlike the other document skills.
                void skill.call("create_spreadsheet", {
                  filename: docTitle,
                  sheets: [{ name: "Sheet1", headers: ["Item"], rows: [[""]] }],
                })
              }
            >
              Spreadsheet
            </Button>
            <Button
              disabled={!docTitle}
              onClick={() =>
                void skill.call("create_presentation", {
                  title: docTitle,
                  slides: [{ title: docTitle, bullets: [] }],
                })
              }
            >
              Slides
            </Button>
          </ActionRow>
        </Panel>
      </div>
    </Screen>
  );
}
