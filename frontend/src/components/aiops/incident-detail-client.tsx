"use client";

import Link from "next/link";
import { useState, useTransition, useEffect, useCallback } from "react";
import {
  CheckCircle2, ClipboardCheck, Copy, Loader2, PlayCircle, RefreshCw,
  ShieldAlert, Terminal, Wrench, Clock, Server, FileText, ListTree,
} from "lucide-react";
import type { AIOpsIncidentDetailPayload } from "@/lib/aiops-types";
import { approveProposal, executeProposal, fetchIncidentDetail, runTroubleshoot, submitRecoveryDecision } from "@/lib/aiops-api";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";

const POLL_INTERVAL = 15_000;

type Tab = "overview" | "investigation" | "evidence" | "timeline";

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: "overview",      label: "Overview",      icon: Server },
  { id: "investigation", label: "Investigation",  icon: Wrench },
  { id: "evidence",      label: "Evidence",       icon: FileText },
  { id: "timeline",      label: "Timeline",       icon: ListTree },
];

const SEV_BANNER: Record<string, string> = {
  critical: "border-l-rose-500   bg-rose-500/5",
  warning:  "border-l-amber-500  bg-amber-500/5",
  info:     "border-l-sky-500/60 bg-sky-500/5",
};

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function fmt(v: string | null | undefined) {
  return v ? new Date(v).toLocaleString() : "—";
}

function ConfidencePill({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const cls = pct >= 80 ? "text-emerald-400 border-emerald-500/30 bg-emerald-500/8"
            : pct >= 60 ? "text-amber-400  border-amber-500/30  bg-amber-500/8"
                        : "text-rose-400   border-rose-500/30   bg-rose-500/8";
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[0.68rem] font-semibold ${cls}`}>
      {pct}% confidence
    </span>
  );
}

function RiskBadge({ level }: { level: string }) {
  const cls = level === "high"   ? "border-rose-500/30   bg-rose-500/10   text-rose-300"
            : level === "medium" ? "border-amber-500/30  bg-amber-500/10  text-amber-300"
                                 : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  return (
    <span className={`inline-flex items-center rounded border px-2 py-0.5 text-[0.67rem] font-semibold uppercase tracking-wide ${cls}`}>
      Risk: {level}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1 rounded border border-white/8 bg-white/[0.04] px-2 py-0.5 text-[0.67rem] text-slate-500 transition hover:bg-white/[0.08] hover:text-slate-200"
    >
      {copied ? <ClipboardCheck className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function StatRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 text-[0.8rem]">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-200">{value ?? "—"}</span>
    </div>
  );
}

function CodeBlock({ title, content, color = "text-slate-300" }: { title: string; content: string; color?: string }) {
  return (
    <div className="rounded border border-white/8 bg-[#060b14]">
      <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
        <div className="flex items-center gap-2">
          <Terminal className="h-3 w-3 text-slate-600" />
          <span className="text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">{title}</span>
        </div>
        <CopyButton text={content} />
      </div>
      <pre className={`max-h-48 overflow-auto p-3 text-[0.76rem] leading-6 ${color}`}>
        {content}
      </pre>
    </div>
  );
}

export function IncidentDetailClient({ initialData }: { initialData: AIOpsIncidentDetailPayload }) {
  const [data, setData]           = useState(initialData);
  const [tab, setTab]             = useState<Tab>("overview");
  const [pending, startTransition] = useTransition();
  const [error, setError]         = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const incident = data.incident;

  const refresh = useCallback(async () => {
    try { setData(await fetchIncidentDetail(incident.incident_no)); }
    catch { /* ignore */ }
  }, [incident.incident_no]);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const withAction = (name: string, action: () => Promise<AIOpsIncidentDetailPayload>) => {
    setError(null);
    setActionLoading(name);
    startTransition(async () => {
      try { setData(await action()); }
      catch (err) { setError(err instanceof Error ? err.message : "Action failed"); }
      finally { setActionLoading(null); }
    });
  };

  const bannerCls = SEV_BANNER[incident.severity] ?? "border-l-white/10";

  return (
    <div className="space-y-4">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-[0.73rem] text-slate-600">
        <Link href="/aiops" className="hover:text-slate-300">Dashboard</Link>
        <span>/</span>
        <Link href="/aiops/incidents" className="hover:text-slate-300">Incidents</Link>
        <span>/</span>
        <span className="text-slate-400">{incident.incident_no}</span>
      </nav>

      {/* Incident header */}
      <div className={`rounded-lg border-l-2 border border-white/[0.07] px-4 py-4 ${bannerCls}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-[0.66rem] font-semibold uppercase tracking-widest text-slate-600">{incident.incident_no}</span>
              <StatusBadge value={incident.severity} showDot />
              <StatusBadge value={incident.status} />
              {incident.category && <StatusBadge value={incident.category} />}
            </div>
            <h1 className="mt-1.5 text-[1.05rem] font-semibold text-white">{incident.title}</h1>
            <p className="mt-0.5 text-[0.78rem] text-slate-500">
              {incident.primary_hostname ?? incident.primary_source_ip}
              <span className="mx-1.5 text-slate-700">·</span>
              {incident.event_family}
              <span className="mx-1.5 text-slate-700">·</span>
              {incident.event_count} event{incident.event_count !== 1 ? "s" : ""}
              <span className="mx-1.5 text-slate-700">·</span>
              Opened {relativeTime(incident.opened_at)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              className="rounded border border-white/8 bg-white/[0.04] p-1.5 text-slate-500 transition hover:bg-white/[0.08] hover:text-slate-200"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* Action bar */}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            disabled={pending}
            onClick={() => withAction("troubleshoot", () => runTroubleshoot(incident.incident_no))}
            className="inline-flex items-center gap-1.5 rounded border border-cyan-500/25 bg-cyan-500/10 px-3 py-1.5 text-[0.78rem] font-medium text-cyan-300 transition hover:bg-cyan-500/15 disabled:opacity-50"
          >
            {actionLoading === "troubleshoot" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
            Run AI Troubleshoot
          </button>
          <button
            disabled={pending}
            onClick={() => withAction("verify", () => submitRecoveryDecision(incident.incident_no, { healed: true, note: "Recovery confirmed by operator." }))}
            className="inline-flex items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/10 px-3 py-1.5 text-[0.78rem] font-medium text-emerald-300 transition hover:bg-emerald-500/15 disabled:opacity-50"
          >
            {actionLoading === "verify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            Mark Recovered
          </button>
          {data.proposal && (
            <>
              <button
                disabled={pending}
                onClick={() => withAction("approve", () => approveProposal(incident.incident_no, "lab-operator"))}
                className="inline-flex items-center gap-1.5 rounded border border-fuchsia-500/25 bg-fuchsia-500/10 px-3 py-1.5 text-[0.78rem] font-medium text-fuchsia-300 transition hover:bg-fuchsia-500/15 disabled:opacity-50"
              >
                {actionLoading === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldAlert className="h-3.5 w-3.5" />}
                Approve Proposal
              </button>
              <button
                disabled={pending}
                onClick={() => withAction("execute", () => executeProposal(incident.incident_no, "lab-operator"))}
                className="inline-flex items-center gap-1.5 rounded border border-orange-500/25 bg-orange-500/10 px-3 py-1.5 text-[0.78rem] font-medium text-orange-300 transition hover:bg-orange-500/15 disabled:opacity-50"
              >
                {actionLoading === "execute" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                Execute
              </button>
            </>
          )}
        </div>
        {error && <p className="mt-2 text-[0.78rem] text-rose-400">{error}</p>}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0 border-b border-white/[0.07]">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-[0.8rem] font-medium transition ${
              tab === id
                ? "border-cyan-400 text-cyan-300"
                : "border-transparent text-slate-500 hover:border-white/20 hover:text-slate-300"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* ── Tab: Overview ─────────────────────────────────────────────────── */}
      {tab === "overview" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
          <div className="space-y-4">
            {/* AI Summary */}
            <SectionCard title="AI Assessment" eyebrow="Triage">
              {data.ai_summary ? (
                <div className="space-y-3">
                  <p className="text-[0.88rem] leading-7 text-slate-200">{data.ai_summary.summary}</p>
                  <div className="flex flex-wrap items-center gap-2">
                    <ConfidencePill score={data.ai_summary.confidence_score} />
                    <StatusBadge value={data.ai_summary.category} />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <p className="mb-1.5 text-[0.67rem] font-semibold uppercase tracking-widest text-slate-600">Probable Cause</p>
                      <p className="text-[0.82rem] leading-6 text-slate-300">{data.ai_summary.probable_cause}</p>
                    </div>
                    <div>
                      <p className="mb-1.5 text-[0.67rem] font-semibold uppercase tracking-widest text-slate-600">Impact</p>
                      <p className="text-[0.82rem] leading-6 text-slate-300">{data.ai_summary.impact}</p>
                    </div>
                  </div>
                  {data.ai_summary.suggested_checks.length > 0 && (
                    <div>
                      <p className="mb-2 text-[0.67rem] font-semibold uppercase tracking-widest text-slate-600">Suggested Checks</p>
                      <ol className="space-y-1.5">
                        {data.ai_summary.suggested_checks.map((item, i) => (
                          <li key={i} className="flex gap-2 text-[0.8rem] text-slate-300">
                            <span className="mt-0.5 shrink-0 font-mono text-[0.65rem] text-slate-600">{i + 1}.</span>
                            <span className="leading-6">{item}</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-[0.82rem] text-slate-600">No AI summary yet. Run troubleshoot to generate an assessment.</p>
              )}
            </SectionCard>

            {/* Latest evidence */}
            {data.raw_logs[0] && (
              <SectionCard title="Latest Signal" eyebrow="Evidence">
                <div className="rounded border border-white/8 bg-[#060b14] p-3">
                  <div className="mb-2 flex items-center gap-2 text-[0.66rem] text-slate-600">
                    <Clock className="h-3 w-3" />
                    <span>{fmt(data.raw_logs[0].received_at)}</span>
                    <span className="mx-1">·</span>
                    <span className="font-mono">{data.raw_logs[0].source_ip}</span>
                  </div>
                  <pre className="whitespace-pre-wrap text-[0.8rem] leading-6 text-slate-300">{data.raw_logs[0].raw_message}</pre>
                </div>
              </SectionCard>
            )}
          </div>

          {/* Right column: lifecycle */}
          <div className="space-y-4">
            <SectionCard title="Lifecycle" eyebrow="Status">
              <div className="divide-y divide-white/[0.06]">
                <StatRow label="Status" value={<StatusBadge value={incident.status} showDot />} />
                <StatRow label="Severity" value={<StatusBadge value={incident.severity} showDot />} />
                <StatRow label="Recovery" value={incident.current_recovery_state} />
                <StatRow label="Opened" value={fmt(incident.opened_at)} />
                <StatRow label="Last seen" value={fmt(incident.last_seen_at)} />
                {incident.resolved_at && <StatRow label="Resolved" value={fmt(incident.resolved_at)} />}
                <StatRow label="Resolution" value={incident.resolution_type ?? "Pending"} />
                <StatRow label="Events" value={incident.event_count} />
                <StatRow label="Reopened" value={incident.reopened_count} />
              </div>
            </SectionCard>

            {data.proposal && (
              <SectionCard title="Proposal Status" eyebrow="Change">
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <StatusBadge value={data.proposal.status} />
                    <RiskBadge level={data.proposal.risk_level} />
                  </div>
                  <p className="text-[0.82rem] font-semibold text-slate-200">{data.proposal.title}</p>
                  {data.proposal.target_devices?.length ? (
                    <p className="text-[0.73rem] text-slate-500">Target: {data.proposal.target_devices.join(", ")}</p>
                  ) : null}
                </div>
              </SectionCard>
            )}
          </div>
        </div>
      )}

      {/* ── Tab: Investigation ────────────────────────────────────────────── */}
      {tab === "investigation" && (
        <div className="space-y-4">
          {data.troubleshoot ? (
            <SectionCard title="Troubleshoot Result" eyebrow="AI Investigation">
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <StatusBadge value={data.troubleshoot.disposition} />
                </div>
                <p className="text-[0.87rem] font-medium leading-7 text-slate-100">{data.troubleshoot.summary}</p>
                <p className="text-[0.82rem] leading-7 text-slate-400">{data.troubleshoot.conclusion}</p>

                {data.troubleshoot.steps?.length > 0 && (
                  <div className="mt-2 space-y-2">
                    <p className="text-[0.67rem] font-semibold uppercase tracking-widest text-slate-600">CLI Investigation Steps</p>
                    {data.troubleshoot.steps.map((step, i) => (
                      <div key={i} className="rounded border border-white/8 bg-[#060b14]">
                        <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
                          <div className="flex items-center gap-2">
                            <span className="text-[0.65rem] font-mono text-slate-600">step {i + 1}</span>
                            <span className="text-[0.78rem] font-semibold text-cyan-400">{step.tool_name}</span>
                          </div>
                          <CopyButton text={step.content} />
                        </div>
                        <pre className="max-h-40 overflow-auto p-3 text-[0.74rem] leading-6 text-slate-300">
                          {step.content}
                        </pre>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </SectionCard>
          ) : (
            <SectionCard title="No Investigation Yet" eyebrow="AI Investigation">
              <p className="text-[0.82rem] text-slate-600">Run AI Troubleshoot from the header to start an investigation.</p>
            </SectionCard>
          )}

          {data.proposal ? (
            <SectionCard
              title="Remediation Proposal"
              eyebrow="Change Plan"
              actions={
                <div className="flex items-center gap-2">
                  <RiskBadge level={data.proposal.risk_level} />
                  <StatusBadge value={data.proposal.status} />
                </div>
              }
            >
              <div className="space-y-3">
                <p className="text-[0.86rem] font-semibold text-slate-100">{data.proposal.title}</p>
                <p className="text-[0.8rem] leading-6 text-slate-400">{data.proposal.rationale}</p>
                {data.proposal.target_devices?.length ? (
                  <p className="text-[0.73rem] text-slate-500">Target devices: {data.proposal.target_devices.join(", ")}</p>
                ) : null}
                <CodeBlock title="Commands" content={data.proposal.commands.join("\n")} color="text-cyan-200" />
                {data.proposal.verification_commands?.length > 0 && (
                  <CodeBlock title="Verification" content={data.proposal.verification_commands.join("\n")} color="text-emerald-200" />
                )}
                {data.proposal.rollback_plan && (
                  <CodeBlock title="Rollback" content={data.proposal.rollback_plan} color="text-amber-200" />
                )}
              </div>
            </SectionCard>
          ) : null}

          {data.execution ? (
            <SectionCard
              title="Execution Record"
              eyebrow="Last Run"
              actions={<StatusBadge value={data.execution.status} />}
            >
              <div className="space-y-3">
                <p className="text-[0.78rem] text-slate-500">Executed by <strong className="text-slate-300">{data.execution.executed_by}</strong></p>
                <CodeBlock title="Output" content={data.execution.output} />
                {data.execution.verification_notes && (
                  <p className="text-[0.78rem] text-slate-400">{data.execution.verification_notes}</p>
                )}
              </div>
            </SectionCard>
          ) : null}
        </div>
      )}

      {/* ── Tab: Evidence ─────────────────────────────────────────────────── */}
      {tab === "evidence" && (
        <div className="grid gap-4 xl:grid-cols-2">
          <SectionCard title="Raw Logs" eyebrow={`${data.raw_logs.length} entries`} noPadding>
            {data.raw_logs.length ? (
              <div className="divide-y divide-white/[0.05]">
                {data.raw_logs.map((log) => (
                  <div key={log.id} className="px-4 py-3">
                    <div className="mb-1 flex items-center gap-2 text-[0.64rem] text-slate-600">
                      <span className="font-mono">{log.source_ip}</span>
                      <span>·</span>
                      <span>{fmt(log.received_at)}</span>
                    </div>
                    <pre className="whitespace-pre-wrap text-[0.78rem] leading-6 text-slate-300">{log.raw_message}</pre>
                  </div>
                ))}
              </div>
            ) : (
              <p className="px-4 py-5 text-[0.8rem] text-slate-600">No raw logs attached.</p>
            )}
          </SectionCard>

          <SectionCard title="Normalized Events" eyebrow={`${data.events.length} events`} noPadding>
            {data.events.length ? (
              <div className="divide-y divide-white/[0.05]">
                {data.events.map((ev) => (
                  <div key={ev.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <StatusBadge value={ev.event_state} />
                        <StatusBadge value={ev.severity} showDot />
                      </div>
                      <span className="text-[0.64rem] text-slate-600">{relativeTime(ev.created_at)}</span>
                    </div>
                    <p className="mt-1 text-[0.82rem] font-semibold text-slate-200">{ev.title}</p>
                    <p className="text-[0.73rem] text-slate-500">{ev.event_family} · {ev.correlation_key}</p>
                    {ev.raw_message && (
                      <pre className="mt-1 truncate text-[0.72rem] text-slate-600">{ev.raw_message}</pre>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="px-4 py-5 text-[0.8rem] text-slate-600">No events attached.</p>
            )}
          </SectionCard>
        </div>
      )}

      {/* ── Tab: Timeline ─────────────────────────────────────────────────── */}
      {tab === "timeline" && (
        <SectionCard title="Incident Timeline" eyebrow="Audit Trail" noPadding>
          {data.timeline.length ? (
            <div className="relative pl-4">
              <div className="absolute bottom-0 left-[1.65rem] top-4 w-px bg-white/[0.06]" />
              <div className="space-y-0 divide-y divide-white/[0.05]">
                {data.timeline.map((entry) => (
                  <div key={entry.id} className="flex gap-3 py-3.5 pl-4 pr-4">
                    <div className="relative z-10 mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full bg-white/10 ring-2 ring-[#0c1220]" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge value={entry.kind} />
                        <p className="text-[0.82rem] font-semibold text-slate-200">{entry.title}</p>
                        <span className="ml-auto text-[0.66rem] text-slate-600">{fmt(entry.created_at)}</span>
                      </div>
                      <p className="mt-1 text-[0.78rem] leading-6 text-slate-400">{entry.body}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="px-4 py-5 text-[0.8rem] text-slate-600">No timeline entries yet.</p>
          )}
        </SectionCard>
      )}

      {/* Full logs link */}
      <div className="flex justify-end">
        <Link
          href={`/aiops/logs?incident=${incident.incident_no}`}
          className="text-[0.73rem] text-slate-600 transition hover:text-cyan-400"
        >
          View all raw logs for {incident.incident_no} →
        </Link>
      </div>
    </div>
  );
}
