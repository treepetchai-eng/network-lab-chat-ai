"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, RefreshCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { CompactTime } from "@/components/ops/compact-time";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonTableRows } from "@/components/ops/ops-skeleton";
import { PageHeader } from "@/components/ops/page-header";
import { PaginationControls } from "@/components/ops/pagination-controls";
import { SortButton } from "@/components/ops/sort-button";
import { StatusBadge } from "@/components/ops/status-badge";
import { Button } from "@/components/ui/button";
import { fetchOpsIncidents, getErrorMessage } from "@/lib/ops-api";
import { OPS_CONTROL_CLASS, OPS_TABLE_WRAPPER_CLASS, OPS_TH_CLASS, OPS_ERROR_CLASS, SEV_BORDER } from "@/lib/ops-ui";
import { formatDuration } from "@/lib/time";
import { mergeSearchParams, getNumberParam, getStringParam } from "@/lib/search-params";
import type { OpsIncident, PaginatedResponse, SortDirection } from "@/lib/ops-types";

function OpsHistoryPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [response, setResponse] = useState<PaginatedResponse<OpsIncident> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const q = getStringParam(searchParams, "q");
  const severity = getStringParam(searchParams, "severity");
  const sortBy = getStringParam(searchParams, "sort_by", "closed_at") as "closed_at" | "opened_at" | "severity";
  const sortDir = getStringParam(searchParams, "sort_dir", "desc") as SortDirection;
  const page = getNumberParam(searchParams, "page", 1);
  const pageSize = getNumberParam(searchParams, "page_size", 25);
  const incidents = response?.items ?? [];
  const severityOptions = response?.facets?.severities ?? [];

  function updateParams(updates: Record<string, string | number | boolean | null | undefined>) {
    const next = mergeSearchParams(new URLSearchParams(searchParams.toString()), updates);
    router.replace(next ? `${pathname}?${next}` : pathname);
  }

  function toggleSort(nextSortBy: typeof sortBy) {
    const nextSortDir: SortDirection = sortBy === nextSortBy && sortDir === "desc" ? "asc" : "desc";
    updateParams({ sort_by: nextSortBy, sort_dir: nextSortDir, page: 1 });
  }

  function buildQuery() {
    return {
      status: "resolved",
      q: q || undefined,
      severity: severity || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      page,
      page_size: pageSize,
    } as const;
  }

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsIncidents(buildQuery())
      .then((r) => { if (!cancelled) { setResponse(r); setError(null); } })
      .catch((e) => { if (!cancelled) setError(getErrorMessage(e)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, severity, sortBy, sortDir, page, pageSize]);

  useEffect(() => {
    const id = setInterval(() => {
      fetchOpsIncidents(buildQuery()).then((r) => setResponse(r)).catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, severity, sortBy, sortDir, page, pageSize]);

  async function handleRefresh() {
    setIsBusy(true);
    try { const r = await fetchOpsIncidents(buildQuery()); setResponse(r); setError(null); }
    catch (e) { setError(getErrorMessage(e)); }
    finally { setIsBusy(false); }
  }

  return (
    <div className="min-h-full">
      <PageHeader
        title="Incident History"
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "History" }]} />}
        actions={(
          <Button variant="outline" onClick={() => { void handleRefresh(); }} disabled={isBusy}>
            <RefreshCcw className="size-4" />
            {isBusy ? "Refreshing..." : "Refresh"}
          </Button>
        )}
      />

      <div className="space-y-4 px-6 py-6 sm:px-8">
        {error ? <div className={OPS_ERROR_CLASS}>{error}</div> : null}

        <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_1fr]">
          <input
            value={q}
            onChange={(e) => updateParams({ q: e.target.value, page: 1 })}
            placeholder="Search title, summary, host..."
            className={OPS_CONTROL_CLASS}
          />
          <select
            value={severity}
            onChange={(e) => updateParams({ severity: e.target.value || null, page: 1 })}
            className={OPS_CONTROL_CLASS}
          >
            <option value="">All severities</option>
            {severityOptions.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>

        <div className={OPS_TABLE_WRAPPER_CLASS}>
          <div className="overflow-x-auto">
            <table className="min-w-[900px] divide-y divide-white/8">
              <colgroup>
                <col className="w-[28%]" />
                <col className="w-[8%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[10%]" />
                <col className="w-[24%]" />
                <col className="w-[10%]" />
              </colgroup>
              <thead className="bg-white/[0.03]">
                <tr>
                  <th className={OPS_TH_CLASS}>Incident</th>
                  <th className={OPS_TH_CLASS}>Severity</th>
                  <th className={OPS_TH_CLASS}>
                    <SortButton label="Opened" active={sortBy === "opened_at"} direction={sortDir} onClick={() => toggleSort("opened_at")} />
                  </th>
                  <th className={OPS_TH_CLASS}>Duration</th>
                  <th className={OPS_TH_CLASS}>Resolved By</th>
                  <th className={OPS_TH_CLASS}>Resolution</th>
                  <th className={OPS_TH_CLASS}>
                    <SortButton label="Closed" active={sortBy === "closed_at"} direction={sortDir} onClick={() => toggleSort("closed_at")} />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/6">
                {isLoading ? (
                  <SkeletonTableRows rows={5} cols={7} />
                ) : (
                  incidents.map((inc) => (
                    <HistoryRow key={inc.id} inc={inc} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {!isLoading && incidents.length === 0 ? (
            <OpsEmptyState icon={CheckCircle2} title="No resolved incidents yet." />
          ) : null}
        </div>

        {response ? (
          <PaginationControls
            page={response.page}
            totalPages={response.total_pages}
            totalItems={response.total}
            pageSize={response.page_size}
            onPageChange={(p) => updateParams({ page: p })}
          />
        ) : null}
      </div>
    </div>
  );
}

function HistoryRow({ inc }: { inc: OpsIncident }) {
  const resolution = inc.resolution_notes || inc.ai_summary;
  const resolvedBy = inc.resolved_by || "System";
  const duration = formatDuration(inc.opened_at, inc.closed_at);

  return (
    <tr className="align-top transition hover:bg-white/[0.04]">
      <td className={cn("px-4 py-3 border-l-2", SEV_BORDER[inc.severity] ?? "border-l-slate-700/50")}>
        <Link
          href={`/ops/incidents/${inc.id}`}
          className="line-clamp-1 font-medium text-white text-sm transition hover:text-cyan-200"
        >
          {inc.title}
        </Link>
        <p className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
          {inc.incident_no && <span className="font-mono text-[0.7rem] text-slate-600">{inc.incident_no}</span>}
          <span>{inc.hostname ?? inc.primary_source_ip ?? "—"}</span>
        </p>
      </td>
      <td className="px-4 py-3"><StatusBadge value={inc.severity} /></td>
      <td className="px-4 py-3"><CompactTime value={inc.opened_at} /></td>
      <td className="px-4 py-3">
        <span className="font-mono text-sm text-slate-300">{duration}</span>
        <p className="mt-0.5 text-xs text-slate-600">{inc.event_count} evt{inc.event_count !== 1 ? "s" : ""}</p>
      </td>
      <td className="px-4 py-3">
        <span className="text-sm text-slate-300">{resolvedBy}</span>
      </td>
      <td className="px-4 py-3">
        {resolution ? (
          <p className="line-clamp-2 text-sm text-slate-400">{resolution.slice(0, 200)}</p>
        ) : (
          <span className="text-xs text-slate-600">—</span>
        )}
      </td>
      <td className="px-4 py-3"><CompactTime value={inc.closed_at} /></td>
    </tr>
  );
}

export default function OpsHistoryPage() {
  return (
    <Suspense fallback={<div className="px-6 py-10 text-sm text-slate-300 sm:px-8">Loading...</div>}>
      <OpsHistoryPageContent />
    </Suspense>
  );
}
