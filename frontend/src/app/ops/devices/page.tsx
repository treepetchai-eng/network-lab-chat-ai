"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Network, RefreshCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { CompactTime } from "@/components/ops/compact-time";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonTableRows } from "@/components/ops/ops-skeleton";
import { PageHeader } from "@/components/ops/page-header";
import { PaginationControls } from "@/components/ops/pagination-controls";
import { SortButton } from "@/components/ops/sort-button";
import { Button } from "@/components/ui/button";
import { fetchOpsDevices, getErrorMessage } from "@/lib/ops-api";
import { OPS_CONTROL_CLASS, OPS_TABLE_WRAPPER_CLASS, OPS_TH_CLASS, OPS_ERROR_CLASS, OPS_TEXT_LINK_CLASS } from "@/lib/ops-ui";
import { mergeSearchParams, getNumberParam, getStringParam } from "@/lib/search-params";
import type { OpsDevice, PaginatedResponse, SortDirection } from "@/lib/ops-types";

function HealthDot({ enabled, openIncidentCount }: { enabled: boolean; openIncidentCount: number }) {
  const color = !enabled
    ? "bg-rose-400"
    : openIncidentCount > 0
    ? "bg-amber-400"
    : "bg-emerald-400";
  const title = !enabled ? "Disabled" : openIncidentCount > 0 ? `${openIncidentCount} open incident(s)` : "Healthy";
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", color)} title={title} />;
}

function OpsDevicesPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [response, setResponse] = useState<PaginatedResponse<OpsDevice> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const query = getStringParam(searchParams, "q");
  const site = getStringParam(searchParams, "site");
  const sortBy = getStringParam(searchParams, "sort_by", "hostname") as "hostname" | "site" | "role" | "open_incident_count" | "last_event_time";
  const sortDir = getStringParam(searchParams, "sort_dir", "asc") as SortDirection;
  const page = getNumberParam(searchParams, "page", 1);
  const pageSize = getNumberParam(searchParams, "page_size", 25);
  const devices = response?.items ?? [];
  const siteOptions = response?.facets?.sites ?? [];

  function updateParams(updates: Record<string, string | number | boolean | null | undefined>) {
    const next = mergeSearchParams(new URLSearchParams(searchParams.toString()), updates);
    router.replace(next ? `${pathname}?${next}` : pathname);
  }

  function toggleSort(nextSortBy: typeof sortBy) {
    const nextSortDir: SortDirection = sortBy === nextSortBy && sortDir === "asc" ? "desc" : "asc";
    updateParams({ sort_by: nextSortBy, sort_dir: nextSortDir, page: 1 });
  }

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsDevices({ q: query || undefined, site: site || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize })
      .then((r) => { if (!cancelled) { setResponse(r); setError(null); } })
      .catch((e) => { if (!cancelled) setError(getErrorMessage(e)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  }, [query, site, sortBy, sortDir, page, pageSize]);

  useEffect(() => {
    const id = setInterval(() => {
      fetchOpsDevices({ q: query || undefined, site: site || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize })
        .then((r) => setResponse(r)).catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  }, [query, site, sortBy, sortDir, page, pageSize]);

  async function handleRefresh() {
    setIsBusy(true);
    try {
      const r = await fetchOpsDevices({ q: query || undefined, site: site || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize });
      setResponse(r);
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="min-h-full">
      <PageHeader
        title="Devices"
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Devices" }]} />}
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
            value={query}
            onChange={(e) => updateParams({ q: e.target.value, page: 1 })}
            placeholder="Search hostname, IP, platform..."
            className={OPS_CONTROL_CLASS}
          />
          <select
            value={site}
            onChange={(e) => updateParams({ site: e.target.value || null, page: 1 })}
            className={OPS_CONTROL_CLASS}
          >
            <option value="">All sites</option>
            {siteOptions.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>

        <div className={OPS_TABLE_WRAPPER_CLASS}>
          <div className="overflow-x-auto">
            <table className="min-w-[900px] divide-y divide-white/8">
              <colgroup>
                <col className="w-[28%]" />
                <col className="w-[14%]" />
                <col className="w-[14%]" />
                <col className="w-[18%]" />
                <col className="w-[14%]" />
                <col className="w-[12%]" />
              </colgroup>
              <thead className="bg-white/[0.03]">
                <tr>
                  <th className={OPS_TH_CLASS}><SortButton label="Hostname" active={sortBy === "hostname"} direction={sortDir} onClick={() => toggleSort("hostname")} /></th>
                  <th className={OPS_TH_CLASS}><SortButton label="Site" active={sortBy === "site"} direction={sortDir} onClick={() => toggleSort("site")} /></th>
                  <th className={OPS_TH_CLASS}><SortButton label="Role" active={sortBy === "role"} direction={sortDir} onClick={() => toggleSort("role")} /></th>
                  <th className={OPS_TH_CLASS}>Platform</th>
                  <th className={OPS_TH_CLASS}><SortButton label="Incidents" active={sortBy === "open_incident_count"} direction={sortDir} onClick={() => toggleSort("open_incident_count")} /></th>
                  <th className={OPS_TH_CLASS}><SortButton label="Last Seen" active={sortBy === "last_event_time"} direction={sortDir} onClick={() => toggleSort("last_event_time")} /></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/6">
                {isLoading ? (
                  <SkeletonTableRows rows={5} cols={6} />
                ) : (
                  devices.map((d) => (
                    <tr key={d.id} className="align-top transition hover:bg-white/[0.04]">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <HealthDot enabled={d.enabled} openIncidentCount={d.open_incident_count} />
                          <div className="min-w-0">
                            <Link
                              href={`/ops/incidents?q=${encodeURIComponent(d.hostname)}`}
                              className={OPS_TEXT_LINK_CLASS}
                            >
                              <span className="font-medium">{d.hostname}</span>
                            </Link>
                            <p className="mt-0.5 text-xs text-slate-500">{d.mgmt_ip}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-slate-200">{d.site}</td>
                      <td className="px-4 py-3 text-sm text-slate-200">{d.device_role}</td>
                      <td className="px-4 py-3 text-sm text-slate-200">{d.os_platform}</td>
                      <td className="px-4 py-3">
                        {d.open_incident_count > 0 ? (
                          <Link
                            href={`/ops/incidents?q=${encodeURIComponent(d.hostname)}`}
                            className="inline-flex items-center gap-1 rounded-full border border-rose-400/25 bg-rose-400/10 px-2.5 py-1 text-xs font-medium text-rose-100 transition hover:bg-rose-400/15"
                          >
                            {d.open_incident_count} open
                          </Link>
                        ) : (
                          <span className="text-sm text-slate-500">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3"><CompactTime value={d.last_event_time} /></td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {!isLoading && devices.length === 0 ? (
            <OpsEmptyState icon={Network} title="No devices matched the current filters." />
          ) : null}
        </div>

        {response ? (
          <PaginationControls
            page={response.page} totalPages={response.total_pages}
            totalItems={response.total} pageSize={response.page_size}
            onPageChange={(p) => updateParams({ page: p })}
          />
        ) : null}
      </div>
    </div>
  );
}

export default function OpsDevicesPage() {
  return (
    <Suspense fallback={<div className="space-y-4 px-6 py-6 sm:px-8"><div className={OPS_TABLE_WRAPPER_CLASS}><table className="min-w-full"><tbody><SkeletonTableRows rows={6} cols={6} /></tbody></table></div></div>}>
      <OpsDevicesPageContent />
    </Suspense>
  );
}
