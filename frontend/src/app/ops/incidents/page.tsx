"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { RefreshCcw, Siren } from "lucide-react";
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
import {
  FILTER_BAR_CLASS,
  OPS_CONTROL_CLASS,
  OPS_ERROR_CLASS,
  OPS_SECTION_CARD_CLASS,
  OPS_TH_CLASS,
  PAGE_CONTENT_CLASS,
  SEV_BORDER,
} from "@/lib/ops-ui";
import { getNumberParam, getStringParam, mergeSearchParams } from "@/lib/search-params";
import type { OpsIncident, PaginatedResponse, SortDirection } from "@/lib/ops-types";

function ScopeList({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {items.slice(0, 3).map((item) => (
        <span key={item} className="rounded border border-white/8 bg-white/[0.03] px-1.5 py-0.5 text-[0.63rem] text-slate-500">
          {item}
        </span>
      ))}
      {items.length > 3 && (
        <span className="text-[0.63rem] text-slate-600">+{items.length - 3}</span>
      )}
    </div>
  );
}

function OpsIncidentsPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [response, setResponse] = useState<PaginatedResponse<OpsIncident> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);

  const q = getStringParam(searchParams, "q");
  const severity = getStringParam(searchParams, "severity");
  const status = getStringParam(searchParams, "status", "open");
  const sortBy = getStringParam(searchParams, "sort_by", "updated_at") as "updated_at" | "severity";
  const sortDir = getStringParam(searchParams, "sort_dir", "desc") as SortDirection;
  const page = getNumberParam(searchParams, "page", 1);
  const pageSize = getNumberParam(searchParams, "page_size", 25);
  const incidents = response?.items ?? [];

  function updateParams(updates: Record<string, string | number | null | undefined>) {
    const next = mergeSearchParams(new URLSearchParams(searchParams.toString()), updates);
    router.replace(next ? `${pathname}?${next}` : pathname);
  }

  function toggleSort(nextSortBy: typeof sortBy) {
    const nextSortDir: SortDirection = sortBy === nextSortBy && sortDir === "desc" ? "asc" : "desc";
    updateParams({ sort_by: nextSortBy, sort_dir: nextSortDir, page: 1 });
  }

  async function load() {
    const result = await fetchOpsIncidents({
      q: q || undefined,
      status: status || undefined,
      severity: severity || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      page,
      page_size: pageSize,
    });
    setResponse(result);
    setError(null);
  }

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsIncidents({
      q: q || undefined,
      status: status || undefined,
      severity: severity || undefined,
      sort_by: sortBy,
      sort_dir: sortDir,
      page,
      page_size: pageSize,
    })
      .then((result) => { if (!cancelled) { setResponse(result); setError(null); } })
      .catch((err) => { if (!cancelled) setError(getErrorMessage(err)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, q, severity, status, sortBy, sortDir]);

  // 30-second auto-refresh
  useEffect(() => {
    const id = setInterval(() => { load().catch(() => {}); }, 30_000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, q, severity, status, sortBy, sortDir]);

  async function handleRefresh() {
    setIsBusy(true);
    try { await load(); } catch (err) { setError(getErrorMessage(err)); } finally { setIsBusy(false); }
  }

  return (
    <div className="min-h-full">
      <PageHeader
        title="Incidents"
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Incidents" }]} />}
        actions={(
          <Button variant="outline" size="sm" onClick={() => { void handleRefresh(); }} disabled={isBusy}>
            <RefreshCcw className="size-3.5" />
            {isBusy ? "Refreshing…" : "Refresh"}
          </Button>
        )}
      />

      <div className={PAGE_CONTENT_CLASS}>
        {error ? <div className={OPS_ERROR_CLASS}>{error}</div> : null}

        {/* Filter bar */}
        <div className={FILTER_BAR_CLASS}>
          <input
            value={q}
            onChange={(e) => updateParams({ q: e.target.value, page: 1 })}
            placeholder="Search title, summary, host…"
            className={OPS_CONTROL_CLASS}
          />
          <select
            value={status}
            onChange={(e) => updateParams({ status: e.target.value || null, page: 1 })}
            className={OPS_CONTROL_CLASS}
          >
            <option value="open">Open incidents</option>
            <option value="">All incidents</option>
          </select>
          <select
            value={severity}
            onChange={(e) => updateParams({ severity: e.target.value || null, page: 1 })}
            className={OPS_CONTROL_CLASS}
          >
            <option value="">All severities</option>
            {(response?.facets?.severities ?? []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>

        {/* Table */}
        <div className={OPS_SECTION_CARD_CLASS}>
          <div className="overflow-x-auto">
            <table className="w-full table-fixed">
              <colgroup>
                <col style={{ width: "36%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "30%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "9%" }} />
              </colgroup>
              <thead>
                <tr className="border-b border-white/8 bg-white/[0.02]">
                  <th className={OPS_TH_CLASS}>Incident</th>
                  <th className={OPS_TH_CLASS}>
                    <SortButton
                      label="Severity"
                      active={sortBy === "severity"}
                      direction={sortDir}
                      onClick={() => toggleSort("severity")}
                    />
                  </th>
                  <th className={OPS_TH_CLASS}>AI Summary</th>
                  <th className={OPS_TH_CLASS}>Status / Scope</th>
                  <th className={OPS_TH_CLASS}>
                    <SortButton
                      label="Updated"
                      active={sortBy === "updated_at"}
                      direction={sortDir}
                      onClick={() => toggleSort("updated_at")}
                    />
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {isLoading ? (
                  <SkeletonTableRows rows={6} cols={5} />
                ) : (
                  incidents.map((incident) => (
                    <tr
                      key={incident.id}
                      className="align-top transition hover:bg-white/[0.03]"
                    >
                      {/* Incident — title + meta */}
                      <td className={cn("px-4 py-2.5 border-l-2", SEV_BORDER[incident.severity] ?? "border-l-slate-700/50")}>
                        <Link
                          href={`/ops/incidents/${incident.id}`}
                          className="line-clamp-1 text-sm font-medium text-white transition hover:text-cyan-200"
                        >
                          {incident.title}
                        </Link>
                        <p className="mt-0.5 flex items-center gap-2 text-xs text-slate-600">
                          {incident.incident_no && (
                            <span className="font-mono text-[0.68rem] text-slate-700">{incident.incident_no}</span>
                          )}
                          <span className="truncate">{incident.hostname ?? incident.primary_source_ip ?? "—"}</span>
                        </p>
                      </td>

                      {/* Severity */}
                      <td className="px-4 py-2.5">
                        <StatusBadge value={incident.severity} />
                      </td>

                      {/* AI Summary */}
                      <td className="px-4 py-2.5">
                        <p className="line-clamp-2 text-xs leading-relaxed text-slate-400">
                          {incident.ai_summary || incident.summary || "Pending analysis."}
                        </p>
                      </td>

                      {/* Status / Scope */}
                      <td className="px-4 py-2.5">
                        <StatusBadge value={incident.status} />
                        <ScopeList items={incident.affected_scope} />
                      </td>

                      {/* Updated */}
                      <td className="px-4 py-2.5">
                        <CompactTime value={incident.updated_at} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {!isLoading && incidents.length === 0 && (
            <OpsEmptyState icon={Siren} title="No incidents matched the current filters." />
          )}
        </div>

        {response ? (
          <PaginationControls
            page={response.page}
            totalPages={response.total_pages}
            totalItems={response.total}
            pageSize={response.page_size}
            onPageChange={(nextPage) => updateParams({ page: nextPage })}
          />
        ) : null}
      </div>
    </div>
  );
}

export default function OpsIncidentsPage() {
  return (
    <Suspense fallback={<div className="px-6 py-10 text-sm text-slate-400 sm:px-8">Loading…</div>}>
      <OpsIncidentsPageContent />
    </Suspense>
  );
}
