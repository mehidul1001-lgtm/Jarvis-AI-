import { useMemo, type ReactNode } from 'react';

/** Minimal, dependency-free renderer for the markdown subset the assistant
 * uses: fenced code blocks, inline code, bold, and line structure. */
export function Markdown({ text }: { text: string }) {
  const nodes = useMemo(() => renderMarkdown(text), [text]);
  return <div className="space-y-2 text-sm leading-relaxed">{nodes}</div>;
}

function renderMarkdown(text: string): ReactNode[] {
  const segments = text.split(/```(\w*)\n?/);
  const nodes: ReactNode[] = [];
  // split yields [text, lang, code, text, lang, code, ...]
  for (let i = 0; i < segments.length; i += 1) {
    const isCode = i % 3 === 2;
    const isLang = i % 3 === 1;
    if (isLang) continue;
    const segment = segments[i];
    if (!segment) continue;
    if (isCode) {
      nodes.push(
        <pre
          key={i}
          className="overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-100 dark:bg-black/60"
        >
          <code>{segment.replace(/\n$/, '')}</code>
        </pre>,
      );
    } else {
      segment.split(/\n{2,}/).forEach((paragraph, j) => {
        if (!paragraph.trim()) return;
        nodes.push(
          <p key={`${i}-${j}`} className="whitespace-pre-wrap">
            {renderInline(paragraph)}
          </p>,
        );
      });
    }
  }
  return nodes;
}

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={index}
          className="rounded bg-slate-200 px-1 py-0.5 text-xs dark:bg-slate-700"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}
