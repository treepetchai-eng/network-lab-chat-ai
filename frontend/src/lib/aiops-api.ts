import { API_BASE_URL } from "./constants";
import type {
  AIOpsDashboardPayload,
  AIOpsDevice,
  AIOpsDeviceDetailPayload,
  AIOpsIncident,
  AIOpsIncidentDetailPayload,
  AIOpsLogsPayload,
  AIOpsProposal,
  AIOpsResetResponse,
} from "./aiops-types";

async function fetchAIOps<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`AIOps request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchDashboard(): Promise<AIOpsDashboardPayload> {
  return fetchAIOps("/api/aiops/dashboard");
}

export function fetchIncidents(): Promise<AIOpsIncident[]> {
  return fetchAIOps("/api/aiops/incidents");
}

export function resetIncidents(): Promise<AIOpsResetResponse> {
  return fetchAIOps("/api/aiops/incidents/reset", { method: "POST" });
}

export function fetchIncidentDetail(incidentNo: string): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}`);
}

export function fetchLogs(incidentNo?: string): Promise<AIOpsLogsPayload> {
  const query = incidentNo ? `?incident_no=${encodeURIComponent(incidentNo)}` : "";
  return fetchAIOps(`/api/aiops/logs${query}`);
}

export function fetchApprovals(): Promise<AIOpsProposal[]> {
  return fetchAIOps("/api/aiops/approvals");
}

export function fetchDevices(): Promise<AIOpsDevice[]> {
  return fetchAIOps("/api/aiops/devices");
}

export function fetchDeviceDetail(hostname: string): Promise<AIOpsDeviceDetailPayload> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}`);
}

export function fetchHistory(): Promise<AIOpsIncident[]> {
  return fetchAIOps("/api/aiops/history");
}

export function runTroubleshoot(incidentNo: string): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}/troubleshoot`, { method: "POST" });
}

export function approveProposal(incidentNo: string, actor: string): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}/approve`, {
    method: "POST",
    body: JSON.stringify({ actor }),
  });
}

export function executeProposal(incidentNo: string, actor: string): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}/execute`, {
    method: "POST",
    body: JSON.stringify({ actor }),
  });
}

export function submitRecoveryDecision(
  incidentNo: string,
  payload: { healed: boolean; note: string },
): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}/verify`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
