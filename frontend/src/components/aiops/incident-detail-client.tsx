"use client";

import Link from "next/link";
import { useState, useTransition, useEffect, useCallback } from "react";
import {
  Activity, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight,
  ChevronUp, Clock, Copy, Eye, FileSearch, Loader2, MessageSquare, PenLine,
  Play, RotateCcw, Send, Server, ShieldAlert, Terminal, Wifi, Wrench, XCircle,
} from "lucide-react";
import type { AIOpsIncidentDetailPayload, AIOpsTimelineEntry } from "@/lib/aiops-types";
import {
  addIncidentNote, approveProposal, executeProposal, fetchIncidentDetail,
  runTroubleshoot, submitRecoveryDecision,
} from "@/lib/aiops-api";
import { StatusBadge } from "@/components/aiops/status-badge";
import { IncidentChat } from "@/components/aiops/incident-chat";

const POLL_INTERVAL = 15_000;

/* ─────────────────── Utilities ─────────────────── */

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtTime(v: string | null | undefined) {
  if (!v) return "—";
  return new Date(v).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" });
}

function dur(from: string, to?: string | null) {
  const ms = (to ? new Date(to) : new Date()).getTime() - new Date(from).getTime();
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}

/* ─────────────────── Micro-components ─────────────────── */

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={async () => { await navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500); }}
      title="Copy"
      className="rounded px-1.5 py-0.5 text-[0.65rem] text-slate-700 transition hover:bg-white/[0.06] hover:text-slate-300"
    >
      {ok ? "✓" : <Copy className="h-3 w-3" />}
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 text-[0.65rem] font-bold uppercase tracking-[0.14em] text-slate-600">{children}</p>
  );
}

function Divider() {
  return <div className="border-t border-white/[0.06]" />;
}

/* ─────────────────── Terminal / code blocks ─────────────────── */

function TermBlock({ title, lines, color = "text-slate-200" }: { title: string; lines: string[]; color?: string }) {
  if (!lines.length) return null;
  return (
    <div className="overflow-hidden rounded border border-white/[0.07] bg-[#080c16]">
      <div className="flex items-center justify-between bg-white/[0.025] px-3 py-1.5">
        <span className="flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-widest text-slate-600">
          <Terminal className="h-3 w-3" />{title}
        </span>
        <CopyBtn text={lines.join("\n")} />
      </div>
      <div className={`px-3 py-2.5 font-mono text-[0.78rem] leading-[1.75] ${color}`}>
        {lines.map((l, i) => (
          <div key={i} className="flex gap-2">
            <span className="select-none text-slate-700">$</span>
            <span>{l}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function OutputBlock({ title, content }: { title: string; content: string }) {
  const [exp, setExp] = useState(false);
  const lines = content.trim().split("\n");
  return (
    <div className="overflow-hidden rounded border border-white/[0.06] bg-[#060a12]">
      <div className="flex items-center justify-between bg-white/[0.015] px-3 py-1.5">
        <span className="text-[0.63rem] font-medium uppercase tracking-widest text-slate-700">{title}</span>
        <CopyBtn text={content} />
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 font-mono text-[0.74rem] leading-[1.65] text-slate-400 whitespace-pre-wrap">
        {exp ? content.trim() : lines.slice(0, 7).join("\n")}
        {!exp && lines.length > 7 && <span className="text-slate-700">{"\n…"}</span>}
      </pre>
      {lines.length > 7 && (
        <button onClick={() => setExp(v => !v)}
          className="flex w-full items-center justify-center gap-1 border-t border-white/[0.05] py-1.5 text-[0.65rem] text-slate-700 hover:text-slate-400">
          {exp ? <><ChevronUp className="h-3 w-3" />collapse</> : <><ChevronDown className="h-3 w-3" />{lines.length - 7} more lines</>}
        </button>
      )}
    </div>
  );
}

/* ─────────────────── Disposition banner ─────────────────── */

const DISP: Record<string, { icon: React.ElementType; color: string; bg: string; border: string; label: string; sub: string }> = {
  config_fix_possible:  { icon: CheckCircle2,  color: "text-emerald-300", bg: "bg-emerald-500/[0.07]", border: "border-emerald-500/25", label: "Config Fix Identified",   sub: "AI found a config-level root cause and generated a remediation plan." },
  physical_issue:       { icon: AlertTriangle, color: "text-orange-300",  bg: "bg-orange-500/[0.07]",  border: "border-orange-500/25",  label: "Physical / Hardware",      sub: "Cannot be fixed via config push. On-site inspection or hardware replacement required." },
  external_issue:       { icon: Wifi,          color: "text-rose-300",    bg: "bg-rose-500/[0.07]",    border: "border-rose-500/25",    label: "External / Provider",      sub: "Root cause is outside this device. Contact upstream provider or circuit owner." },
  self_recovered:       { icon: CheckCircle2,  color: "text-sky-300",     bg: "bg-sky-500/[0.07]",     border: "border-sky-500/25",     label: "Self-Recovered",           sub: "Network healed on its own. Verify stability before closing." },
  monitor_further:      { icon: Activity,      color: "text-amber-300",   bg: "bg-amber-500/[0.07]",   border: "border-amber-500/25",   label: "Monitor Further",          sub: "Insufficient evidence to act now. Continue observing." },
  needs_human_review:   { icon: ShieldAlert,   color: "text-fuchsia-300", bg: "bg-fuchsia-500/[0.07]", border: "border-fuchsia-500/25", label: "Needs Human Review",       sub: "AI confidence too low. Manual inspection required." },
};

function DispositionBanner({ disposition, summary }: { disposition: string; summary?: string }) {
  const d = DISP[disposition] ?? { icon: FileSearch, color: "text-slate-300", bg: "bg-white/[0.03]", border: "border-white/10", label: disposition.replaceAll("_", " "), sub: "" };
  const Icon = d.icon;
  return (
    <div className={`flex items-start gap-3 rounded-lg border px-4 py-3.5 ${d.bg} ${d.border}`}>
      <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${d.color}`} />
      <div className="min-w-0">
        <p className={`text-[0.9rem] font-bold ${d.color}`}>{d.label}</p>
        <p className="mt-0.5 text-[0.78rem] text-slate-500">{summary || d.sub}</p>
      </div>
    </div>
  );
}


/* ─────────────────── Remediation plan ─────────────────── */

function RemediationPlan({ data, withAction, actionLoading }: {
  data: AIOpsIncidentDetailPayload;
  withAction: (n: string, fn: () => Promise<AIOpsIncidentDetailPayload>) => void;
  actionLoading: string | null;
}) {
  const { proposal, execution } = data;
  if (!proposal) return null;

  const isPending  = proposal.status === "pending";
  const isApproved = proposal.status === "approved" && !execution;
  const isDone     = !isPending && !isApproved;
  const isBusy     = !!actionLoading;
  const incNo      = data.incident.incident_no;

  const riskCls = proposal.risk_level === "high"
    ? "text-rose-300 border-rose-500/30 bg-rose-500/10"
    : proposal.risk_level === "medium"
    ? "text-amber-300 border-amber-500/30 bg-amber-500/10"
    : "text-emerald-300 border-emerald-500/30 bg-emerald-500/10";

  return (
    <div id="remediation" className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2">
          <ShieldAlert className={`h-3.5 w-3.5 ${isDone ? "text-slate-600" : "text-fuchsia-400"}`} />
          <span className="text-[0.82rem] font-semibold text-white">Remediation Plan</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded border px-1.5 py-0.5 text-[0.62rem] font-bold uppercase tracking-wide ${riskCls}`}>{proposal.risk_level} risk</span>
          <StatusBadge value={proposal.status} />
        </div>
      </div>

      <div className="space-y-4 p-4">
        {/* Title + rationale */}
        <div>
          <p className="text-[0.88rem] font-semibold text-slate-100">{proposal.title}</p>
          <p className="mt-1 text-[0.79rem] leading-6 text-slate-400">{proposal.rationale}</p>
          {proposal.target_devices?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {proposal.target_devices.map(d => (
                <span key={d} className="rounded border border-cyan-500/20 bg-cyan-500/[0.06] px-2 py-0.5 font-mono text-[0.73rem] text-cyan-300">
                  <Server className="mr-1 inline h-3 w-3 opacity-60" />{d}
                </span>
              ))}
            </div>
          )}
        </div>

        <Divider />

        {/* Commands grid */}
        <div className="grid gap-3 sm:grid-cols-2">
          {proposal.commands?.length > 0 && (
            <TermBlock title="Apply" lines={proposal.commands} color="text-cyan-300" />
          )}
          {proposal.verification_commands?.length > 0 && (
            <TermBlock title="Verify after" lines={proposal.verification_commands} color="text-emerald-300" />
          )}
        </div>

        {/* Rollback */}
        {proposal.rollback_plan && (
          <div className="flex gap-2 rounded border border-amber-500/15 bg-amber-500/[0.04] px-3 py-2.5">
            <RotateCcw className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500/60" />
            <div>
              <p className="text-[0.65rem] font-semibold uppercase tracking-widest text-amber-600">Rollback plan</p>
              <p className="mt-0.5 text-[0.77rem] leading-5 text-amber-300/80">{proposal.rollback_plan}</p>
            </div>
          </div>
        )}

        {/* Execution result */}
        {execution && (
          <>
            <Divider />
            <div className={`rounded border p-3 ${execution.status === "completed" ? "border-emerald-500/20 bg-emerald-500/[0.04]" : "border-rose-500/20 bg-rose-500/[0.04]"}`}>
              <div className="mb-2 flex items-center gap-2">
                {execution.status === "completed" ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> : <XCircle className="h-3.5 w-3.5 text-rose-400" />}
                <span className={`text-[0.8rem] font-semibold ${execution.status === "completed" ? "text-emerald-300" : "text-rose-300"}`}>
                  Execution {execution.status === "completed" ? "completed" : "failed"}
                </span>
                {execution.completed_at && <span className="ml-auto text-[0.67rem] text-slate-600">{fmtTime(execution.completed_at)}</span>}
              </div>
              {execution.output && <OutputBlock title="Device output" content={execution.output} />}
              {execution.verification_notes && (
                <div className="mt-2"><OutputBlock title="Verification output" content={execution.verification_notes} /></div>
              )}
            </div>
          </>
        )}

        {/* Action buttons */}
        {!isDone && (
          <>
            <Divider />
            <div className="flex items-center gap-3">
              {isPending && (
                <button onClick={() => withAction("approve", () => approveProposal(incNo, "lab-operator"))} disabled={isBusy}
                  className="inline-flex items-center gap-2 rounded border border-fuchsia-500/30 bg-fuchsia-500/10 px-4 py-2 text-[0.82rem] font-semibold text-fuchsia-300 transition hover:bg-fuchsia-500/20 disabled:opacity-40">
                  {actionLoading === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldAlert className="h-3.5 w-3.5" />}
                  {actionLoading === "approve" ? "Approving…" : "Approve"}
                </button>
              )}
              {isApproved && (
                <button onClick={() => withAction("execute", () => executeProposal(incNo, "lab-operator"))} disabled={isBusy}
                  className="inline-flex items-center gap-2 rounded border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-[0.82rem] font-semibold text-emerald-300 transition hover:bg-emerald-500/20 disabled:opacity-40">
                  {actionLoading === "execute" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                  {actionLoading === "execute" ? "Executing on device…" : "Execute Now"}
                </button>
              )}
              {actionLoading === "execute" && (
                <span className="text-[0.75rem] text-slate-500 animate-pulse">SSH → applying config…</span>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────── Investigation evidence ─────────────────── */

function CliStep({ step, index }: { step: { tool_name: string; args: Record<string, unknown>; content: string }; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const cmd = (step.args?.command as string) ?? (step.args?.hostname as string) ?? step.tool_name;
  return (
    <div className="overflow-hidden rounded border border-white/[0.07] bg-[#080c16]">
      <div role="button" tabIndex={0} onClick={() => setOpen(v => !v)} onKeyDown={e => e.key === "Enter" && setOpen(v => !v)}
        className="flex w-full cursor-pointer items-center gap-3 px-3 py-2.5 text-left hover:bg-white/[0.02]">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-white/[0.06] font-mono text-[0.62rem] font-bold text-slate-500">{index + 1}</span>
        <Terminal className="h-3.5 w-3.5 shrink-0 text-slate-700" />
        <code className="flex-1 truncate font-mono text-[0.8rem] text-cyan-300">{cmd}</code>
        <CopyBtn text={step.content} />
        {open ? <ChevronUp className="h-3.5 w-3.5 text-slate-700" /> : <ChevronRight className="h-3.5 w-3.5 text-slate-700" />}
      </div>
      {open && step.content && (
        <div className="border-t border-white/[0.05]">
          <pre className="max-h-52 overflow-auto px-4 py-3 font-mono text-[0.74rem] leading-[1.65] text-slate-300 whitespace-pre">{step.content}</pre>
        </div>
      )}
    </div>
  );
}

function InvestigationSection({ data, loading }: { data: AIOpsIncidentDetailPayload; loading?: boolean }) {
  const { troubleshoot, ai_summary } = data;

  /* ── Loading state ── */
  if (loading) {
    return (
      <div id="investigation" className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
        <div className="border-b border-white/[0.07] px-4 py-3 flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan-500" />
          <span className="text-[0.82rem] font-semibold text-white">AI Investigation</span>
          <span className="ml-1.5 text-[0.72rem] text-slate-500 animate-pulse">SSHing into device, running diagnostics…</span>
        </div>
        <div className="space-y-3 p-4">
          {[80, 60, 72].map((w, i) => (
            <div key={i} className="h-3 rounded bg-white/[0.04] animate-pulse" style={{ width: `${w}%` }} />
          ))}
        </div>
      </div>
    );
  }

  /* ── No troubleshoot yet — show ai_summary as initial analysis ── */
  if (!troubleshoot) {
    if (!ai_summary) return null;
    const pct = Math.round((ai_summary.confidence_score ?? 0) * 100);
    return (
      <div id="investigation" className="overflow-hidden rounded-lg border border-amber-500/15 bg-[#0c1220]">
        <div className="border-b border-white/[0.07] px-4 py-3 flex items-center gap-2">
          <FileSearch className="h-3.5 w-3.5 text-amber-400/70" />
          <span className="text-[0.82rem] font-semibold text-white">Initial Analysis</span>
          <span className="ml-1.5 rounded bg-amber-500/10 px-1.5 py-0.5 text-[0.62rem] font-semibold text-amber-400/80">
            Log-based · SSH not yet run
          </span>
        </div>
        <div className="space-y-4 p-4">
          <div className="space-y-1.5">
            <SectionLabel>Summary</SectionLabel>
            <p className="text-[0.84rem] font-medium leading-7 text-slate-100">{ai_summary.summary}</p>
          </div>
          {ai_summary.probable_cause && (
            <>
              <Divider />
              <div className="space-y-1.5">
                <SectionLabel>Probable Cause</SectionLabel>
                <p className="text-[0.81rem] leading-7 text-slate-300">{ai_summary.probable_cause}</p>
              </div>
            </>
          )}
          {ai_summary.impact && (
            <>
              <Divider />
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <SectionLabel>Impact</SectionLabel>
                  <p className="text-[0.8rem] text-slate-400">{ai_summary.impact}</p>
                </div>
                <div className="text-right">
                  <p className="text-[0.63rem] text-slate-600 uppercase tracking-widest">Confidence</p>
                  <p className={`text-[0.9rem] font-bold ${pct >= 70 ? "text-emerald-400" : pct >= 50 ? "text-amber-400" : "text-rose-400"}`}>{pct}%</p>
                </div>
              </div>
            </>
          )}
          <Divider />
          <p className="flex items-center gap-1.5 text-[0.72rem] text-amber-500/70">
            <AlertTriangle className="h-3 w-3 shrink-0" />
            This analysis is based on syslog text only. Run AI Troubleshoot above to get SSH-verified diagnosis with higher confidence.
          </p>
        </div>
      </div>
    );
  }

  const steps = troubleshoot.steps ?? [];

  return (
    <div id="investigation" className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      <div className="border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2">
          <Wrench className="h-3.5 w-3.5 text-slate-600" />
          <span className="text-[0.82rem] font-semibold text-white">AI Investigation</span>
          <span className="ml-1.5 rounded bg-cyan-500/10 px-1.5 py-0.5 text-[0.62rem] font-semibold text-cyan-400">
            {steps.length} CLI command{steps.length !== 1 ? "s" : ""}
          </span>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {/* ai_summary always shown first — log-based context */}
        {ai_summary && (
          <>
            <div className="space-y-1.5">
              <SectionLabel>Incident Summary</SectionLabel>
              <p className="text-[0.84rem] font-medium leading-7 text-slate-100">{ai_summary.summary}</p>
              {ai_summary.probable_cause && (
                <p className="mt-1 text-[0.78rem] leading-6 text-slate-400">
                  <span className="font-semibold text-slate-500">Probable cause:</span> {ai_summary.probable_cause}
                </p>
              )}
            </div>
            <Divider />
          </>
        )}

        {/* SSH investigation result */}
        <div className="space-y-1.5">
          <SectionLabel>SSH Investigation</SectionLabel>
          <p className="text-[0.82rem] leading-7 text-slate-400">{troubleshoot.summary}</p>
        </div>

        {troubleshoot.conclusion && (
          <>
            <Divider />
            <div className="space-y-1.5">
              <SectionLabel>Conclusion</SectionLabel>
              <p className="text-[0.81rem] leading-7 text-slate-300">{troubleshoot.conclusion}</p>
            </div>
          </>
        )}

        {/* CLI steps */}
        {steps.length > 0 && (
          <>
            <Divider />
            <div>
              <SectionLabel>CLI Evidence ({steps.length} steps)</SectionLabel>
              <div className="space-y-1.5">
                {steps.map((s, i) => <CliStep key={i} step={s} index={i} />)}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ─────────────────── Syslog evidence ─────────────────── */

function SyslogSection({ data }: { data: AIOpsIncidentDetailPayload }) {
  const [showAll, setShowAll] = useState(false);
  const logs = data.raw_logs;
  if (!logs.length) return null;
  const visible = showAll ? logs : logs.slice(0, 3);

  return (
    <div id="evidence" className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2">
          <Terminal className="h-3.5 w-3.5 text-slate-600" />
          <span className="text-[0.82rem] font-semibold text-white">Syslog Evidence</span>
          <span className="rounded bg-white/[0.05] px-1.5 py-0.5 text-[0.62rem] font-semibold text-slate-500">{logs.length}</span>
        </div>
        <Link href={`/aiops/logs?incident=${data.incident.incident_no}`}
          className="text-[0.7rem] text-slate-600 hover:text-cyan-400">
          View all →
        </Link>
      </div>

      <div className="divide-y divide-white/[0.05]">
        {visible.map(log => (
          <div key={log.id} className="px-4 py-3">
            <div className="mb-1.5 flex items-center gap-2 text-[0.63rem] text-slate-700">
              <span className="font-mono">{log.source_ip}</span>
              <span>·</span>
              <span>{fmtTime(log.event_time)}</span>
              <span className="ml-auto rounded border border-white/[0.06] px-1 py-0.5">{log.parse_status}</span>
            </div>
            <pre className="whitespace-pre-wrap font-mono text-[0.76rem] leading-6 text-slate-300">{log.raw_message}</pre>
          </div>
        ))}
      </div>

      {logs.length > 3 && (
        <button onClick={() => setShowAll(v => !v)}
          className="flex w-full items-center justify-center gap-1 border-t border-white/[0.05] py-2.5 text-[0.72rem] text-slate-600 hover:text-slate-400">
          {showAll ? <><ChevronUp className="h-3.5 w-3.5" />Show less</> : <><ChevronDown className="h-3.5 w-3.5" />{logs.length - 3} more logs</>}
        </button>
      )}
    </div>
  );
}

/* ─────────────────── Timeline ─────────────────── */

const TL_ICONS: Record<string, React.ElementType> = {
  event: Activity, decision: FileSearch, summary: FileSearch,
  troubleshoot: Wrench, proposal: ShieldAlert, approval: CheckCircle2,
  execution: Play, recovery: CheckCircle2,
};

function TimelineSection({
  entries,
  incidentNo,
  onNoteAdded,
}: {
  entries: AIOpsTimelineEntry[];
  incidentNo: string;
  onNoteAdded: (updated: AIOpsIncidentDetailPayload) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);

  const handleSubmitNote = async () => {
    if (!note.trim() || submitting) return;
    setSubmitting(true);
    setNoteError(null);
    try {
      const updated = await addIncidentNote(incidentNo, note.trim());
      setNote("");
      onNoteAdded(updated);
    } catch {
      setNoteError("Failed to save note — please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const visible = expanded ? entries : entries.slice(0, 5);

  return (
    <div id="timeline" className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2">
          <Clock className="h-3.5 w-3.5 text-slate-600" />
          <span className="text-[0.82rem] font-semibold text-white">Incident Timeline</span>
          <span className="rounded bg-white/[0.05] px-1.5 py-0.5 text-[0.62rem] font-semibold text-slate-500">{entries.length}</span>
        </div>
      </div>

      {/* Note composer */}
      <div className="border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-start gap-2.5">
          <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.05] ring-1 ring-white/[0.08]">
            <PenLine className="h-3 w-3 text-slate-500" />
          </div>
          <div className="flex-1 space-y-1.5">
            <textarea
              value={note}
              onChange={e => setNote(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleSubmitNote(); }}
              placeholder="Add a note… (Ctrl+Enter to save)"
              rows={2}
              disabled={submitting}
              className="w-full resize-none rounded border border-white/[0.07] bg-white/[0.03] px-3 py-2 text-[0.78rem] text-slate-200 placeholder:text-slate-600 focus:border-cyan-500/30 focus:outline-none focus:ring-1 focus:ring-cyan-500/15 disabled:opacity-50"
            />
            <div className="flex items-center justify-between">
              {noteError
                ? <span className="text-[0.7rem] text-rose-400">{noteError}</span>
                : <span className="text-[0.68rem] text-slate-700">Saved to timeline · visible on handover</span>
              }
              <button
                onClick={handleSubmitNote}
                disabled={!note.trim() || submitting}
                className="inline-flex items-center gap-1.5 rounded border border-cyan-500/20 bg-cyan-500/[0.08] px-2.5 py-1 text-[0.72rem] text-cyan-300 transition hover:bg-cyan-500/15 disabled:cursor-not-allowed disabled:opacity-30"
              >
                {submitting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
                Save note
              </button>
            </div>
          </div>
        </div>
      </div>

      {entries.length > 0 && (
        <>
          <div className="relative">
            <div className="absolute bottom-0 left-[1.85rem] top-4 w-px bg-white/[0.05]" />
            <div className="divide-y divide-white/[0.04]">
              {visible.map((e) => {
                const Icon = e.kind === "engineer_note" ? PenLine : (TL_ICONS[e.kind] ?? Activity);
                const isNote = e.kind === "engineer_note";
                return (
                  <div key={e.id} className={`flex gap-3 px-4 py-3 ${isNote ? "bg-amber-500/[0.03]" : ""}`}>
                    <div className={`relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ring-1 ${isNote ? "bg-amber-500/10 ring-amber-500/20" : "bg-[#0c1220] ring-white/[0.07]"}`}>
                      <Icon className={`h-3 w-3 ${isNote ? "text-amber-400/70" : "text-slate-600"}`} />
                    </div>
                    <div className="min-w-0 flex-1 pt-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        {isNote
                          ? <span className="rounded border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wide text-amber-400">Note</span>
                          : <StatusBadge value={e.kind} />
                        }
                        <span className="text-[0.81rem] font-semibold text-slate-200">{e.title}</span>
                        <span className="ml-auto shrink-0 text-[0.65rem] text-slate-600">{fmtTime(e.created_at)}</span>
                      </div>
                      {e.body && <p className={`mt-0.5 text-[0.76rem] leading-5 ${isNote ? "text-slate-300" : "text-slate-500"}`}>{e.body}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {entries.length > 5 && (
            <button onClick={() => setExpanded(v => !v)}
              className="flex w-full items-center justify-center gap-1 border-t border-white/[0.05] py-2.5 text-[0.72rem] text-slate-600 hover:text-slate-400">
              {expanded ? <><ChevronUp className="h-3.5 w-3.5" />Show less</> : <><ChevronDown className="h-3.5 w-3.5" />{entries.length - 5} earlier events</>}
            </button>
          )}
        </>
      )}
    </div>
  );
}

/* ─────────────────── Sidebar ─────────────────── */

function Sidebar({ data }: { data: AIOpsIncidentDetailPayload }) {
  const { incident, troubleshoot } = data;
  const parts = incident.correlation_key?.split("|") ?? [];
  const iface    = parts[2] ?? null;
  const neighbor = parts[3] ?? null;
  const elapsed  = dur(incident.opened_at, incident.resolved_at ?? undefined);

  const rows: [string, React.ReactNode][] = [
    ["Status",    <StatusBadge key="st" value={incident.status} showDot />],
    ["Severity",  <StatusBadge key="sv" value={incident.severity} showDot />],
    ["Device",    incident.primary_hostname ?? incident.primary_source_ip],
    ["Protocol",  incident.event_family?.toUpperCase()],
    ...(iface    ? [["Interface",  iface]    as [string, React.ReactNode]] : []),
    ...(neighbor ? [["Neighbor",   neighbor] as [string, React.ReactNode]] : []),
    ["Site",      incident.site || "—"],
    ["Duration",  elapsed],
    ["Events",    String(incident.event_count)],
    ["Opened",    fmtTime(incident.opened_at)],
    ["Last seen", fmtTime(incident.last_seen_at)],
    ...(incident.resolved_at ? [["Resolved", fmtTime(incident.resolved_at)] as [string, React.ReactNode]] : []),
  ];

  const sections = [
    ...(troubleshoot ? [{ id: "investigation", label: "Investigation" }] : []),
    ...(data.proposal    ? [{ id: "remediation",   label: "Remediation Plan" }] : []),
    { id: "evidence",    label: "Syslog Evidence" },
    { id: "timeline",    label: "Timeline" },
  ];

  return (
    <div className="space-y-3">
      {/* Incident details */}
      <div className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
        <div className="border-b border-white/[0.07] px-4 py-2.5">
          <span className="text-[0.67rem] font-bold uppercase tracking-widest text-slate-600">Incident Details</span>
        </div>
        <table className="w-full">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={String(k)} className="border-b border-white/[0.04] last:border-0">
                <td className="px-4 py-[7px] text-[0.72rem] text-slate-600 whitespace-nowrap">{k}</td>
                <td className="px-4 py-[7px] text-right text-[0.74rem] font-medium text-slate-200">
                  {typeof v === "string" ? v : v}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Section navigation */}
      <div className="overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
        <div className="border-b border-white/[0.07] px-4 py-2.5">
          <span className="text-[0.67rem] font-bold uppercase tracking-widest text-slate-600">On this page</span>
        </div>
        <div className="divide-y divide-white/[0.04]">
          {sections.map(({ id, label }) => (
            <a key={id} href={`#${id}`}
              className="flex items-center justify-between px-4 py-2.5 text-[0.78rem] text-slate-500 transition hover:bg-white/[0.03] hover:text-slate-200">
              {label}
              <ChevronRight className="h-3 w-3 text-slate-700" />
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────── Root component ─────────────────── */

const SEV_BORDER: Record<string, string> = {
  critical: "border-l-rose-500",
  warning:  "border-l-amber-500",
  info:     "border-l-sky-500/60",
};

type Tab = "overview" | "chat";

export function IncidentDetailClient({ initialData }: { initialData: AIOpsIncidentDetailPayload }) {
  const [data, setData]             = useState(initialData);
  const [pending, startTransition]  = useTransition();
  const [error, setError]           = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [activeTab, setActiveTab]   = useState<Tab>("overview");

  const incident = data.incident;

  const refresh = useCallback(async () => {
    try { setData(await fetchIncidentDetail(incident.incident_no)); } catch (_) { /* ignore — background poll, error already visible via action errors */ }
  }, [incident.incident_no]);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const withAction = useCallback((name: string, fn: () => Promise<AIOpsIncidentDetailPayload>) => {
    setError(null);
    setActionLoading(name);
    startTransition(async () => {
      try { setData(await fn()); }
      catch (e) { setError(e instanceof Error ? e.message : "Action failed"); }
      finally { setActionLoading(null); }
    });
  }, []);

  const borderCls = SEV_BORDER[incident.severity] ?? "border-l-white/10";

  return (
    <div className="space-y-4">

      {/* Breadcrumb */}
      <nav className="flex items-center gap-1 text-[0.72rem] text-slate-600">
        <Link href="/aiops" className="hover:text-slate-300">Dashboard</Link>
        <ChevronRight className="h-3 w-3" />
        <Link href="/aiops/incidents" className="hover:text-slate-300">Incidents</Link>
        <ChevronRight className="h-3 w-3" />
        <span className="text-slate-400">{incident.incident_no}</span>
      </nav>

      {/* ── Incident header ── */}
      <div className={`rounded-lg border-l-2 border border-white/[0.07] bg-[#0c1220] px-5 py-4 ${borderCls}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[0.68rem] font-bold text-slate-600">{incident.incident_no}</span>
              <StatusBadge value={incident.severity} showDot />
              <StatusBadge value={incident.status} />
            </div>
            <h1 className="mt-2 text-[1.08rem] font-bold leading-snug text-white">{incident.title}</h1>
            <p className="mt-1 text-[0.76rem] text-slate-500">
              {incident.primary_hostname ?? incident.primary_source_ip}
              <span className="mx-1.5 text-slate-700">·</span>{incident.event_family}
              <span className="mx-1.5 text-slate-700">·</span>{incident.event_count} event{incident.event_count !== 1 ? "s" : ""}
              <span className="mx-1.5 text-slate-700">·</span>Opened {relativeTime(incident.opened_at)}
            </p>
          </div>
        </div>

        {/* Disposition summary line — hide when incident is already resolved */}
        {data.troubleshoot && !["resolved", "resolved_uncertain", "closed"].includes(incident.status) && (
          <div className="mt-3">
            <DispositionBanner
              disposition={data.troubleshoot.disposition}
            />
          </div>
        )}

        {/* Action bar */}
        {incident.status === "verifying" ? (
          /* Verifying — prominent recovery confirm banner */
          <div className="mt-4 rounded-lg border border-emerald-500/25 bg-emerald-500/[0.05] px-4 py-3">
            <p className="mb-2.5 flex items-center gap-2 text-[0.78rem] font-semibold text-emerald-300">
              <Eye className="h-3.5 w-3.5" />
              Remediation executed — confirm recovery status
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <button disabled={pending}
                onClick={() => withAction("verify", () => submitRecoveryDecision(incident.incident_no, { healed: true, note: "Recovery confirmed by operator." }))}
                className="inline-flex items-center gap-2 rounded border border-emerald-500/40 bg-emerald-500/15 px-4 py-2 text-[0.82rem] font-semibold text-emerald-300 transition hover:bg-emerald-500/25 disabled:opacity-40">
                {actionLoading === "verify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                {actionLoading === "verify" ? "Confirming…" : "Mark Recovered — Close Incident"}
              </button>
              <button disabled={pending}
                onClick={() => withAction("not-healed", () => submitRecoveryDecision(incident.incident_no, { healed: false, note: "Still broken after execution — returned to monitoring." }))}
                className="inline-flex items-center gap-2 rounded border border-rose-500/30 bg-rose-500/[0.07] px-4 py-2 text-[0.82rem] font-semibold text-rose-400 transition hover:bg-rose-500/15 disabled:opacity-40">
                {actionLoading === "not-healed" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <XCircle className="h-3.5 w-3.5" />}
                Mark Still Broken
              </button>
              <button disabled={pending}
                onClick={() => withAction("troubleshoot", () => runTroubleshoot(incident.incident_no))}
                className="inline-flex items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[0.78rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200 disabled:opacity-50">
                {actionLoading === "troubleshoot" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                Re-investigate
              </button>
              {error && <p className="text-[0.78rem] text-rose-400">{error}</p>}
            </div>
          </div>
        ) : (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button disabled={pending}
              onClick={() => withAction("troubleshoot", () => runTroubleshoot(incident.incident_no))}
              className="inline-flex items-center gap-1.5 rounded border border-cyan-500/25 bg-cyan-500/[0.08] px-3 py-1.5 text-[0.78rem] font-medium text-cyan-300 transition hover:bg-cyan-500/15 disabled:opacity-50">
              {actionLoading === "troubleshoot" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
              {actionLoading === "troubleshoot" ? "Investigating…" : data.troubleshoot ? "Re-run Troubleshoot" : "Run AI Troubleshoot"}
            </button>
            <button disabled={pending}
              onClick={() => withAction("verify", () => submitRecoveryDecision(incident.incident_no, { healed: true, note: "Recovery confirmed by operator." }))}
              className="inline-flex items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/[0.08] px-3 py-1.5 text-[0.78rem] font-medium text-emerald-300 transition hover:bg-emerald-500/15 disabled:opacity-50">
              {actionLoading === "verify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              Mark Recovered
            </button>
            {error && <p className="text-[0.78rem] text-rose-400">{error}</p>}
          </div>
        )}
      </div>

      {/* ── Tab bar ── */}
      <div className="flex gap-1 border-b border-white/[0.06]">
        {([
          { id: "overview" as Tab, label: "Overview", icon: Server },
          { id: "chat"     as Tab, label: "Chat",     icon: MessageSquare },
        ] as { id: Tab; label: string; icon: React.ElementType }[]).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-[0.78rem] font-medium transition border-b-2 -mb-px ${
              activeTab === id
                ? "border-cyan-500 text-cyan-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* ── Overview tab ── */}
      {activeTab === "overview" && (
        <div className="grid gap-5 xl:grid-cols-[1fr_300px]">
          {/* Main scroll column */}
          <div className="space-y-4 min-w-0">
            <InvestigationSection data={data} loading={actionLoading === "troubleshoot"} />
            <RemediationPlan data={data} withAction={withAction} actionLoading={actionLoading} />
            <SyslogSection data={data} />
            <TimelineSection
              entries={data.timeline}
              incidentNo={data.incident.incident_no}
              onNoteAdded={setData}
            />
          </div>
          {/* Sticky sidebar */}
          <div className="hidden xl:block">
            <div className="sticky top-16">
              <Sidebar data={data} />
            </div>
          </div>
        </div>
      )}

      {/* ── Chat tab ── */}
      {activeTab === "chat" && (
        <div>
          <IncidentChat data={data} />
        </div>
      )}
    </div>
  );
}
