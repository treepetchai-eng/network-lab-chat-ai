"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ClipboardCheck, RefreshCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { ApprovalActionDialog } from "@/components/ops/approval-action-dialog";
import { useOpsIdentity } from "@/components/ops/ops-identity-context";
import { OpsBreadcrumb } from "@/components/ops/ops-breadcrumb";
import { OpsEmptyState } from "@/components/ops/ops-empty-state";
import { SkeletonTableRows } from "@/components/ops/ops-skeleton";
import { PageHeader } from "@/components/ops/page-header";
import { PaginationControls } from "@/components/ops/pagination-controls";
import { SortButton } from "@/components/ops/sort-button";
import { StatusBadge } from "@/components/ops/status-badge";
import { Button } from "@/components/ui/button";
import {
  approveOpsApproval,
  executeOpsApproval,
  fetchOpsApprovals,
  getErrorMessage,
  rejectOpsApproval,
} from "@/lib/ops-api";
import { OPS_CONTROL_CLASS, OPS_TABLE_WRAPPER_CLASS, OPS_TH_CLASS, OPS_TEXT_LINK_CLASS, OPS_ERROR_CLASS, OPS_INFO_CLASS, SEV_BORDER } from "@/lib/ops-ui";
import { mergeSearchParams, getNumberParam, getStringParam } from "@/lib/search-params";
import { CompactTime } from "@/components/ops/compact-time";
import type { OpsApproval, PaginatedResponse, SortDirection } from "@/lib/ops-types";

function OpsApprovalsPageContent() {
  const { actorName, actorRole } = useOpsIdentity();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [response, setResponse] = useState<PaginatedResponse<OpsApproval> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedApproval, setSelectedApproval] = useState<OpsApproval | null>(null);
  const [pendingAction, setPendingAction] = useState<"approve" | "reject" | "execute" | null>(null);
  const [actionComment, setActionComment] = useState("");

  const q = getStringParam(searchParams, "q");
  const status = getStringParam(searchParams, "status");
  const sortBy = getStringParam(searchParams, "sort_by", "requested_at") as "requested_at" | "decided_at" | "risk_level" | "status";
  const sortDir = getStringParam(searchParams, "sort_dir", "desc") as SortDirection;
  const page = getNumberParam(searchParams, "page", 1);
  const pageSize = getNumberParam(searchParams, "page_size", 25);
  const approvals = response?.items ?? [];
  const statusOptions = response?.facets?.statuses ?? [];

  function updateParams(updates: Record<string, string | number | boolean | null | undefined>) {
    const next = mergeSearchParams(new URLSearchParams(searchParams.toString()), updates);
    router.replace(next ? `${pathname}?${next}` : pathname);
  }

  function toggleSort(nextSortBy: typeof sortBy) {
    const nextSortDir: SortDirection = sortBy === nextSortBy && sortDir === "desc" ? "asc" : "desc";
    updateParams({ sort_by: nextSortBy, sort_dir: nextSortDir, page: 1 });
  }

  async function load() {
    const r = await fetchOpsApprovals({ q: q || undefined, status: status || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize });
    setResponse(r);
    setError(null);
  }

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchOpsApprovals({ q: q || undefined, status: status || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize })
      .then((r) => { if (!cancelled) { setResponse(r); setError(null); } })
      .catch((e) => { if (!cancelled) setError(getErrorMessage(e)); })
      .finally(() => { if (!cancelled) setIsLoading(false); });
    return () => { cancelled = true; };
  }, [q, status, sortBy, sortDir, page, pageSize]);

  useEffect(() => {
    const id = setInterval(() => {
      fetchOpsApprovals({ q: q || undefined, status: status || undefined, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize })
        .then((r) => setResponse(r)).catch(() => {});
    }, 30_000);
    return () => clearInterval(id);
  }, [q, status, sortBy, sortDir, page, pageSize]);

  async function handleRefresh() {
    setIsBusy(true);
    try { await load(); } catch (e) { setError(getErrorMessage(e)); } finally { setIsBusy(false); }
  }

  async function runAction() {
    if (!selectedApproval || !pendingAction) return;
    setIsBusy(true);
    try {
      const result =
        pendingAction === "approve" ? await approveOpsApproval(selectedApproval.id, actorName, actorRole, actionComment)
        : pendingAction === "reject" ? await rejectOpsApproval(selectedApproval.id, actorName, actorRole, actionComment)
        : await executeOpsApproval(selectedApproval.id, actorName, actorRole);
      setMessage(result.detail);
      setError(null);
      setPendingAction(null);
      setSelectedApproval(null);
      setActionComment("");
      await load();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setIsBusy(false);
    }
  }

  function openAction(approval: OpsApproval, action: "approve" | "reject" | "execute") {
    setSelectedApproval(approval);
    setPendingAction(action);
    setActionComment(approval.decision_comment ?? "");
  }

  return (
    <div className="min-h-full">
      <PageHeader
        title="Approvals"
        breadcrumb={<OpsBreadcrumb items={[{ label: "Dashboard", href: "/ops" }, { label: "Approvals" }]} />}
        actions={(
          <Button variant="outline" onClick={() => { void handleRefresh(); }} disabled={isBusy}>
            <RefreshCcw className="size-4" />
            {isBusy ? "Refreshing..." : "Refresh"}
          </Button>
        )}
      />

      <div className="space-y-4 px-6 py-6 sm:px-8">
        {message ? <div className={OPS_INFO_CLASS}>{message}</div> : null}
        {error ? <div className={OPS_ERROR_CLASS}>{error}</div> : null}

        <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_1fr]">
          <input
            value={q}
            onChange={(e) => updateParams({ q: e.target.value, page: 1 })}
            placeholder="Search title, target, rationale..."
            className={OPS_CONTROL_CLASS}
          />
          <select
            value={status}
            onChange={(e) => updateParams({ status: e.target.value || null, page: 1 })}
            className={OPS_CONTROL_CLASS}
          >
            <option value="">All statuses</option>
            {statusOptions.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>

        <div className={OPS_TABLE_WRAPPER_CLASS}>
          <div className="overflow-x-auto">
            <table className="min-w-[900px] divide-y divide-white/8">
              <colgroup>
                <col className="w-[40%]" />
                <col className="w-[10%]" />
                <col className="w-[14%]" />
                <col className="w-[12%]" />
                <col className="w-[24%]" />
              </colgroup>
              <thead className="bg-white/[0.03]">
                <tr>
                  <th className={OPS_TH_CLASS}>Proposal</th>
                  <th className={OPS_TH_CLASS}><SortButton label="Risk" active={sortBy === "risk_level"} direction={sortDir} onClick={() => toggleSort("risk_level")} /></th>
                  <th className={OPS_TH_CLASS}><SortButton label="Status" active={sortBy === "status"} direction={sortDir} onClick={() => toggleSort("status")} /></th>
                  <th className={OPS_TH_CLASS}><SortButton label="Requested" active={sortBy === "requested_at"} direction={sortDir} onClick={() => toggleSort("requested_at")} /></th>
                  <th className={OPS_TH_CLASS}>Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/6">
                {isLoading ? (
                  <SkeletonTableRows rows={5} cols={5} />
                ) : (
                  approvals.map((a) => (
                    <tr key={a.id} className="align-top transition hover:bg-white/[0.04]">
                      <td className={cn("px-4 py-3 border-l-2", SEV_BORDER[a.risk_level] ?? "border-l-slate-700/50")}>
                        <p className="font-medium text-white text-sm">{a.title}</p>
                        <p className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                          {a.target_host && <span>Target: {a.target_host}</span>}
                          {a.incident_id && (
                            <>
                              <span className="text-slate-700">·</span>
                              <Link href={`/ops/incidents/${a.incident_id}`} className={OPS_TEXT_LINK_CLASS}>
                                {a.incident_title ?? `INC ${a.incident_id}`}
                              </Link>
                            </>
                          )}
                        </p>
                        {a.rationale ? (
                          <p className="mt-0.5 line-clamp-1 text-xs text-slate-400" title={a.rationale}>
                            {a.rationale}
                          </p>
                        ) : null}
                      </td>
                      <td className="px-4 py-3"><StatusBadge value={a.risk_level} /></td>
                      <td className="px-4 py-3">
                        <StatusBadge value={a.status} />
                        {a.status === "approved" && a.execution_status && (
                          <p className="mt-1 text-xs text-slate-500">Exec: {a.execution_status}</p>
                        )}
                      </td>
                      <td className="px-4 py-3"><CompactTime value={a.requested_at} /></td>
                      <td className="px-4 py-3">
                        {["pending", "awaiting_second_approval"].includes(a.status) && (
                          <Button variant="outline" size="sm" onClick={() => openAction(a, "approve")} disabled={isBusy}>
                            Approve / Reject
                          </Button>
                        )}
                        {a.status === "approved" && (
                          <Button size="sm" onClick={() => openAction(a, "execute")} disabled={isBusy}>
                            Execute
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {!isLoading && approvals.length === 0 ? (
            <OpsEmptyState icon={ClipboardCheck} title="No approvals matched the current filters." />
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

      <ApprovalActionDialog
        approval={selectedApproval} action={pendingAction}
        open={Boolean(selectedApproval && pendingAction)} busy={isBusy}
        actorName={actorName} actorRole={actorRole}
        comment={actionComment} onCommentChange={setActionComment}
        onOpenChange={(open) => { if (!open) { setSelectedApproval(null); setPendingAction(null); setActionComment(""); } }}
        onConfirm={() => { void runAction(); }}
      />
    </div>
  );
}

export default function OpsApprovalsPage() {
  return (
    <Suspense fallback={<div className="px-6 py-10 text-sm text-slate-300 sm:px-8">Loading...</div>}>
      <OpsApprovalsPageContent />
    </Suspense>
  );
}
