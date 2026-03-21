"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity, AlertCircle, AlertTriangle, BarChart2, CheckCircle2, ClipboardCheck,
  Network, RefreshCcw, ShieldAlert, Terminal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { CompactTime } from "@/components/ops/compact-time";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { PageHeader } from "@/components/ops/page-header";
import { OpsKpiCard } from "@/components/ops/ops-kpi-card";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonKpiCard, SkeletonSection } from "@/components/ops/ops-skeleton";
import { StatusBadge } from "@/components/ops/status-badge";
import { Button } from "@/components/ui/button";
import { fetchOpsOverview, getErrorMessage } from "@/lib/ops-api";
import {
  OPS_ACTION_LINK_CLASS,
  OPS_ERROR_CLASS,
  OPS_SECTION_CARD_CLASS,
  OPS_SECTION_CARD_HEADER,
  OPS_TEXT_LINK_CLASS,
  PAGE_CONTENT_CLASS,
  SEV_BORDER,
} from "@/lib/ops-ui";
import type { OpsApproval, OpsIncident, OpsOverview } from "@/lib/ops-types";

/* ── Section title ─────────────────────────────────── */
function SectionTitle({
  icon: Icon,
  iconColor,
  title,
  badge,
  badgeColor,
}: {
  icon: React.ElementType;
  iconColor: string;
  title: string;
  badge?: number;
  badgeColor?: string;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <Icon className={cn("size-3.5 shrink-0", iconColor)} />
      <h2 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-300">{title}</h2>
      {badge !== undefined && badge > 0 && (
        <span className={cn(
          "rounded-full border px-1.5 py-0.5 text-[0.65rem] font-medium tabular-nums",
          badgeColor ?? "border-white/15 bg-white/[0.06] text-slate-400",
        )}>
          {badge}
        </span>
      )}
    </div>
  );
}

/* ── System Status Banner ──────────────────────────── */
function SystemStatusBanner({ overview }: { overview: OpsOverview }) {
  const { open_incidents, pending_approvals } = overview.counts;
  const criticalOrHigh = overview.open_incidents.filter(
    (i) => ["critical", "high"].includes(i.severity),
  ).length;

  if (open_incidents === 0 && pending_approvals === 0) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/[0.06] px-4 py-2.5">
        <CheckCircle2 className="size-3.5 shrink-0 text-emerald-400" />
        <p className="text-sm font-medium text-emerald-100">All systems nominal — no open incidents</p>
      </div>
    );
  }

  if (criticalOrHigh > 0) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-rose-500/20 bg-rose-500/[0.06] px-4 py-2.5">
        <AlertTriangle className="size-3.5 shrink-0 text-rose-400" />
        <p className="text-sm font-medium text-rose-100">
          {criticalOrHigh} critical/high incident{criticalOrHigh > 1 ? "s" : ""} require attention
          {pending_approvals > 0 && ` · ${pending_approvals} approval${pending_approvals > 1 ? "s" : ""} pending`}
        </p>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-4 py-2.5">
      <AlertTriangle className="size-3.5 shrink-0 text-amber-400" />
      <p className="text-sm font-medium text-amber-100">
        {open_incidents} open incident{open_incidents > 1 ? "s" : ""} being monitored
        {pending_approvals > 0 && ` · ${pending_approvals} approval${pending_approvals > 1 ? "s" : ""} awaiting review`}
      </p>
    </div>
  );
}

/* ── Incident Row ──────────────────────────────────── */
function IncidentRow({ incident }: { incident: OpsIncident }) {
  return (
    <tr className="transition hover:bg-white/[0.03]">
      <td className={cn("w-[9rem] px-4 py-2.5 border-l-2", SEV_BORDER[incident.severity] ?? "border-l-slate-700/50")}>
        <StatusBadge value={incident.severity} />
      </td>
      <td className="min-w-0 py-2.5 pr-4">
        <div className="flex min-w-0 items-center gap-1.5">
          {incident.requires_attention && (
            <AlertCircle className="size-3 shrink-0 text-amber-400" />
          )}
          <Link
            href={`/ops/incidents/${incident.id}`}
            className={cn(OPS_TEXT_LINK_CLASS, "line-clamp-1 text-sm font-medium")}
          >
            {incident.title}
          </Link>
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-600">
          {incident.hostname ?? incident.primary_source_ip ?? "Unknown"}
        </p>
      </td>
      <td className="w-[9rem] py-2.5 pr-3">
        <StatusBadge value={incident.status} />
      </td>
      <td className="w-[5.5rem] py-2.5 pr-4">
        <CompactTime value={incident.updated_at} />
      </td>
    </tr>
  );
}

/* ── Approval Row ──────────────────────────────────── */
function ApprovalRow({ approval }: { approval: OpsApproval }) {
  return (
    <tr className="transition hover:bg-white/[0.03]">
      <td className="w-[9rem] px-4 py-2.5">
        <StatusBadge value={approval.risk_level} />
      </td>
      <td className="min-w-0 py-2.5 pr-3">
        <p className="truncate text-sm font-medium text-white">{approval.title}</p>
        {approval.target_host && (
          <p className="mt-0.5 text-xs text-slate-600">Target: {approval.target_host}</p>
        )}
      </td>
      <td className="w-[5.5rem] py-2.5 pr-3 text-right">
        <CompactTime value={approval.requested_at} />
      </td>
      <td className="w-[5.5rem] py-2.5 pr-4">
        <Link href="/ops/approvals" className={cn(OPS_ACTION_LINK_CLASS, "px-2.5 py-1.5 text-xs whitespace-nowrap")}>
          Review
        </Link>
      </td>
    </tr>
  );
}

/* ── Report Row ────────────────────────────────────── */
function ReportRow({ approval }: { approval: OpsApproval }) {
  const ts = approval.executed_at ?? approval.decided_at ?? approval.requested_at;
  return (
    <tr className="transition hover:bg-white/[0.03]">
      <td className="w-[9rem] px-4 py-2.5">
        <StatusBadge value={approval.execution_status} />
      </td>
      <td className="min-w-0 py-2.5 pr-3">
        <p className="truncate text-sm font-medium text-white">{approval.title}</p>
        <p className="mt-0.5 text-xs text-slate-600">{approval.failure_category ?? approval.execution_status}</p>
      </td>
      <td className="w-[5.5rem] py-2.5 pr-3 text-right">
        <CompactTime value={ts} />
      </td>
      <td className="w-[5.5rem] py-2.5 pr-4">
        {approval.incident_id ? (
          <Link href={`/ops/incidents/${approval.incident_id}`} className={cn(OPS_TEXT_LINK_CLASS, "whitespace-nowrap text-xs")}>
            Details
          </Link>
        ) : <span />}
      </td>
    </tr>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function OpsDashboardPage() {
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsOverview()
      .then((data) => { if (!cancelled) { setOverview(data); setError(null); } })
      .catch((e) => { if (!cancelled) setError(getErrorMessage(e)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      fetchOpsOverview().then((data) => setOverview(data)).catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  }, []);

  async function handleRefresh() {
    setIsBusy(true);
    try {
      const data = await fetchOpsOverview();
      setOverview(data);
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setIsBusy(false);
    }
  }

  const counts = overview?.counts;
  const openIncidents = overview?.open_incidents ?? [];
  const pendingApprovals = overview?.pending_approvals ?? [];
  const executionReports = overview?.recent_execution_reports ?? [];
  const topEventTypes = overview?.top_event_types ?? [];

  return (
    <div className="min-h-full">
      <PageHeader
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard" }]} />}
        title="Overview"
        actions={(
          <Button variant="outline" size="sm" onClick={() => { void handleRefresh(); }} disabled={isBusy}>
            <RefreshCcw className="size-3.5" />
            {isBusy ? "Refreshing…" : "Refresh"}
          </Button>
        )}
      />

      <div className={PAGE_CONTENT_CLASS}>
        {error ? <div className={OPS_ERROR_CLASS}>{error}</div> : null}

        {/* System Status Banner */}
        {!isLoading && overview && <SystemStatusBanner overview={overview} />}

        {/* KPI row */}
        {isLoading && !overview ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => <SkeletonKpiCard key={i} />)}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <OpsKpiCard
              label="Open Incidents"
              value={counts?.open_incidents ?? 0}
              icon={ShieldAlert}
              href="/ops/incidents"
              accentColor={(counts?.open_incidents ?? 0) > 0 ? "rose" : "slate"}
            />
            <OpsKpiCard
              label="Pending Approvals"
              value={counts?.pending_approvals ?? 0}
              icon={ClipboardCheck}
              href="/ops/approvals"
              accentColor={(counts?.pending_approvals ?? 0) > 0 ? "amber" : "slate"}
            />
            <OpsKpiCard
              label="Devices"
              value={counts?.devices ?? 0}
              icon={Network}
              href="/ops/devices"
              accentColor="cyan"
            />
            <OpsKpiCard
              label="Total Events"
              value={counts?.events ?? 0}
              icon={Activity}
              href="/ops/incidents"
              accentColor="sky"
              subtitle="Normalized syslog events"
            />
          </div>
        )}

        {/* Main content grid */}
        {isLoading && !overview ? (
          <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
            <SkeletonSection lines={5} />
            <SkeletonSection lines={4} />
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[1fr_340px]">

            {/* LEFT — Active Incidents + Execution Reports */}
            <div className="space-y-4">

              {/* Active Incidents */}
              <div className={OPS_SECTION_CARD_CLASS}>
                <div className={OPS_SECTION_CARD_HEADER}>
                  <SectionTitle
                    icon={ShieldAlert}
                    iconColor={openIncidents.length > 0 ? "text-rose-400" : "text-slate-500"}
                    title="Active Incidents"
                    badge={openIncidents.length}
                    badgeColor="border-rose-400/25 bg-rose-400/10 text-rose-200"
                  />
                  <Link href="/ops/incidents" className={OPS_ACTION_LINK_CLASS}>
                    View all
                  </Link>
                </div>
                {openIncidents.length > 0 ? (
                  <table className="w-full table-fixed">
                    <tbody className="divide-y divide-white/[0.05]">
                      {openIncidents.slice(0, 8).map((inc) => (
                        <IncidentRow key={inc.id} incident={inc} />
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <OpsEmptyState
                    icon={CheckCircle2}
                    title="No active incidents"
                    description="All clear — the network is looking good."
                  />
                )}
              </div>

              {/* Execution Reports */}
              {executionReports.length > 0 && (
                <div className={OPS_SECTION_CARD_CLASS}>
                  <div className={OPS_SECTION_CARD_HEADER}>
                    <SectionTitle
                      icon={Terminal}
                      iconColor="text-emerald-400"
                      title="Recent Execution Reports"
                      badge={executionReports.length}
                      badgeColor="border-emerald-400/25 bg-emerald-400/10 text-emerald-200"
                    />
                    <Link href="/ops/approvals" className={OPS_ACTION_LINK_CLASS}>
                      See all
                    </Link>
                  </div>
                  <table className="w-full table-fixed">
                    <tbody className="divide-y divide-white/[0.05]">
                      {executionReports.map((ap) => (
                        <ReportRow key={ap.id} approval={ap} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* RIGHT — Pending Approvals + Top Event Types */}
            <div className="flex flex-col gap-4">

              {/* Pending Approvals */}
              <div className={OPS_SECTION_CARD_CLASS}>
                <div className={OPS_SECTION_CARD_HEADER}>
                  <SectionTitle
                    icon={ClipboardCheck}
                    iconColor={pendingApprovals.length > 0 ? "text-amber-400" : "text-slate-500"}
                    title="Pending Approvals"
                    badge={pendingApprovals.length}
                    badgeColor="border-amber-400/25 bg-amber-400/10 text-amber-200"
                  />
                  <Link href="/ops/approvals" className={OPS_ACTION_LINK_CLASS}>
                    View all
                  </Link>
                </div>
                {pendingApprovals.length > 0 ? (
                  <table className="w-full table-fixed">
                    <tbody className="divide-y divide-white/[0.05]">
                      {pendingApprovals.slice(0, 5).map((ap) => (
                        <ApprovalRow key={ap.id} approval={ap} />
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <OpsEmptyState title="No pending approvals" />
                )}
              </div>

              {/* Top Event Types */}
              {topEventTypes.length > 0 && (
                <div className={OPS_SECTION_CARD_CLASS}>
                  <div className={OPS_SECTION_CARD_HEADER}>
                    <SectionTitle
                      icon={BarChart2}
                      iconColor="text-cyan-400"
                      title="Top Event Types"
                    />
                  </div>
                  <div className="space-y-1.5 px-5 py-4">
                    {topEventTypes.slice(0, 7).map((evt, i) => {
                      const max = topEventTypes[0]?.count ?? 1;
                      const pct = Math.round((evt.count / max) * 100);
                      return (
                        <div key={evt.event_type} className="flex items-center gap-3">
                          <span className="w-4 shrink-0 text-right text-xs tabular-nums text-slate-600">{i + 1}</span>
                          <div className="relative min-w-0 flex-1 overflow-hidden rounded">
                            <div
                              className="absolute inset-y-0 left-0 rounded bg-cyan-400/[0.08]"
                              style={{ width: `${pct}%` }}
                            />
                            <span className="relative block truncate px-2 py-1 text-xs text-slate-300">
                              {evt.event_type}
                            </span>
                          </div>
                          <span className="w-8 shrink-0 text-right text-xs font-medium tabular-nums text-slate-400">
                            {evt.count}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

          </div>
        )}
      </div>
    </div>
  );
}
