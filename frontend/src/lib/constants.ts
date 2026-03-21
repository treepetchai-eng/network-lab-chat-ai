function getApiBaseUrl(): string {
  // Use env var if set
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  // In the browser, use the same hostname so it works from any machine
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export const API_BASE_URL = getApiBaseUrl();

export const SSE_EVENTS = {
  ROUTING: "routing",
  STATUS: "status",
  TOOL_RESULT: "tool_result",
  ANALYST_TOKEN: "analyst_token",
  ANALYST_DONE: "analyst_done",
  ERROR: "error",
  DONE: "done",
  PLAN_READY: "plan_ready",
  ROUND_DONE: "round_done",
} as const;
