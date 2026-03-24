import { API_BASE_URL } from "./constants";
import type {
  AIOpsDashboardPayload,
  AIOpsAdvisoryCheck,
  AIOpsCheckSummary,
  AIOpsDevice,
  AIOpsDeviceDetailPayload,
  AIOpsDeviceVulnPayload,
  AIOpsIncident,
  AIOpsIncidentDetailPayload,
  AIOpsLogsPayload,
  AIOpsProposal,
  AIOpsResetResponse,
  AIOpsVulnSummaryPayload,
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

export function fetchLogs(
  incidentNo?: string,
  opts?: { device?: string; hoursBack?: number; keyword?: string },
): Promise<AIOpsLogsPayload> {
  const params = new URLSearchParams();
  if (incidentNo) params.set("incident_no", incidentNo);
  if (opts?.device) params.set("device", opts.device);
  if (opts?.hoursBack) params.set("hours_back", String(opts.hoursBack));
  if (opts?.keyword) params.set("keyword", opts.keyword);
  const query = params.size ? `?${params}` : "";
  return fetchAIOps(`/api/aiops/logs${query}`);
}

export function addIncidentNote(
  incidentNo: string,
  body: string,
  author = "engineer",
): Promise<AIOpsIncidentDetailPayload> {
  return fetchAIOps(`/api/aiops/incidents/${incidentNo}/notes`, {
    method: "POST",
    body: JSON.stringify({ body, author }),
  });
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

export function fetchDeviceVulnerabilities(hostname: string): Promise<AIOpsDeviceVulnPayload> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}/vulnerabilities`);
}

export function triggerVulnScan(hostname: string): Promise<AIOpsDeviceVulnPayload> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}/vulnerabilities/scan`, {
    method: "POST",
  });
}

export function fetchVulnerabilitySummary(): Promise<AIOpsVulnSummaryPayload> {
  return fetchAIOps("/api/aiops/vulnerabilities");
}

export function triggerScanAll(): Promise<{ started: boolean; device_count: number; to_scan: number; message: string }> {
  return fetchAIOps("/api/aiops/vulnerabilities/scan-all", { method: "POST" });
}

export function fetchAdvisoryChecks(hostname: string, advisoryId: string): Promise<AIOpsAdvisoryCheck[]> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}/vulnerabilities/${encodeURIComponent(advisoryId)}/checks`);
}

export function clearAdvisoryChecks(hostname: string): Promise<{ deleted: number }> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}/vulnerabilities/checks`, {
    method: "DELETE",
  });
}

export function fetchDeviceCheckSummary(hostname: string): Promise<AIOpsCheckSummary> {
  return fetchAIOps(`/api/aiops/devices/${encodeURIComponent(hostname)}/vulnerabilities/check-summary`);
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
