import { API_BASE_URL } from "./constants";
import type {
  LabRole,
  OpsActionResponse,
  OpsApproval,
  OpsApprovalsQuery,
  OpsDevice,
  OpsDevicesQuery,
  OpsIncident,
  OpsIncidentCluster,
  OpsIncidentDetail,
  OpsIncidentsQuery,
  OpsLoopConfig,
  OpsLoopStatus,
  OpsOverview,
  OpsRemediationStatus,
  PaginatedResponse,
} from "./ops-types";

const DEFAULT_TIMEOUT_MS = 20000;

async function readErrorDetail(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload = await response.json().catch(() => null);
    if (payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string") {
      return payload.detail;
    }
    if (typeof payload === "string") return payload;
    if (payload !== null) return JSON.stringify(payload);
  }
  const detail = await response.text();
  return detail || `Request failed: ${response.status}`;
}

async function fetchJson<T>(path: string, init: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const timeoutController = new AbortController();
  const upstreamSignal = init.signal;
  const timeoutId = window.setTimeout(() => timeoutController.abort(), timeoutMs);
  const signal = upstreamSignal
    ? AbortSignal.any([upstreamSignal, timeoutController.signal])
    : timeoutController.signal;

  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init, signal, cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
    if (!response.ok) throw new Error(await readErrorDetail(response));
    return await response.json() as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.ceil(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function buildQueryString<T extends object>(params: T): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params as Record<string, string | number | boolean | null | undefined>)) {
    if (value === null || value === undefined || value === "" || value === false) continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}

/* ── Overview ─────────────────────────────────────── */

export function fetchOpsOverview() {
  return fetchJson<OpsOverview>("/api/ops/overview");
}

/* ── Devices ──────────────────────────────────────── */

export function fetchOpsDevices(query: OpsDevicesQuery = {}) {
  return fetchJson<PaginatedResponse<OpsDevice>>(`/api/ops/devices${buildQueryString(query)}`);
}

/* ── Incidents ────────────────────────────────────── */

export function fetchOpsIncidents(query: OpsIncidentsQuery = {}) {
  return fetchJson<PaginatedResponse<OpsIncident>>(`/api/ops/incidents${buildQueryString(query)}`);
}

export function fetchOpsIncident(incidentId: number) {
  return fetchJson<OpsIncidentDetail>(`/api/ops/incidents/${incidentId}`);
}

export function investigateOpsIncident(incidentId: number, requestedBy = "manager", requestedByRole: LabRole = "admin") {
  return fetchJson<OpsActionResponse>(`/api/ops/incidents/${incidentId}/investigate`, {
    method: "POST",
    body: JSON.stringify({ requested_by: requestedBy, requested_by_role: requestedByRole }),
  }, 60000);
}

export function troubleshootOpsIncident(incidentId: number, requestedBy = "manager", requestedByRole: LabRole = "admin") {
  return fetchJson<OpsActionResponse>(`/api/ops/incidents/${incidentId}/troubleshoot`, {
    method: "POST",
    body: JSON.stringify({ requested_by: requestedBy, requested_by_role: requestedByRole }),
  }, 120000);
}

export function troubleshootPlanSSE(
  incidentId: number,
  requestedBy = "manager",
  requestedByRole: LabRole = "admin",
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`${API_BASE_URL}/api/ops/incidents/${incidentId}/troubleshoot/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ requested_by: requestedBy, requested_by_role: requestedByRole }),
    signal,
  });
}

export function troubleshootExecuteSSE(
  incidentId: number,
  sessionId: string,
  userInstruction = "",
  requestedBy = "manager",
  requestedByRole: LabRole = "admin",
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`${API_BASE_URL}/api/ops/incidents/${incidentId}/troubleshoot/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      troubleshoot_session_id: sessionId,
      user_instruction: userInstruction,
      requested_by: requestedBy,
      requested_by_role: requestedByRole,
    }),
    signal,
  });
}

export function fetchOpsIncidentRemediationStatus(incidentId: number) {
  return fetchJson<OpsRemediationStatus>(`/api/ops/incidents/${incidentId}/remediation-status`);
}

/* ── Approvals ────────────────────────────────────── */

export function fetchOpsApprovals(query: OpsApprovalsQuery = {}) {
  return fetchJson<PaginatedResponse<OpsApproval>>(`/api/ops/approvals${buildQueryString(query)}`);
}

export function approveOpsApproval(approvalId: number, actor = "manager", actorRole: LabRole = "admin", comment?: string) {
  return fetchJson<OpsActionResponse<OpsApproval>>(`/api/ops/approvals/${approvalId}/approve`, {
    method: "POST",
    body: JSON.stringify({ actor, actor_role: actorRole, comment }),
  });
}

export function rejectOpsApproval(approvalId: number, actor = "manager", actorRole: LabRole = "admin", comment?: string) {
  return fetchJson<OpsActionResponse<OpsApproval>>(`/api/ops/approvals/${approvalId}/reject`, {
    method: "POST",
    body: JSON.stringify({ actor, actor_role: actorRole, comment }),
  });
}

export function executeOpsApproval(approvalId: number, actor = "manager", actorRole: LabRole = "admin") {
  return fetchJson<OpsActionResponse<OpsApproval>>(`/api/ops/approvals/${approvalId}/execute`, {
    method: "POST",
    body: JSON.stringify({ actor, actor_role: actorRole }),
  }, 60000);
}

/* ── AI Ops Loop ──────────────────────────────────── */

export function fetchOpsLoopConfig() {
  return fetchJson<OpsLoopConfig>("/api/ops/loop/config");
}

export function fetchOpsLoopStatus(incidentId: number) {
  return fetchJson<OpsLoopStatus>(`/api/ops/incidents/${incidentId}/loop/status`);
}

export function opsLoopStreamSSE(incidentId: number, signal?: AbortSignal): Promise<Response> {
  return fetch(`${API_BASE_URL}/api/ops/incidents/${incidentId}/loop/stream`, {
    method: "GET",
    signal,
    cache: "no-store",
  });
}

export function retriggerOpsLoop(
  incidentId: number,
  mode: "full" | "investigate_only" | "troubleshoot_only" = "full",
  actor = "operator",
  actorRole: LabRole = "admin",
) {
  return fetchJson<OpsActionResponse>(`/api/ops/incidents/${incidentId}/loop/retrigger`, {
    method: "POST",
    body: JSON.stringify({ mode, actor, actor_role: actorRole }),
  });
}

/* ── Incident Status ──────────────────────────────── */

export function updateIncidentStatus(
  incidentId: number,
  status: string,
  comment?: string,
  actor = "operator",
  actorRole = "admin",
) {
  return fetchJson(`/api/ops/incidents/${incidentId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status, actor, actor_role: actorRole, comment }),
  });
}

/* ── Incident Assignment ──────────────────────────────── */

export function assignIncident(
  incidentId: number,
  assignedTo: string,
  actor = "operator",
  actorRole = "admin",
) {
  return fetchJson(`/api/ops/incidents/${incidentId}/assign`, {
    method: "POST",
    body: JSON.stringify({ assigned_to: assignedTo, actor, actor_role: actorRole }),
  });
}

/* ── Incident Feedback ──────────────────────────────── */

export function submitIncidentFeedback(
  incidentId: number,
  payload: {
    rating: number;
    was_false_positive?: boolean;
    resolution_effectiveness?: string;
    operator_notes?: string;
    created_by?: string;
  },
) {
  return fetchJson(`/api/ops/incidents/${incidentId}/feedback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchIncidentFeedback(incidentId: number) {
  return fetchJson(`/api/ops/incidents/${incidentId}/feedback`);
}

/* ── Incident Chat ──────────────────────────────── */

export function chatWithIncident(
  incidentId: number,
  message: string,
  history: Array<{ role: string; content: string }> = [],
  requestedBy = "manager",
  requestedByRole = "admin",
) {
  return fetchJson(`/api/ops/incidents/${incidentId}/chat`, {
    method: "POST",
    body: JSON.stringify({
      message,
      history,
      requested_by: requestedBy,
      requested_by_role: requestedByRole,
    }),
  }, 60000);
}

/* ── Clusters ──────────────────────────────── */

export function fetchOpsClusters(query: Record<string, string | number> = {}): Promise<{ items: OpsIncidentCluster[] }> {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([k, v]) => { if (v != null) params.set(k, String(v)); });
  const qs = params.toString();
  return fetchJson(`/api/ops/clusters${qs ? `?${qs}` : ""}`);
}

export function fetchOpsClusterDetail(clusterId: number) {
  return fetchJson(`/api/ops/clusters/${clusterId}`);
}

/* ── Dev: Purge ──────────────────────────────── */

export function purgeAllIncidents() {
  return fetchJson("/api/ops/dev/purge-incidents", { method: "POST" });
}
