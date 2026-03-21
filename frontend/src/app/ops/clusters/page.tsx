"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Layers, RefreshCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { CompactTime } from "@/components/ops/compact-time";
import { PageHeader } from "@/components/ops/page-header";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonTableRows } from "@/components/ops/ops-skeleton";
import { SortButton } from "@/components/ops/sort-button";
import { StatusBadge } from "@/components/ops/status-badge";
import { Button } from "@/components/ui/button";
import { fetchOpsClusters, getErrorMessage } from "@/lib/ops-api";
import {
  OPS_CONTROL_CLASS,
  OPS_TABLE_WRAPPER_CLASS,
  OPS_TH_CLASS,
  OPS_TEXT_LINK_CLASS,
  OPS_ERROR_CLASS,
  SEV_BORDER,
} from "@/lib/ops-ui";
import type { OpsIncidentCluster } from "@/lib/ops-types";

export default function ClustersPage() {
  const [clusters, setClusters] = useState<OpsIncidentCluster[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isBusy, setIsBusy] = useState(false);
  const [q, setQ] = useState("");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  async function load() {
    try {
      const res = await fetchOpsClusters() as { items: OpsIncidentCluster[] };
      setClusters(res.items ?? []);
      setError(null);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const id = setInterval(() => { void load(); }, 30_000);
    return () => clearInterval(id);
  }, []);

  const filtered = clusters.filter((c) =>
    !q || c.title.toLowerCase().includes(q.toLowerCase()),
  );
  const sorted = [...filtered].sort((a, b) => {
    const av = a.created_at ?? "";
    const bv = b.created_at ?? "";
    return sortDir === "desc" ? bv.localeCompare(av) : av.localeCompare(bv);
  });

  return (
    <div className="min-h-full">
      <PageHeader
        title="Clusters"
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Clusters" }]} />}
        actions={
          <Button variant="outline" onClick={() => { setIsBusy(true); load().finally(() => setIsBusy(false)); }} disabled={isBusy}>
            <RefreshCcw className="size-4" />
            {isBusy ? "Refreshing..." : "Refresh"}
          </Button>
        }
      />
      <div className="space-y-4 px-6 py-6 sm:px-8">
        {error && <div className={OPS_ERROR_CLASS}>{error}</div>}

        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter clusters..."
          className={OPS_CONTROL_CLASS}
        />

        <div className={OPS_TABLE_WRAPPER_CLASS}>
          <div className="overflow-x-auto">
          <table className="min-w-[900px] divide-y divide-white/8">
            <colgroup>
              <col className="w-[44%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
              <col className="w-[14%]" />
              <col className="w-[18%]" />
            </colgroup>
            <thead className="bg-white/[0.03]">
              <tr>
                <th className={OPS_TH_CLASS}>Cluster</th>
                <th className={OPS_TH_CLASS}>Severity</th>
                <th className={OPS_TH_CLASS}>Status</th>
                <th className={OPS_TH_CLASS}>Incidents</th>
                <th className={OPS_TH_CLASS}>
                  <SortButton
                    label="Created"
                    active
                    direction={sortDir}
                    onClick={() => setSortDir((d) => d === "desc" ? "asc" : "desc")}
                  />
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/6">
              {isLoading ? (
                <SkeletonTableRows rows={4} cols={5} />
              ) : (
                sorted.map((c) => (
                  <tr key={c.id} className="align-top transition hover:bg-white/[0.04]">
                    <td className={cn("px-4 py-3 border-l-2", SEV_BORDER[c.severity] ?? "border-l-slate-700/50")}>
                      <Link href={`/ops/clusters/${c.id}`} className={cn(OPS_TEXT_LINK_CLASS, "font-medium text-white transition hover:text-cyan-200")}>
                        {c.title}
                      </Link>
                      {c.root_cause_summary && (
                        <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{c.root_cause_summary}</p>
                      )}
                    </td>
                    <td className="px-4 py-3"><StatusBadge value={c.severity} /></td>
                    <td className="px-4 py-3"><StatusBadge value={c.status} /></td>
                    <td className="px-4 py-3">
                      <span className="font-mono tabular-nums text-sm text-slate-300">{c.member_count}</span>
                      <p className="text-xs text-slate-600">incident{c.member_count !== 1 ? "s" : ""}</p>
                    </td>
                    <td className="px-4 py-3"><CompactTime value={c.created_at} /></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          </div>
          {!isLoading && sorted.length === 0 && (
            <OpsEmptyState icon={Layers} title={q ? "No clusters matched your filter." : "No incident clusters yet."} description={q ? undefined : "Clusters form automatically when similar incidents occur across multiple devices."} />
          )}
        </div>
      </div>
    </div>
  );
}
