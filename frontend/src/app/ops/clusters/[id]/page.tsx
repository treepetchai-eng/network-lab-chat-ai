"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Layers } from "lucide-react";
import { cn } from "@/lib/utils";
import { CompactTime } from "@/components/ops/compact-time";
import { PageHeader } from "@/components/ops/page-header";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonSection } from "@/components/ops/ops-skeleton";
import { StatusBadge } from "@/components/ops/status-badge";
import { fetchOpsClusterDetail, getErrorMessage } from "@/lib/ops-api";
import {
  OPS_SECTION_CLASS,
  OPS_TABLE_WRAPPER_CLASS,
  OPS_TH_CLASS,
  OPS_TEXT_LINK_CLASS,
  OPS_ERROR_CLASS,
  SEV_BORDER,
} from "@/lib/ops-ui";
import type { OpsClusterDetail } from "@/lib/ops-types";

export default function ClusterDetailPage() {
  const { id } = useParams<{ id: string }>();
  const clusterId = Number(id);
  const [cluster, setCluster] = useState<OpsClusterDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    fetchOpsClusterDetail(clusterId)
      .then((data) => { setCluster(data as OpsClusterDetail); setError(null); })
      .catch((e) => setError(getErrorMessage(e)))
      .finally(() => setIsLoading(false));
  }, [clusterId]);

  if (isLoading) return <div className="px-6 py-6 sm:px-8"><SkeletonSection lines={6} /></div>;
  if (error) return <div className="px-6 py-6 sm:px-8"><div className={OPS_ERROR_CLASS}>{error}</div></div>;
  if (!cluster) return <div className="px-6 py-6 sm:px-8"><OpsEmptyState icon={Layers} title="Cluster not found." /></div>;

  return (
    <div className="min-h-full">
      <PageHeader
        title={cluster.title}
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Clusters", href: "/ops/clusters" }, { label: `Cluster #${cluster.id}` }]} />}
      />

      <div className="space-y-5 px-6 py-6 sm:px-8">
        {/* Metadata */}
        <div className={OPS_SECTION_CLASS}>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={cluster.severity} />
            <StatusBadge value={cluster.status} />
            <span className="text-sm text-slate-400">{cluster.member_count} device{cluster.member_count !== 1 ? "s" : ""} affected</span>
          </div>
          {cluster.root_cause_summary && (
            <div className="mt-3 rounded-lg border border-cyan-500/20 bg-cyan-500/[0.08] px-4 py-3">
              <p className="mb-1 text-[0.65rem] uppercase tracking-[0.18em] text-cyan-400">Root Cause Summary</p>
              <p className="whitespace-pre-wrap text-sm leading-6 text-cyan-50">{cluster.root_cause_summary}</p>
            </div>
          )}
          <div className="mt-3 flex items-center gap-4">
            <div><span className="text-[0.65rem] uppercase tracking-[0.18em] text-slate-500">Created </span><CompactTime value={cluster.created_at} /></div>
            <div><span className="text-[0.65rem] uppercase tracking-[0.18em] text-slate-500">Updated </span><CompactTime value={cluster.updated_at} /></div>
          </div>
        </div>

        {/* Member incidents */}
        <div className={OPS_TABLE_WRAPPER_CLASS}>
          <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-white/8">
            <colgroup>
              <col className="w-[36%]" />
              <col className="w-[14%]" />
              <col className="w-[14%]" />
              <col className="w-[20%]" />
              <col className="w-[16%]" />
            </colgroup>
            <thead className="bg-white/[0.03]">
              <tr>
                <th className={OPS_TH_CLASS}>Incident</th>
                <th className={OPS_TH_CLASS}>Severity</th>
                <th className={OPS_TH_CLASS}>Status</th>
                <th className={OPS_TH_CLASS}>Device</th>
                <th className={OPS_TH_CLASS}>Opened</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/6">
              {cluster.incidents.map((inc) => (
                <tr key={inc.id} className="align-top transition hover:bg-white/[0.04]">
                  <td className={cn("px-4 py-3 border-l-2", SEV_BORDER[inc.severity] ?? "border-l-slate-700/50")}>
                    <Link href={`/ops/incidents/${inc.id}`} className={OPS_TEXT_LINK_CLASS}>
                      <span className="mr-2 font-mono text-xs text-slate-500">{inc.incident_no}</span>
                      <span className="font-medium">{inc.title}</span>
                    </Link>
                  </td>
                  <td className="px-4 py-3"><StatusBadge value={inc.severity} /></td>
                  <td className="px-4 py-3"><StatusBadge value={inc.status} /></td>
                  <td className="px-4 py-3 text-sm text-slate-300">{inc.hostname ?? inc.primary_source_ip ?? "—"}</td>
                  <td className="px-4 py-3"><CompactTime value={inc.opened_at} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {cluster.incidents.length === 0 && (
            <OpsEmptyState title="No incidents in this cluster." />
          )}
        </div>
      </div>
    </div>
  );
}
