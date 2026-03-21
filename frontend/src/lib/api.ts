import { API_BASE_URL } from "./constants";

export interface InventoryApiRecord {
  hostname: string;
  ip_address: string;
  os_platform: string;
  device_role: string;
  site: string;
  version: string;
}

const DEFAULT_TIMEOUT_MS = 8000;
const CHAT_START_TIMEOUT_MS = 15000;

async function fetchWithTimeout(input: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
  const timeoutController = new AbortController();
  const upstreamSignal = init.signal;
  const timeoutId = window.setTimeout(() => timeoutController.abort(), timeoutMs);

  const signal = upstreamSignal
    ? AbortSignal.any([upstreamSignal, timeoutController.signal])
    : timeoutController.signal;

  try {
    return await fetch(input, {
      ...init,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.ceil(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function createSession(): Promise<string> {
  const res = await fetchWithTimeout(`${API_BASE_URL}/api/session`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to create session");
  const data = await res.json();
  return data.session_id;
}

interface DeleteSessionOptions {
  keepalive?: boolean;
}

export async function deleteSession(
  sessionId: string,
  options: DeleteSessionOptions = {},
): Promise<void> {
  await fetch(`${API_BASE_URL}/api/session/${sessionId}`, {
    method: "DELETE",
    keepalive: options.keepalive ?? false,
  });
}

export async function validateSession(sessionId: string): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/api/session/${sessionId}/validate`);
    return res.ok;
  } catch {
    return false;
  }
}

export async function fetchInventory(): Promise<InventoryApiRecord[]> {
  const res = await fetchWithTimeout(`${API_BASE_URL}/api/inventory`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Inventory fetch failed: ${res.status}`);
  const data = await res.json();
  if (!Array.isArray(data)) throw new Error("Inventory payload was not an array");
  return data as InventoryApiRecord[];
}

export async function sendMessageStream(sessionId: string, message: string, signal?: AbortSignal): Promise<Response> {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/api/chat`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    },
    CHAT_START_TIMEOUT_MS,
  );
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res;
}
