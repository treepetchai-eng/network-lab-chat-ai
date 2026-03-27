"use client";

import Link from "next/link";
import { useEffect, useState, useCallback } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ClipboardList,
  RefreshCw,
  RotateCcw,
  TrendingUp,
} from "lucide-react";
import { MetricCard } from "@/components/aiops/metric-card";
import { ProposalCard } from "@/components/aiops/approvals-client";
import { ResetIncidentsButton } from "@/components/aiops/reset-incidents-button";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchDashboard, fetchLogs } from "@/lib/aiops-api";
import type { AIOpsDashboardPayload, AIOpsLogsPayload, AIOpsProposal } from "@/lib/aiops-types";

const POLL_INTERVAL = 30_000;

function relativeTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const SEVERITY_BORDER: Record<string, string> = {
  critical: "border-l-rose-500",
  warning:  "border-l-amber-500",
  info:     "border-l-sky-500/60",
};

function showWorkflowBadge(phase: string | null | undefined) {
  return [
    "ai_investigating",
    "intent_confirmation_required",
    "remediation_available",
    "escalated_physical",
    "escalated_external",
  ].includes(phase ?? "none");
}

interface Props {
  initialDashboard: AIOpsDashboardPayload;
  initialLogs: AIOpsLogsPayload;
}

export function DashboardClient({ initialDashboard, initialLogs }: Props) {
  const [dashboard, setDashboard] = useState(initialDashboard);
  const [approvals, setApprovals] = useState(initialDashboard.approvals);
  const [logs, setLogs]           = useState(initialLogs);
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);
  useEffect(() => { setLastRefresh(Date.now()); }, []);
  const [refreshing, setRefreshing]   = useState(false);
  const [fetchError, setFetchError]   = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [d, l] = await Promise.all([fetchDashboard(), fetchLogs()]);
      setDashboard(d);
      setApprovals(d.approvals);
      setLogs(l);
      setLastRefresh(Date.now());
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const m = dashboard.metrics;

  return (
    <div className="space-y-5">
      {fetchError && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/[0.06] px-4 py-2.5">
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
          <p className="text-[0.76rem] text-rose-300">Refresh failed: {fetchError} — showing cached data</p>
        </div>
      )}
      {/* Incident Lifecycle Banner */}
      <div className="overflow-hidden rounded-xl border border-white/8 bg-white/[0.02] px-5 py-3.5">
        <p className="mb-2.5 text-[0.65rem] font-semibold uppercase tracking-widest text-slate-600">Incident Lifecycle</p>
        <div className="flex flex-wrap items-center gap-1.5 text-[0.7rem]">
          {([
            { label: "Active",         color: "text-rose-300",     bg: "bg-rose-500/10 border-rose-500/20" },
            { label: "Recovering",     color: "text-amber-300",    bg: "bg-amber-500/10 border-amber-500/20" },
            { label: "Monitoring",     color: "text-yellow-300",   bg: "bg-yellow-500/10 border-yellow-500/20" },
            { label: "Resolved →",     color: "text-emerald-300",  bg: "bg-emerald-500/10 border-emerald-500/20" },
            { label: "History",        color: "text-slate-400",    bg: "bg-slate-500/10 border-slate-500/20" },
          ] as const).map(({ label, color, bg }, i, arr) => (
            <span key={label} className="flex items-center gap-1.5">
              <span className={`inline-flex items-center rounded border px-2 py-0.5 font-semibold uppercase tracking-wide ${color} ${bg}`}>
                {label}
              </span>
              {i < arr.length - 2 && <span className="text-slate-700">→</span>}
              {i === arr.length - 2 && null}
            </span>
          ))}
          <span className="ml-2 text-slate-600">· Related incidents and remediation hints appear as secondary context, not as the primary health status</span>
        </div>
      </div>

      {/* Metrics row */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricCard
          label="Active Incidents"
          value={m.active_incidents}
          icon={Activity}
          variant={m.active_incidents > 0 ? "critical" : "default"}
          sub="Open threads"
        />
        <MetricCard
          label="Recovery Watch"
          value={m.recovering_incidents}
          icon={TrendingUp}
          variant={m.recovering_incidents > 0 ? "warning" : "default"}
          sub="Recovering + monitoring"
        />
        <MetricCard
          label="Pending Approval"
          value={m.pending_approvals}
          icon={ClipboardList}
          variant={m.pending_approvals > 0 ? "info" : "default"}
          sub="Awaiting human gate"
        />
        <MetricCard
          label="Resolved Today"
          value={m.resolved_today}
          icon={CheckCircle2}
          variant={m.resolved_today > 0 ? "success" : "default"}
          sub="Last 24 h"
        />
        <MetricCard
          label="Reopened"
          value={m.reopened_this_week}
          icon={RotateCcw}
          variant={m.reopened_this_week > 0 ? "warning" : "default"}
          sub="This week"
        />
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3">
        <p className="text-[0.73rem] text-slate-600">
          Auto-refresh every 30 s{lastRefresh ? ` · Last updated ${new Date(lastRefresh).toLocaleTimeString()}` : ""}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-[0.73rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200 disabled:opacity-40"
          >
            <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <ResetIncidentsButton />
        </div>
      </div>

      {/* Main content grid */}
      <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">

        {/* Live incident queue */}
        <SectionCard
          title="Live Incident Queue"
          eyebrow="Active"
          noPadding
        >
          {dashboard.incidents.length ? (
            <div className="divide-y divide-white/[0.05]">
              {dashboard.incidents.map((inc) => (
                <Link
                  key={inc.incident_no}
                  href={`/aiops/incidents/${inc.incident_no}`}
                  className={`flex items-start gap-3.5 border-l-2 px-4 py-3.5 transition hover:bg-white/[0.04] ${SEVERITY_BORDER[inc.severity] ?? "border-l-white/10"}`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[0.66rem] font-semibold uppercase tracking-widest text-slate-500">{inc.incident_no}</span>
                      <StatusBadge value={inc.severity} showDot />
                    </div>
                    <p className="mt-1 truncate text-[0.86rem] font-semibold text-slate-100">{inc.title}</p>
                    <p className="mt-0.5 text-[0.76rem] text-slate-500">
                      {inc.primary_hostname ?? inc.primary_source_ip}
                    </p>
                    {(inc.child_count ?? 0) > 0 ? (
                      <p className="mt-1 text-[0.68rem] text-slate-600">
                        {(inc.active_child_count ?? 0)} open / {inc.child_count} related incident{(inc.child_count ?? 0) !== 1 ? "s" : ""}
                      </p>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="flex flex-col items-end gap-1">
                      <StatusBadge value={inc.status} />
                      {showWorkflowBadge(inc.workflow_phase) ? <StatusBadge value={inc.workflow_phase} /> : null}
                    </div>
                    <p className="mt-1 text-[0.68rem] text-slate-600">{relativeTime(inc.last_seen_at)}</p>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center px-4 py-10 text-center">
              <CheckCircle2 className="h-8 w-8 text-emerald-500/40" />
              <p className="mt-3 text-[0.86rem] font-medium text-slate-400">All clear</p>
              <p className="mt-1 text-[0.78rem] text-slate-600">No active incidents. Monitoring syslog.</p>
            </div>
          )}
        </SectionCard>

        <div className="space-y-5">
          {/* Approval queue */}
          <SectionCard title="Approval Queue" eyebrow="Change Control" noPadding>
            {approvals.length ? (
              <div className="divide-y divide-white/[0.05]">
                {approvals.map((p) => (
                  <ProposalCard
                    key={p.id}
                    proposal={p}
                    onDone={(updated: AIOpsProposal) =>
                      setApprovals((prev) => prev.map((x) => (x.id === updated.id ? updated : x)))
                    }
                  />
                ))}
              </div>
            ) : (
              <p className="px-4 py-5 text-[0.8rem] text-slate-600">No pending proposals.</p>
            )}
          </SectionCard>

          {/* Resolved recently */}
          <SectionCard title="Resolved Recently" eyebrow="History" noPadding>
            {dashboard.history.length ? (
              <div className="divide-y divide-white/[0.05]">
                {dashboard.history.map((inc) => (
                  <Link
                    key={inc.incident_no}
                    href={`/aiops/incidents/${inc.incident_no}`}
                    className="flex items-center justify-between gap-3 px-4 py-3 transition hover:bg-white/[0.04]"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-[0.82rem] font-semibold text-slate-100">{inc.title}</p>
                      <p className="text-[0.72rem] text-slate-500">{inc.incident_no}</p>
                    </div>
                    <div className="shrink-0 text-right">
                      <StatusBadge value={inc.status} />
                      {inc.resolved_at && (
                        <p className="mt-1 text-[0.68rem] text-slate-600">{relativeTime(inc.resolved_at)}</p>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="px-4 py-5 text-[0.8rem] text-slate-600">No resolved incidents yet.</p>
            )}
          </SectionCard>

          {/* Log feed shortcut */}
          <SectionCard
            title="Log Feed"
            eyebrow="Syslog"
            actions={
              <Link href="/aiops/logs" className="text-[0.72rem] text-slate-500 transition hover:text-cyan-400">
                Open Logs →
              </Link>
            }
          >
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-[0.78rem] text-slate-400">Raw logs ingested</span>
                <span className="text-[0.82rem] font-semibold text-slate-200">{logs.raw_logs.length}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-[0.78rem] text-slate-400">Normalized events</span>
                <span className="text-[0.82rem] font-semibold text-slate-200">{logs.events.length}</span>
              </div>
              {logs.raw_logs[0] && (
                <p className="mt-2 text-[0.7rem] text-slate-600">
                  Last: {relativeTime(logs.raw_logs[0].received_at)} · {logs.raw_logs[0].source_ip}
                </p>
              )}
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
