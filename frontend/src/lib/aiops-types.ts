export interface AIOpsMetricSnapshot {
  active_incidents: number;
  recovering_incidents: number;
  pending_approvals: number;
  resolved_today: number;
  reopened_this_week: number;
}

export interface AIOpsIncident {
  id: number;
  incident_no: string;
  title: string;
  status: string;
  severity: string;
  category: string;
  summary: string;
  probable_cause: string;
  confidence_score: number;
  site: string;
  primary_source_ip: string;
  primary_hostname?: string | null;
  correlation_key: string;
  event_family: string;
  event_count: number;
  current_recovery_state: string;
  resolution_type?: string | null;
  opened_at: string;
  last_seen_at: string;
  resolved_at?: string | null;
  reopened_count: number;
}

export interface AIOpsTimelineEntry {
  id: number;
  kind: string;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AIOpsSummary {
  id: number;
  summary: string;
  probable_cause: string;
  confidence_score: number;
  category: string;
  impact: string;
  suggested_checks: string[];
  created_at: string;
}

export interface AIOpsTroubleshootRun {
  id: number;
  status: string;
  disposition: string;
  summary: string;
  conclusion: string;
  steps: Array<{ tool_name: string; args: Record<string, unknown>; content: string }>;
  created_at: string;
}

export interface AIOpsProposal {
  id: number;
  title: string;
  rationale: string;
  target_devices: string[];
  commands: string[];
  rollback_plan: string;
  expected_impact: string;
  verification_commands: string[];
  risk_level: string;
  status: string;
  approved_at?: string | null;
  approved_by?: string | null;
  cancelled_reason?: string | null;
  created_at: string;
  incident_no?: string;
  incident_title?: string;
}

export interface AIOpsExecution {
  id: number;
  status: string;
  executed_by: string;
  output: string;
  verification_status: string;
  verification_notes: string;
  created_at: string;
  completed_at?: string | null;
}

export interface AIOpsRawLog {
  id: number;
  source_ip: string;
  hostname?: string | null;
  raw_message: string;
  event_time: string;
  received_at: string;
  parse_status: string;
  incident_no?: string | null;
}

export interface AIOpsEvent {
  id: number;
  event_family: string;
  event_state: string;
  severity: string;
  title: string;
  summary: string;
  correlation_key: string;
  hostname?: string | null;
  raw_message?: string | null;
  incident_no?: string | null;
  created_at: string;
}

export interface AIOpsDevice {
  id: number;
  hostname: string;
  ip_address: string;
  os_platform: string;
  device_role: string;
  site: string;
  version: string;
  open_incident_count: number;
  last_incident_seen?: string | null;
}

export interface AIOpsDashboardPayload {
  metrics: AIOpsMetricSnapshot;
  incidents: AIOpsIncident[];
  approvals: AIOpsProposal[];
  history: AIOpsIncident[];
}

export interface AIOpsIncidentDetailPayload {
  incident: AIOpsIncident;
  timeline: AIOpsTimelineEntry[];
  ai_summary?: AIOpsSummary | null;
  troubleshoot?: AIOpsTroubleshootRun | null;
  proposal?: AIOpsProposal | null;
  execution?: AIOpsExecution | null;
  raw_logs: AIOpsRawLog[];
  events: AIOpsEvent[];
}

export interface AIOpsLogsPayload {
  raw_logs: AIOpsRawLog[];
  events: AIOpsEvent[];
}

export interface AIOpsResetResponse {
  incidents_removed: number;
  events_removed: number;
  raw_logs_removed: number;
}

export interface AIOpsDeviceDetailPayload {
  device: AIOpsDevice;
  incidents: AIOpsIncident[];
  events: AIOpsEvent[];
}
