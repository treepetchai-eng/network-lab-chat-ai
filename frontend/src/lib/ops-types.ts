export type SortDirection = "asc" | "desc";
export type LabRole = "viewer" | "operator" | "approver" | "admin";

/* ── Ops Loop ─────────────────────────────────────── */

export interface OpsLoopStage {
  stage: string;
  timestamp: string | null;
  summary: string;
  payload: Record<string, unknown>;
}

export interface OpsLoopConfig {
  auto_troubleshoot_enabled: boolean;
  auto_execute_enabled: boolean;
  auto_verify_enabled: boolean;
  auto_close_enabled: boolean;
  troubleshoot_delay_seconds: number;
  poll_interval_seconds: number;
  verify_timeout_seconds: number;
  max_auto_execute_risk: string;
}

export interface OpsLoopStatus {
  incident_id: number;
  incident_status: string;
  current_phase: string;
  latest_approval_id: number | null;
  latest_approval_status: string | null;
  stages: OpsLoopStage[];
  config: OpsLoopConfig;
  terminal_state: "success" | "needs_action" | "escalated" | null;
  available_actions: string[];
  escalation_context: {
    analysis: string;
    root_cause: string;
    confidence_score: number;
    created_at: string | null;
  } | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  sort_by: string;
  sort_dir: SortDirection;
  facets?: Record<string, string[]>;
}

export interface OpsActionCatalogEntry {
  action_id: string;
  label: string;
  description: string;
  category: string;
  supported_platforms: string[];
  required_params: string[];
  default_risk: string;
  minimum_role: LabRole;
  approval_role: LabRole;
  readonly: boolean;
  prechecks: string[];
  verify_steps: string[];
  rollback_strategy: string[];
  blocked_conditions: string[];
}

export interface OpsAuditEntry {
  id: number;
  actor: string;
  actor_role: LabRole | string;
  action: string;
  entity_type: string;
  entity_id: number | null;
  status: string;
  summary: string;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface OpsAIArtifact {
  id: number;
  artifact_type: string;
  title: string;
  incident_id: number | null;
  device_id: number | null;
  job_id: number | null;
  approval_id: number | null;
  provider: string | null;
  model: string | null;
  prompt_version: string;
  summary: string | null;
  root_cause: string | null;
  confidence_score: number;
  readiness: string;
  risk_explanation: string | null;
  evidence_refs: Record<string, unknown>;
  proposed_actions: Record<string, unknown>;
  content: Record<string, unknown>;
  steps?: { step_name: string; content: string; is_error: boolean }[];
  created_at: string | null;
}

export interface OpsDevice {
  id: number;
  hostname: string;
  mgmt_ip: string;
  os_platform: string;
  device_role: string;
  site: string;
  version: string;
  vendor: string;
  enabled: boolean;
  open_incident_count: number;
  last_event_summary: string | null;
  last_event_time: string | null;
}

export interface OpsEvent {
  id: number;
  event_time: string | null;
  ingested_at: string | null;
  source_ip: string;
  hostname: string | null;
  severity: string;
  facility: string;
  event_code: string;
  event_type: string;
  protocol: string | null;
  interface_name: string | null;
  neighbor: string | null;
  state: string | null;
  summary: string;
  details: Record<string, unknown>;
}

export interface OpsIncident {
  id: number;
  incident_no: string;
  title: string;
  status: string;
  severity: string;
  source: string;
  event_type: string;
  correlation_key: string;
  primary_device_id: number | null;
  primary_source_ip: string | null;
  hostname: string | null;
  site: string | null;
  summary: string;
  ai_summary: string | null;
  probable_root_cause: string | null;
  affected_scope: string[];
  confidence_score: number;
  last_analysis_id: number | null;
  recommendation: string | null;
  assigned_to: string | null;
  assigned_at: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_by: string | null;
  resolution_notes: string | null;
  event_count: number;
  requires_attention: boolean;
  opened_at: string | null;
  updated_at: string | null;
  closed_at: string | null;
  last_event_time: string | null;
  incident_cluster_id: number | null;
}

export interface OpsIncidentFeedback {
  id: number;
  incident_id: number;
  rating: number;
  was_false_positive: boolean;
  resolution_effectiveness: "effective" | "partial" | "ineffective" | "unknown";
  operator_notes: string | null;
  created_by: string;
  created_at: string | null;
}

export interface IncidentChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  artifactId?: number;
}

export interface OpsIncidentCluster {
  id: number;
  title: string;
  status: string;
  root_cause_summary: string | null;
  severity: string;
  member_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface OpsClusterDetail extends OpsIncidentCluster {
  incidents: OpsIncident[];
}

export interface OpsApproval {
  id: number;
  job_id: number | null;
  incident_id: number | null;
  incident_title: string | null;
  title: string;
  status: string;
  execution_status: string;
  failure_category: string | null;
  requested_by: string;
  requested_by_role: LabRole | string;
  reviewed_by: string | null;
  reviewed_by_role: LabRole | string | null;
  executed_by: string | null;
  executed_by_role: LabRole | string | null;
  target_host: string | null;
  action_id: string;
  action: OpsActionCatalogEntry | null;
  commands_text: string | null;
  rollback_commands_text: string | null;
  verify_commands_text: string | null;
  diff_text: string | null;
  rationale: string | null;
  decision_comment: string | null;
  risk_level: string;
  required_approval_role: LabRole | string;
  required_execution_role: LabRole | string;
  readiness: string;
  readiness_score: number;
  policy_snapshot: Record<string, unknown>;
  evidence_snapshot: Record<string, unknown>;
  notes: string | null;
  execution_output: string | null;
  requested_at: string | null;
  decided_at: string | null;
  executed_at: string | null;
  audit_entries: OpsAuditEntry[];
}

export interface OpsIncidentHistoryEntry {
  id: number;
  incident_id: number;
  action: string;
  actor: string;
  actor_role: LabRole | string;
  from_status: string | null;
  to_status: string | null;
  summary: string;
  comment: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface OpsRemediationTask {
  id: number;
  approval_id: number;
  incident_id: number | null;
  phase: string;
  step_order: number;
  command_text: string;
  status: string;
  output_text: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string | null;
}

export interface OpsRemediationStatus {
  approval_id: number | null;
  status: string;
  progress: {
    total: number;
    completed: number;
    failed: number;
  };
  tasks: OpsRemediationTask[];
}

export interface OpsIncidentDetail extends OpsIncident {
  events: OpsEvent[];
  approvals: OpsApproval[];
  jobs: unknown[];
  audits: OpsAuditEntry[];
  artifacts: OpsAIArtifact[];
  history: OpsIncidentHistoryEntry[];
  notifications: unknown[];
  feedback: OpsIncidentFeedback[];
  remediation_status: OpsRemediationStatus;
  available_actions: unknown[];
  latest_analysis: {
    id: number;
    incident_id: number | null;
    decision: string;
    status: string;
    window_start: string | null;
    window_end: string | null;
    input_log_ids: number[];
    open_incident_ids: number[];
    provider: string | null;
    model: string | null;
    prompt_version: string;
    raw_text: string | null;
    output: Record<string, unknown>;
    created_at: string | null;
  } | null;
  latest_log_summary: OpsAIArtifact | null;
  latest_troubleshoot: OpsAIArtifact | null;
  latest_execution_report: OpsAIArtifact | null;
  latest_proposal: OpsApproval | null;
  feedback_summary: {
    count: number;
    latest: OpsIncidentFeedback | null;
  };
}

export interface OpsOverview {
  counts: {
    open_incidents: number;
    pending_approvals: number;
    devices: number;
    events: number;
  };
  open_incidents: OpsIncident[];
  pending_approvals: OpsApproval[];
  recent_execution_reports: OpsApproval[];
  top_event_types: { event_type: string; count: number }[];
}

export interface OpsActionResponse<T = Record<string, unknown>> {
  ok: boolean;
  detail: string;
  data: T;
}

export interface OpsDevicesQuery {
  q?: string;
  site?: string;
  role?: string;
  has_open_incidents?: boolean;
  sort_by?: "hostname" | "site" | "role" | "open_incident_count" | "last_event_time";
  sort_dir?: SortDirection;
  page?: number;
  page_size?: number;
}

export interface OpsIncidentsQuery {
  q?: string;
  status?: string;
  severity?: string;
  site?: string;
  updated_from?: string;
  updated_to?: string;
  sort_by?: "updated_at" | "opened_at" | "closed_at" | "last_event_time" | "severity" | "event_count";
  sort_dir?: SortDirection;
  page?: number;
  page_size?: number;
}

export interface OpsApprovalsQuery {
  q?: string;
  status?: string;
  risk_level?: string;
  sort_by?: "requested_at" | "decided_at" | "executed_at" | "risk_level" | "status";
  sort_dir?: SortDirection;
  page?: number;
  page_size?: number;
}

/* ── Troubleshoot interactive types ─────────────── */

export type TroubleshootPhase =
  | "idle"
  | "planning"
  | "plan_ready"
  | "executing"
  | "round_done"
  | "error";

export interface TroubleshootStep {
  id: string;
  toolName: string;
  stepName: string;
  content: string;
  isError: boolean;
}

export interface TroubleshootRound {
  roundNumber: number;
  planText: string;
  steps: TroubleshootStep[];
  analysisText: string;
  approvalId: number | null;
  artifactId: number | null;
}

export interface TroubleshootState {
  phase: TroubleshootPhase;
  sessionId: string | null;
  currentPlan: string;
  currentStatus: string | null;
  streamingTokens: string;
  currentSteps: TroubleshootStep[];
  rounds: TroubleshootRound[];
  error: string | null;
}
