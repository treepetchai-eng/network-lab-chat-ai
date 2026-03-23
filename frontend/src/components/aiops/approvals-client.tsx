"use client";

import Link from "next/link";
import { useEffect, useState, useCallback } from "react";
import { RefreshCw, ChevronLeft, ChevronRight, ShieldAlert } from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchApprovals } from "@/lib/aiops-api";
import type { AIOpsProposal } from "@/lib/aiops-types";

const PAGE_SIZE = 10;
const POLL_INTERVAL = 15_000;

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

export function ApprovalsClient({ initialApprovals }: { initialApprovals: AIOpsProposal[] }) {
  const [approvals, setApprovals] = useState(initialApprovals);
  const [refreshing, setRefreshing] = useState(false);
  const [page, setPage] = useState(1);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try { setApprovals(await fetchApprovals()); }
    catch { /* ignore */ }
    finally { setRefreshing(false); }
  }, []);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const totalPages = Math.max(1, Math.ceil(approvals.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const paged      = approvals.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <SectionCard
      title="Approval Queue"
      eyebrow="Change Control"
      noPadding
      actions={
        <button
          onClick={refresh}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-[0.72rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200 disabled:opacity-40"
        >
          <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </button>
      }
    >
      {paged.length ? (
        <>
          <div className="divide-y divide-white/[0.05]">
            {paged.map((proposal) => (
              <Link
                key={proposal.id}
                href={`/aiops/incidents/${proposal.incident_no}`}
                className="flex items-start gap-3 px-4 py-4 transition hover:bg-white/[0.03]"
              >
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-fuchsia-400" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[0.66rem] font-semibold uppercase tracking-widest text-slate-600">{proposal.incident_no}</span>
                    <StatusBadge value={proposal.status} />
                    <RiskBadge level={proposal.risk_level} />
                  </div>
                  <p className="mt-1 text-[0.84rem] font-semibold text-slate-100">{proposal.title}</p>
                  <p className="mt-1 text-[0.77rem] leading-6 text-slate-500">{proposal.rationale}</p>
                  {proposal.target_devices?.length ? (
                    <p className="mt-1 text-[0.72rem] text-slate-600">
                      Targets: {proposal.target_devices.join(", ")}
                    </p>
                  ) : null}
                </div>
                <p className="shrink-0 text-[0.7rem] text-slate-600">
                  {proposal.created_at ? new Date(proposal.created_at).toLocaleDateString() : ""}
                </p>
              </Link>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-white/[0.07] px-4 py-3">
              <p className="text-[0.72rem] text-slate-600">{approvals.length} proposal{approvals.length !== 1 ? "s" : ""}</p>
              <div className="flex items-center gap-2">
                <button
                  disabled={safePage <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <span className="text-[0.72rem] text-slate-500">{safePage} / {totalPages}</span>
                <button
                  disabled={safePage >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="px-4 py-12 text-center">
          <ShieldAlert className="mx-auto h-8 w-8 text-slate-700" />
          <p className="mt-3 text-[0.84rem] font-medium text-slate-400">No pending proposals</p>
          <p className="mt-1 text-[0.76rem] text-slate-600">Proposals appear here after AI troubleshooting identifies a config-fixable issue.</p>
        </div>
      )}
    </SectionCard>
  );
}
