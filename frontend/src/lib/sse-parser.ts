import type { SSEEvent } from "./types";

export function parseSSELine(buffer: string): { events: SSEEvent[]; remaining: string } {
  const events: SSEEvent[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const chunks = normalized.split("\n\n");
  const hasCompleteBoundary = normalized.endsWith("\n\n");
  const completeChunks = hasCompleteBoundary ? chunks : chunks.slice(0, -1);
  const remaining = hasCompleteBoundary ? "" : (chunks.at(-1) ?? "");

  for (const chunk of completeChunks) {
    if (!chunk.trim()) continue;

    let currentEvent = "";
    const dataLines: string[] = [];

    for (const line of chunk.split("\n")) {
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (!currentEvent) continue;

    try {
      const data = JSON.parse(dataLines.join("\n") || "{}");
      events.push({ event: currentEvent, data });
    } catch {
      // skip malformed payloads
    }
  }

  return { events, remaining };
}
