"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, RefreshCcw, Sparkles, UserCheck, Wrench } from "lucide-react";
import { useOpsIdentity } from "@/components/ops/ops-identity-context";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonSection } from "@/components/ops/ops-skeleton";
import { OpsTabs } from "@/components/ops/ops-tabs";
import { PageHeader } from "@/components/ops/page-header";
import { StatusBadge } from "@/components/ops/status-badge";
import { TimestampItem } from "@/components/ops/timestamp-item";
import { InlineApprovalPanel } from "@/components/ops/inline-approval-panel";
import { EscalationPanel } from "@/components/ops/escalation-panel";
import { ResolveDialog } from "@/components/ops/resolve-dialog";
import { CollapsibleStep } from "@/components/stream/collapsible-step";
import { Button } from "@/components/ui/button";
import { useOpsLoop } from "@/hooks/use-ops-loop";
import type { LiveStep } from "@/hooks/use-ops-loop";
import {
  approveOpsApproval,
  assignIncident,
  executeOpsApproval,
  fetchOpsIncident,
  getErrorMessage,
  investigateOpsIncident,
  rejectOpsApproval,
  retriggerOpsLoop,
  submitIncidentFeedback,
  troubleshootOpsIncident,
  updateIncidentStatus,
} from "@/lib/ops-api";
import {
  OPS_CONTROL_CLASS,
  OPS_ERROR_CLASS,
  OPS_INNER_CARD_CLASS,
  OPS_SECTION_CLASS,
  OPS_SUCCESS_CLASS,
  OPS_TEXT_LINK_CLASS,
} from "@/lib/ops-ui";
import { formatShortTimestamp, formatDuration } from "@/lib/time";
import type { OpsIncidentDetail, OpsLoopStage } from "@/lib/ops-types";

/* ── Helpers (preserved from Codex) ──────────────── */

type ProposalAction = "approve" | "reject" | "execute";

function SectionHeader({
  icon,
  title,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-base font-semibold text-white">{title}</h2>
      </div>
      {action}
    </div>
  );
}

function CommandBlock({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value?.trim()) return null;
  return (
    <details className="rounded-xl border border-white/8 bg-white/[0.03]">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-slate-200">
        {label}
      </summary>
      <pre className="overflow-x-auto border-t border-white/8 px-4 py-3 text-xs leading-6 text-slate-300 whitespace-pre-wrap">
        {value}
      </pre>
    </details>
  );
}

function nestedRecord(value: unknown, key: string): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  const candidate = (value as Record<string, unknown>)[key];
  return candidate && typeof candidate === "object" ? (candidate as Record<string, unknown>) : null;
}

function nestedString(value: unknown, key: string): string | null {
  if (!value || typeof value !== "object") return null;
  const candidate = (value as Record<string, unknown>)[key];
  return typeof candidate === "string" && candidate.trim() ? candidate : null;
}

/* ── Ops Loop Stage Meta ─────────────────────────── */

const LOOP_STAGE_META: Record<string, { label: string; color: string }> = {
  syslog_ingested: { label: "Syslog Ingested", color: "text-slate-400" },
  incident_created: { label: "Incident Created", color: "text-cyan-400" },
  investigation_started: { label: "Investigation Started", color: "text-sky-400" },
  investigation_completed: { label: "Investigation Done", color: "text-sky-300" },
  troubleshoot_started: { label: "Troubleshoot Started", color: "text-amber-400" },
  troubleshoot_completed: { label: "Troubleshoot Done", color: "text-amber-300" },
  troubleshoot_failed: { label: "Troubleshoot Failed", color: "text-rose-400" },
  troubleshoot_deferred: { label: "Troubleshoot Deferred", color: "text-orange-400" },
  proposal_created: { label: "Proposal Created", color: "text-emerald-400" },
  awaiting_approval: { label: "Awaiting Approval", color: "text-amber-300" },
  approval_granted: { label: "Approved", color: "text-emerald-400" },
  approval_rejected: { label: "Rejected", color: "text-rose-400" },
  execution_started: { label: "Execution Started", color: "text-sky-400" },
  execution_succeeded: { label: "Execution Succeeded", color: "text-emerald-400" },
  execution_failed: { label: "Execution Failed", color: "text-rose-400" },
  verification_started: { label: "Verification Started", color: "text-sky-400" },
  verification_passed: { label: "Verification Passed", color: "text-emerald-400" },
  verification_failed: { label: "Verification Failed", color: "text-rose-400" },
  verification_inconclusive: { label: "Verification Inconclusive", color: "text-amber-400" },
  auto_resolved: { label: "Auto-Resolved", color: "text-emerald-400" },
  escalation_needed: { label: "Escalation Needed", color: "text-orange-400" },
  retrigger_requested: { label: "Re-triggered", color: "text-cyan-400" },
  flapping_detected: { label: "Flapping Detected", color: "text-rose-400" },
  recovery_detected: { label: "Recovery Detected", color: "text-emerald-300" },
  ai_health_check_started: { label: "Health Check Started", color: "text-sky-400" },
  health_check_passed: { label: "Health Check Passed", color: "text-emerald-400" },
  health_check_inconclusive: { label: "Health Check Inconclusive", color: "text-amber-400" },
};

/* ── Loop Stage Row ──────────────────────────────── */

function LoopStageRow({ stage }: { stage: OpsLoopStage }) {
  const meta = LOOP_STAGE_META[stage.stage] ?? { label: stage.stage.replace(/_/g, " "), color: "text-slate-400" };
  const steps = stage.payload?.steps as Array<{ step_name: string; content: string; is_error: boolean }> | undefined;
  const [showSteps, setShowSteps] = useState(false);

  return (
    <div className="flex items-start gap-3 border-b border-white/6 py-3 last:border-0">
      <div className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${meta.color.replace("text-", "bg-")}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-medium ${meta.color}`}>{meta.label}</span>
          {stage.timestamp && (
            <span className="text-xs text-slate-500">{formatShortTimestamp(stage.timestamp)}</span>
          )}
        </div>
        {stage.summary && <p className="mt-0.5 text-xs text-slate-400">{stage.summary}</p>}
        {steps && steps.length > 0 && (
          <>
            <button type="button" onClick={() => setShowSteps(!showSteps)}
              className="mt-1 text-xs text-cyan-400 hover:text-cyan-300">
              Commands run ({steps.length}) {showSteps ? "\u25BE" : "\u25B8"}
            </button>
            {showSteps && (
              <div className="mt-2 space-y-1">
                {steps.map((s, i) => (
                  <CollapsibleStep key={i} step={{
                    id: `${stage.stage}-${i}`,
                    name: s.step_name,
                    content: s.content,
                    isError: s.is_error,
                    toolName: "cli",
                  }} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ── Live Troubleshoot Panel ─────────────────────── */

function LiveTroubleshootPanel({ steps, status }: { steps: LiveStep[]; status: string | null }) {
  return (
    <section className={OPS_SECTION_CLASS}>
      <h3 className="mb-3 text-sm font-semibold text-white">Live Troubleshoot</h3>
      {status && <p className="mb-2 animate-pulse text-xs text-cyan-400">{status}</p>}
      <div className="space-y-1">
        {steps.map((step) => (
          <CollapsibleStep key={step.id} step={{
            id: step.id,
            name: step.stepName,
            content: step.content,
            isError: step.isError,
            toolName: step.toolName || "cli",
          }} />
        ))}
      </div>
      {steps.length === 0 && !status && (
        <p className="text-xs text-slate-500">Waiting for troubleshoot commands...</p>
      )}
    </section>
  );
}

/* ── Main Page ───────────────────────────────────── */

export default function OpsIncidentDetailPage() {
  const params = useParams<{ id: string }>();
  const incidentId = Number(params.id);
  const { actorName, actorRole } = useOpsIdentity();

  /* ── Core State ── */
  const [incident, setIncident] = useState<OpsIncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);

  /* ── Tabs ── */
  const [activeTab, setActiveTab] = useState("overview");
  const hasAutoSwitchedTab = useRef(false);

  /* ── Assign ── */
  const [showAssignInput, setShowAssignInput] = useState(false);
  const [assignValue, setAssignValue] = useState("");

  /* ── Resolve Dialog ── */
  const [showResolveDialog, setShowResolveDialog] = useState(false);

  /* ── Feedback (Codex) ── */
  const [reviewComment, setReviewComment] = useState("");
  const [resolutionNote, setResolutionNote] = useState("");
  const [feedbackEffectiveness, setFeedbackEffectiveness] = useState("effective");
  const [feedbackUseful, setFeedbackUseful] = useState(5);
  const [feedbackFalsePositive, setFeedbackFalsePositive] = useState(false);
  const [feedbackNotes, setFeedbackNotes] = useState("");

  /* ── Ops Loop Hook ── */
  const loop = useOpsLoop(incidentId);

  /* ── Derived (Codex) ── */
  const latestProposal = incident?.latest_proposal ?? incident?.approvals?.[0] ?? null;
  const latestTroubleshoot = incident?.latest_troubleshoot ?? null;
  const troubleshootStructured = useMemo(() => nestedRecord(latestTroubleshoot?.content, "structured"), [latestTroubleshoot]);
  const latestExecutionReport = incident?.latest_execution_report ?? null;
  const proposalFromTroubleshoot = troubleshootStructured?.proposal && typeof troubleshootStructured.proposal === "object"
    ? (troubleshootStructured.proposal as Record<string, unknown>)
    : null;

  /* ── Load Incident ── */
  async function loadIncident() {
    const payload = await fetchOpsIncident(incidentId);
    setIncident(payload);
    setError(null);
  }

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsIncident(incidentId)
      .then((payload) => { if (!cancelled) { setIncident(payload); setError(null); } })
      .catch((err) => { if (!cancelled) setError(getErrorMessage(err)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  }, [incidentId]);

  /* ── Auto-tab switch (once) ── */
  useEffect(() => {
    if (!hasAutoSwitchedTab.current && loop.stages.length > 0 && activeTab === "overview") {
      setActiveTab("ops-loop");
      hasAutoSwitchedTab.current = true;
    }
  }, [loop.stages.length, activeTab]);

  /* ── Handlers ── */
  async function handleRefreshSummary() {
    setBusyAction("summary");
    try {
      const result = await investigateOpsIncident(incidentId, actorName, actorRole);
      setMessage(result.detail);
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleTroubleshoot() {
    setBusyAction("troubleshoot");
    try {
      const result = await troubleshootOpsIncident(incidentId, actorName, actorRole);
      setMessage(result.detail);
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleProposalAction(action: ProposalAction) {
    if (!latestProposal) return;
    if (action === "reject" && !reviewComment.trim()) {
      setError("Please enter a reason before rejecting this proposal.");
      return;
    }
    setBusyAction(action);
    try {
      if (action === "approve") {
        const result = await approveOpsApproval(latestProposal.id, actorName, actorRole, reviewComment || undefined);
        setMessage(result.detail);
      } else if (action === "reject") {
        const result = await rejectOpsApproval(latestProposal.id, actorName, actorRole, reviewComment);
        setMessage(result.detail);
      } else {
        const result = await executeOpsApproval(latestProposal.id, actorName, actorRole);
        setMessage(result.detail);
      }
      setReviewComment("");
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleAssign() {
    if (!assignValue.trim()) return;
    setBusyAction("assign");
    try {
      await assignIncident(incidentId, assignValue.trim(), actorName, actorRole);
      setMessage(`Assigned to ${assignValue.trim()}`);
      setShowAssignInput(false);
      setAssignValue("");
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleRetrigger(mode: "full" | "troubleshoot_only") {
    setBusyAction("retrigger");
    try {
      const result = await retriggerOpsLoop(incidentId, mode, actorName, actorRole);
      setMessage(result.detail);
      loop.refresh();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleResolve(notes: string) {
    setBusyAction("resolve");
    try {
      await updateIncidentStatus(incidentId, "resolved", notes, actorName, actorRole);
      setMessage("Incident resolved.");
      setShowResolveDialog(false);
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleResolveAndFeedback() {
    if (!incident) return;
    if (!resolutionNote.trim()) {
      setError("Please add a short resolution note before closing the incident.");
      return;
    }
    setBusyAction("feedback");
    try {
      await submitIncidentFeedback(incident.id, {
        rating: feedbackUseful,
        was_false_positive: feedbackFalsePositive,
        resolution_effectiveness: feedbackEffectiveness,
        operator_notes: feedbackNotes || resolutionNote,
        created_by: actorName,
      });
      await updateIncidentStatus(incident.id, "resolved", resolutionNote, actorName, actorRole);
      setMessage("Incident feedback recorded and incident closed.");
      setResolutionNote("");
      setFeedbackNotes("");
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  async function handleStatusChange(status: string) {
    setBusyAction("status");
    try {
      await updateIncidentStatus(incidentId, status, undefined, actorName, actorRole);
      await loadIncident();
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusyAction(null); }
  }

  /* ── Guards ── */
  if (Number.isNaN(incidentId)) {
    return <div className="px-6 py-10 text-sm text-rose-300 sm:px-8">Invalid incident id.</div>;
  }

  if (isLoading && !incident) {
    return (
      <div className="space-y-5 px-6 py-6 sm:px-8">
        <SkeletonSection lines={4} />
        <SkeletonSection lines={4} />
        <SkeletonSection lines={4} />
      </div>
    );
  }

  if (!incident) {
    return (
      <div className="px-6 py-10 sm:px-8">
        <OpsEmptyState icon={AlertTriangle} title={error || "Incident not found."} />
      </div>
    );
  }

  return (
    <div className="min-h-full">
      {/* ── Header ── */}
      <PageHeader
        eyebrow={incident.incident_no}
        title={incident.title}
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Incidents", href: "/ops/incidents" }, { label: incident.incident_no }]} />}
        actions={(
          <div className="flex items-center gap-2">
            {incident.status !== "resolved" && (
              showAssignInput ? (
                <div className="flex items-center gap-2">
                  <input
                    value={assignValue}
                    onChange={(e) => setAssignValue(e.target.value)}
                    placeholder="Assignee name..."
                    className={`${OPS_CONTROL_CLASS} !py-1.5 !text-sm w-40`}
                  />
                  <Button size="sm" onClick={() => { void handleAssign(); }} disabled={busyAction === "assign"}>
                    {busyAction === "assign" ? "..." : "Save"}
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => { setShowAssignInput(false); setAssignValue(""); }}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button variant="outline" size="sm" onClick={() => setShowAssignInput(true)}>
                  <UserCheck className="size-4" />
                  {incident.assigned_to ? `Assigned: ${incident.assigned_to}` : "Assign"}
                </Button>
              )
            )}
            <Button variant="outline" onClick={async () => { setBusyAction("refresh"); try { await loadIncident(); } catch (err) { setError(getErrorMessage(err)); } finally { setBusyAction(null); } }} disabled={!!busyAction}>
              <RefreshCcw className="size-4" />
              {busyAction === "refresh" ? "Refreshing..." : "Refresh"}
            </Button>
          </div>
        )}
      />

      {/* ── Metadata Strip ── */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-white/6 px-6 py-3 text-xs text-slate-400 sm:px-8">
        <StatusBadge value={incident.severity} />
        <StatusBadge value={incident.status} />
        <span>Confidence {incident.confidence_score}/100</span>
        {incident.assigned_to && (
          <span>Assigned to: <span className="text-slate-200">{incident.assigned_to}</span>
            {incident.assigned_at && <> ({formatShortTimestamp(incident.assigned_at)})</>}
          </span>
        )}
        {incident.acknowledged_by && (
          <span>Ack: <span className="text-slate-200">{incident.acknowledged_by}</span>
            {incident.acknowledged_at && <> ({formatShortTimestamp(incident.acknowledged_at)})</>}
          </span>
        )}
        {incident.status === "resolved" && incident.closed_at && (
          <span>Closed: {formatShortTimestamp(incident.closed_at)}</span>
        )}
        {incident.status === "resolved" && incident.opened_at && incident.closed_at && (
          <span>Duration: {formatDuration(incident.opened_at, incident.closed_at)}</span>
        )}
        {/* Quick status actions */}
        {incident.status === "new" && (
          <Button variant="outline" size="sm" onClick={() => { void handleStatusChange("acknowledged"); }} disabled={!!busyAction}>
            Acknowledge
          </Button>
        )}
        {incident.status !== "resolved" && (
          <Button variant="outline" size="sm" onClick={() => setShowResolveDialog(true)} disabled={!!busyAction}>
            Resolve
          </Button>
        )}
      </div>

      {/* ── Tabs ── */}
      <OpsTabs
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "ops-loop", label: "Ops Loop", badge: loop.stages.length || undefined },
          { id: "events", label: "Events", badge: incident.events.length || undefined },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
      />

      {/* ── Alerts ── */}
      <div className="px-6 sm:px-8">
        {message ? <div className={`mt-4 ${OPS_SUCCESS_CLASS}`}>{message}</div> : null}
        {error ? <div className={`mt-4 ${OPS_ERROR_CLASS}`}>{error}</div> : null}
      </div>

      {/* ══════════════════════════════════════════════ */}
      {/* TAB: Overview                                 */}
      {/* ══════════════════════════════════════════════ */}
      {activeTab === "overview" && (
        <div className="space-y-5 px-6 py-6 sm:px-8">
          {/* AI Summary */}
          <section className={OPS_SECTION_CLASS}>
            <SectionHeader
              icon={<Sparkles className="size-4 text-cyan-300" />}
              title="AI Summary from Logs"
              action={(
                <Button variant="outline" size="sm" onClick={() => { void handleRefreshSummary(); }} disabled={busyAction === "summary"}>
                  {busyAction === "summary" ? "Refreshing..." : "Refresh summary"}
                </Button>
              )}
            />
            <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_18rem]">
              <div className={OPS_INNER_CARD_CLASS}>
                <p className="whitespace-pre-wrap text-sm leading-7 text-slate-200">
                  {incident.ai_summary || "No AI summary has been generated yet."}
                </p>
              </div>
              <div className="space-y-3">
                <div className={OPS_INNER_CARD_CLASS}>
                  <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Probable Root Cause</p>
                  <p className="mt-2 text-sm leading-6 text-slate-200">
                    {incident.probable_root_cause || "Still being determined from the logs."}
                  </p>
                </div>
                <div className={OPS_INNER_CARD_CLASS}>
                  <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Affected Scope</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {incident.affected_scope.length > 0 ? incident.affected_scope.map((item) => (
                      <span key={item} className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-1 text-xs text-slate-300">
                        {item}
                      </span>
                    )) : <span className="text-sm text-slate-400">No scope captured yet.</span>}
                  </div>
                </div>
                <div className={OPS_INNER_CARD_CLASS}>
                  <TimestampItem label="Last updated" value={incident.updated_at} emptyLabel="—" />
                </div>
              </div>
            </div>
          </section>

          {/* Troubleshoot */}
          <section className={OPS_SECTION_CLASS}>
            <SectionHeader
              icon={<Wrench className="size-4 text-amber-300" />}
              title="Troubleshoot with AI"
              action={(
                <Button onClick={() => { void handleTroubleshoot(); }} disabled={busyAction === "troubleshoot"}>
                  {busyAction === "troubleshoot" ? "Running..." : "Run troubleshoot"}
                </Button>
              )}
            />
            <div className="mt-4 space-y-4">
              {latestTroubleshoot ? (
                <>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_16rem]">
                    <div className={OPS_INNER_CARD_CLASS}>
                      <p className="text-sm leading-7 text-slate-200">
                        {latestTroubleshoot.summary || "No troubleshoot summary available."}
                      </p>
                      {troubleshootStructured?.recommended_next_action ? (
                        <p className="mt-4 text-sm text-slate-300">
                          Next action: {String(troubleshootStructured.recommended_next_action)}
                        </p>
                      ) : null}
                    </div>
                    <div className="space-y-3">
                      <div className={OPS_INNER_CARD_CLASS}>
                        <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Diagnosis</p>
                        <div className="mt-2 flex items-center gap-2">
                          <StatusBadge value={String(troubleshootStructured?.diagnosis_type || "unknown")} />
                        </div>
                      </div>
                      <div className={OPS_INNER_CARD_CLASS}>
                        <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Evidence refs</p>
                        <p className="mt-2 text-sm text-slate-300">
                          {Array.isArray(troubleshootStructured?.evidence_refs) && troubleshootStructured.evidence_refs.length > 0
                            ? (troubleshootStructured.evidence_refs as unknown[]).join(", ")
                            : "No explicit evidence refs captured."}
                        </p>
                      </div>
                    </div>
                  </div>
                  <details className="rounded-xl border border-white/8 bg-white/[0.03]">
                    <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-slate-200">
                      More details
                    </summary>
                    <div className="border-t border-white/8 px-4 py-4">
                      <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Raw troubleshoot output</p>
                      <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-slate-300">
                        {String(
                          nestedString(latestTroubleshoot.content, "raw_text")
                            || nestedString(troubleshootStructured, "summary")
                            || latestTroubleshoot.summary
                            || "",
                        )}
                      </pre>
                    </div>
                  </details>
                </>
              ) : (
                <OpsEmptyState icon={Wrench} title="No troubleshoot report yet. Run AI troubleshoot when you want deeper evidence from devices." />
              )}
            </div>
          </section>

          {/* Proposed Fix / Approval */}
          <section className={OPS_SECTION_CLASS}>
            <SectionHeader icon={<CheckCircle2 className="size-4 text-emerald-300" />} title="Proposed Fix / Approval" />
            <div className="mt-4 space-y-4">
              {latestProposal ? (
                <>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_16rem]">
                    <div className={OPS_INNER_CARD_CLASS}>
                      <div className="flex items-center gap-2">
                        <StatusBadge value={latestProposal.status} />
                        <StatusBadge value={latestProposal.risk_level} />
                      </div>
                      <p className="mt-3 text-sm leading-7 text-slate-200">
                        {latestProposal.rationale || "No rationale captured."}
                      </p>
                      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                        <span>Approval role: {latestProposal.required_approval_role}</span>
                        <span>Execution role: {latestProposal.required_execution_role}</span>
                        <Link href="/ops/approvals" className={OPS_TEXT_LINK_CLASS}>Open approvals</Link>
                      </div>
                    </div>
                    <div className={OPS_INNER_CARD_CLASS}>
                      <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Target</p>
                      <p className="mt-2 text-sm text-slate-200">{latestProposal.target_host || "No target"}</p>
                      <p className="mt-4 text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Execution state</p>
                      <div className="mt-2">
                        <StatusBadge value={latestProposal.execution_status} />
                      </div>
                    </div>
                  </div>

                  <CommandBlock label="Commands to run" value={latestProposal.commands_text} />
                  <CommandBlock label="Verification commands" value={latestProposal.verify_commands_text} />
                  <CommandBlock label="Rollback commands" value={latestProposal.rollback_commands_text} />

                  {["pending", "awaiting_second_approval"].includes(latestProposal.status) ? (
                    <div className="rounded-xl border border-white/8 bg-white/[0.03] p-4">
                      <p className="text-sm text-slate-300">
                        Approving this proposal will immediately trigger execution in the lab flow.
                      </p>
                      <textarea
                        rows={3}
                        value={reviewComment}
                        onChange={(event) => setReviewComment(event.target.value)}
                        placeholder="Optional approval note. Required only if you reject."
                        className={`mt-3 ${OPS_CONTROL_CLASS}`}
                      />
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button onClick={() => { void handleProposalAction("approve"); }} disabled={busyAction === "approve"}>
                          {busyAction === "approve" ? "Approving..." : "Approve and run"}
                        </Button>
                        <Button variant="outline" onClick={() => { void handleProposalAction("reject"); }} disabled={busyAction === "reject"}>
                          {busyAction === "reject" ? "Rejecting..." : "Reject"}
                        </Button>
                      </div>
                    </div>
                  ) : latestProposal.status === "approved" ? (
                    <div className="rounded-xl border border-white/8 bg-white/[0.03] p-4">
                      <p className="text-sm text-slate-300">This proposal is approved but not executed yet.</p>
                      <div className="mt-3">
                        <Button onClick={() => { void handleProposalAction("execute"); }} disabled={busyAction === "execute"}>
                          {busyAction === "execute" ? "Executing..." : "Execute fallback"}
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </>
              ) : (
                <OpsEmptyState
                  icon={CheckCircle2}
                  title={
                    proposalFromTroubleshoot
                      ? "A proposal was detected in troubleshoot output but has not been stored yet."
                      : "No config fix has been proposed."
                  }
                  description={
                    troubleshootStructured?.diagnosis_type === "physical" || troubleshootStructured?.diagnosis_type === "provider"
                      ? "This incident currently looks like a non-config problem, so the platform is keeping it recommendation-only."
                      : "Run AI troubleshoot when you want the system to decide whether a remediation proposal is safe to create."
                  }
                />
              )}
            </div>
          </section>

          {/* Execution Report + Feedback */}
          <section className={OPS_SECTION_CLASS}>
            <SectionHeader icon={<CheckCircle2 className="size-4 text-cyan-300" />} title="Execution Report / Feedback" />
            <div className="mt-4 space-y-4">
              {latestExecutionReport || latestProposal ? (
                <>
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_16rem]">
                    <div className={OPS_INNER_CARD_CLASS}>
                      <p className="text-sm leading-7 text-slate-200">
                        {latestExecutionReport?.summary || latestProposal?.execution_output || "Execution has not produced a report yet."}
                      </p>
                    </div>
                    <div className={OPS_INNER_CARD_CLASS}>
                      <p className="text-[0.68rem] uppercase tracking-[0.18em] text-slate-500">Remediation status</p>
                      <div className="mt-2">
                        <StatusBadge value={incident.remediation_status.status} />
                      </div>
                      <p className="mt-3 text-xs text-slate-500">
                        {incident.remediation_status.progress.completed}/{incident.remediation_status.progress.total} steps completed
                      </p>
                    </div>
                  </div>
                  <CommandBlock label="Execution output" value={latestProposal?.execution_output} />
                </>
              ) : (
                <OpsEmptyState icon={CheckCircle2} title="No execution report yet." />
              )}

              <div className="rounded-xl border border-white/8 bg-white/[0.03] p-4">
                <p className="text-sm font-medium text-white">Close incident and record feedback</p>
                <textarea
                  rows={3}
                  value={resolutionNote}
                  onChange={(event) => setResolutionNote(event.target.value)}
                  placeholder="What was resolved, or what should the next human do if this is a physical/provider issue?"
                  className={`mt-3 ${OPS_CONTROL_CLASS}`}
                />
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  <select
                    value={feedbackEffectiveness}
                    onChange={(event) => setFeedbackEffectiveness(event.target.value)}
                    className={OPS_CONTROL_CLASS}
                  >
                    <option value="effective">Action was effective</option>
                    <option value="partial">Partially effective</option>
                    <option value="ineffective">Not effective</option>
                    <option value="unknown">Not sure yet</option>
                  </select>
                  <select
                    value={feedbackUseful}
                    onChange={(event) => setFeedbackUseful(Number(event.target.value))}
                    className={OPS_CONTROL_CLASS}
                  >
                    {[5, 4, 3, 2, 1].map((rating) => (
                      <option key={rating} value={rating}>AI usefulness {rating}/5</option>
                    ))}
                  </select>
                  <label className="flex items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 py-2 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={feedbackFalsePositive}
                      onChange={(event) => setFeedbackFalsePositive(event.target.checked)}
                    />
                    False positive
                  </label>
                </div>
                <textarea
                  rows={2}
                  value={feedbackNotes}
                  onChange={(event) => setFeedbackNotes(event.target.value)}
                  placeholder="Optional feedback for future incident analysis."
                  className={`mt-3 ${OPS_CONTROL_CLASS}`}
                />
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <Button onClick={() => { void handleResolveAndFeedback(); }} disabled={busyAction === "feedback"}>
                    {busyAction === "feedback" ? "Closing..." : "Close incident"}
                  </Button>
                  {incident.feedback_summary.count > 0 ? (
                    <span className="text-sm text-slate-400">
                      {incident.feedback_summary.count} feedback entr{incident.feedback_summary.count === 1 ? "y" : "ies"} recorded
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          </section>
        </div>
      )}

      {/* ══════════════════════════════════════════════ */}
      {/* TAB: Ops Loop                                 */}
      {/* ══════════════════════════════════════════════ */}
      {activeTab === "ops-loop" && (
        <div className="space-y-5 px-6 py-6 sm:px-8">
          {/* Timeline */}
          <section className={OPS_SECTION_CLASS}>
            <h2 className="mb-3 text-base font-semibold text-white">Ops Loop Timeline</h2>
            {loop.stages.length > 0 ? (
              loop.stages.map((stage, i) => <LoopStageRow key={`${stage.stage}-${i}`} stage={stage} />)
            ) : (
              <OpsEmptyState icon={Sparkles} title="No ops loop activity yet." />
            )}
          </section>

          {/* Live troubleshoot streaming */}
          {loop.isTroubleshooting && (
            <LiveTroubleshootPanel steps={loop.liveSteps} status={loop.liveStatus} />
          )}

          {/* Inline approval (when latest approval is pending) */}
          {loop.latestApprovalId && loop.latestApprovalStatus &&
            ["pending", "awaiting_second_approval", "approved"].includes(loop.latestApprovalStatus) &&
            incident.approvals && (() => {
              const approval = incident.approvals.find(a => a.id === loop.latestApprovalId);
              return approval ? (
                <InlineApprovalPanel
                  approval={approval}
                  actorName={actorName}
                  actorRole={actorRole}
                  onActionComplete={() => { void loadIncident(); loop.refresh(); }}
                />
              ) : null;
            })()}

          {/* Escalation */}
          {loop.terminalState === "escalated" && (
            <EscalationPanel
              reason={loop.escalationContext?.root_cause || "AI could not propose an automated fix."}
              escalationContext={loop.escalationContext}
              availableActions={loop.availableActions}
              busy={busyAction === "retrigger"}
              onRetrigger={handleRetrigger}
              onResolve={() => setShowResolveDialog(true)}
            />
          )}

          {/* Action Required (terminal states needing action) */}
          {loop.terminalState === "needs_action" && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.05] p-5">
              <h3 className="mb-3 text-sm font-semibold text-amber-100">Action Required</h3>
              <div className="flex flex-wrap gap-2">
                {loop.availableActions.includes("retrigger_full") && (
                  <Button variant="outline" size="sm" disabled={busyAction === "retrigger"}
                    onClick={() => { void handleRetrigger("full"); }}>
                    Re-investigate &amp; Troubleshoot
                  </Button>
                )}
                {loop.availableActions.includes("retrigger_troubleshoot") && (
                  <Button variant="outline" size="sm" disabled={busyAction === "retrigger"}
                    onClick={() => { void handleRetrigger("troubleshoot_only"); }}>
                    Re-troubleshoot
                  </Button>
                )}
                {loop.availableActions.includes("resolve_manual") && (
                  <Button variant="outline" size="sm" onClick={() => setShowResolveDialog(true)}>
                    Resolve Manually
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════ */}
      {/* TAB: Events                                   */}
      {/* ══════════════════════════════════════════════ */}
      {activeTab === "events" && (
        <div className="space-y-5 px-6 py-6 sm:px-8">
          <section className={OPS_SECTION_CLASS}>
            <h2 className="mb-3 text-base font-semibold text-white">Linked Events ({incident.events.length})</h2>
            {incident.events.length > 0 ? (
              <div className="space-y-2">
                {incident.events.map((event) => (
                  <div key={event.id} className={OPS_INNER_CARD_CLASS}>
                    <div className="flex items-center gap-2 text-xs text-slate-500">
                      {event.event_time && <span>{formatShortTimestamp(event.event_time)}</span>}
                      <StatusBadge value={event.event_type} />
                    </div>
                    <p className="mt-1 text-sm text-slate-200">{event.summary}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {event.hostname ?? event.source_ip}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <OpsEmptyState icon={AlertTriangle} title="No linked events." />
            )}
          </section>
        </div>
      )}

      {/* ── Resolve Dialog ── */}
      <ResolveDialog
        open={showResolveDialog}
        busy={busyAction === "resolve"}
        onOpenChange={setShowResolveDialog}
        onConfirm={(notes) => { void handleResolve(notes); }}
      />
    </div>
  );
}
