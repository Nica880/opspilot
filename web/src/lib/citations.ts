export type Segment = { text: string } | { cite: string };

const CITE = /\[(S\d+)\]/g;

/** Split a reply into text and `[S#]` citation segments so citations can link to evidence. */
export function splitCitations(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const match of text.matchAll(CITE)) {
    const index = match.index ?? 0;
    if (index > last) out.push({ text: text.slice(last, index) });
    out.push({ cite: match[1] });
    last = index + match[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

export function unknownCitations(text: string, evidenceIds: string[]): string[] {
  const known = new Set(evidenceIds);
  return [...new Set([...text.matchAll(CITE)].map((m) => m[1]))].filter((id) => !known.has(id));
}
