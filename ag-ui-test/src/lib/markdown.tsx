import type { ReactNode } from "react";

// Renders "**bold**" and "[text](url)" markdown-lite segments as JSX, preserving line breaks.
export function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let idx = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    if (match[1] !== undefined) {
      nodes.push(
        <a key={`${keyPrefix}-a-${idx}`} href={match[2]} target="_blank" rel="noopener noreferrer">
          {match[1]}
        </a>
      );
    } else {
      nodes.push(<strong key={`${keyPrefix}-b-${idx}`}>{match[3]}</strong>);
    }
    idx++;
    lastIndex = pattern.lastIndex;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

export function renderMessageContent(content: string): ReactNode {
  return content.split("\n").map((line, i, arr) => (
    <span key={i}>
      {renderInline(line, `l${i}`)}
      {i < arr.length - 1 && <br />}
    </span>
  ));
}
