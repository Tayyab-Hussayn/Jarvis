"use client";

/**
 * Renders the model's answer.
 *
 * Models emit Markdown constantly — bold, bullet lists, fenced code and GFM
 * tables. Showing it raw meant answers arrived full of `**` and pipe
 * characters, which is exactly the content the user is meant to read.
 *
 * Everything is styled from tokens; no prose plugin, so the answer matches the
 * rest of the app rather than bringing its own typography.
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
        strong: ({ children }) => (
          <strong className="font-semibold text-[var(--text)]">{children}</strong>
        ),
        em: ({ children }) => <em className="italic">{children}</em>,
        a: ({ children, href }) => (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="underline"
            style={{ color: "var(--accent)" }}
          >
            {children}
          </a>
        ),
        ul: ({ children }) => (
          <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>
        ),
        h1: ({ children }) => (
          <h3 className="mb-1.5 mt-3 text-[length:var(--text-lg)] font-semibold first:mt-0">
            {children}
          </h3>
        ),
        h2: ({ children }) => (
          <h3 className="mb-1.5 mt-3 text-[length:var(--text-base)] font-semibold first:mt-0">
            {children}
          </h3>
        ),
        h3: ({ children }) => (
          <h4 className="mb-1 mt-2.5 text-[length:var(--text-sm)] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)] first:mt-0">
            {children}
          </h4>
        ),
        code: ({ className, children }) => {
          // react-markdown gives fenced blocks a language class and inline
          // code none; that is the only reliable way to tell them apart here.
          const fenced = Boolean(className);
          if (!fenced) {
            return (
              <code
                className="rounded-[var(--radius-sm)] px-1 py-0.5 font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]"
                style={{ background: "var(--surface-sunken)", color: "var(--accent)" }}
              >
                {children}
              </code>
            );
          }
          return (
            <code className="font-[family-name:var(--font-mono)] text-[length:var(--text-xs)]">
              {children}
            </code>
          );
        },
        pre: ({ children }) => (
          <pre className="mb-2 max-h-80 overflow-auto rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-sunken)] p-3 last:mb-0">
            {children}
          </pre>
        ),
        blockquote: ({ children }) => (
          <blockquote
            className="mb-2 border-l-2 pl-3 text-[var(--text-muted)] last:mb-0"
            style={{ borderColor: "var(--border-strong)" }}
          >
            {children}
          </blockquote>
        ),
        hr: () => <hr className="my-3 border-[var(--border)]" />,
        // Tables scroll inside their own container so a wide one never makes
        // the whole page scroll sideways.
        table: ({ children }) => (
          <div className="mb-2 overflow-x-auto rounded-[var(--radius)] border border-[var(--border)] last:mb-0">
            <table className="w-full border-collapse text-[length:var(--text-xs)]">
              {children}
            </table>
          </div>
        ),
        thead: ({ children }) => (
          <thead style={{ background: "var(--surface-sunken)" }}>{children}</thead>
        ),
        th: ({ children }) => (
          <th className="border-b border-[var(--border)] px-3 py-1.5 text-left font-medium text-[var(--text-muted)]">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border-b border-[var(--border)] px-3 py-1.5 tabular-nums last:border-0">
            {children}
          </td>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
