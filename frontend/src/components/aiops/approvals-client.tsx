"use client";

import Link from "next/link";
import { useEffect, useState, useCallback } from "react";
import {
  RefreshCw,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
  Terminal,
  Check,
  Play,
  RotateCcw,
  CheckCircle2,
  XCircle,
  Loader2,
  Server,
  Eye,
  Ban,
} from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { approveProposal, executeProposal, fetchApprovals } from "@/lib/aiops-api";
import type { AIOpsExecution, AIOpsProposal } from "@/lib/aiops-types";

const POLL_INTERVAL = 15_000;

/* ── Risk badge ─────────────────────────────────────────────────────── */
function RiskBadge({ level }: { level: string }) {
  const cls =
    level === "high"
      ? "border-rose-500/30 bg-rose-500/10 text-rose-300"
      : level === "medium"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
        : "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-[0.67rem] font-semibold uppercase tracking-wide ${cls}`}
    >
      Risk: {level}
    </span>
  );
}

/* ── Command block ───────────────────────────────────────────────────── */
function CommandBlock({ label, commands, icon: Icon }: { label: string; commands: string[]; icon: React.ElementType }) {
  if (!commands?.length) return null;
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">
        <Icon className="h-3 w-3" />
        {label}
      </p>
      <div className="rounded-md border border-white/[0.07] bg-[#0a0f1a] p-3 font-mono text-[0.78rem] leading-relaxed text-emerald-300">
        {commands.map((cmd, i) => (
          <div key={i} className="flex gap-2">
            <span className="select-none text-slate-700">$</span>
            <span>{cmd}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Execution output ────────────────────────────────────────────────── */
function ExecutionResult({ execution }: { execution: AIOpsExecution }) {
  const success = execution.status === "completed";
  return (
    <div
      className={`rounded-md border p-4 ${success ? "border-emerald-500/20 bg-emerald-500/[0.04]" : "border-rose-500/20 bg-rose-500/[0.04]"}`}
    >
      <div className="mb-3 flex items-center gap-2">
        {success ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
        ) : (
          <XCircle className="h-4 w-4 text-rose-400" />
        )}
        <span className={`text-[0.8rem] font-semibold ${success ? "text-emerald-300" : "text-rose-300"}`}>
          {success ? "Execution Completed" : "Execution Failed"}
        </span>
        {execution.completed_at && (
          <span className="ml-auto text-[0.7rem] text-slate-600">
            {new Date(execution.completed_at).toLocaleTimeString()}
          </span>
        )}
      </div>

      {execution.output && (
        <div className="mb-3 overflow-x-auto rounded border border-white/[0.06] bg-[#080c14] p-3">
          <pre className="whitespace-pre-wrap font-mono text-[0.74rem] leading-relaxed text-slate-300">
            {execution.output}
          </pre>
        </div>
      )}

      {execution.verification_notes && (
        <div>
          <p className="mb-1 text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">
            Verification output
          </p>
          <div className="overflow-x-auto rounded border border-white/[0.06] bg-[#080c14] p-3">
            <pre className="whitespace-pre-wrap font-mono text-[0.74rem] leading-relaxed text-slate-400">
              {execution.verification_notes}
            </pre>
          </div>
        </div>
      )}

      {execution.verification_status === "auto_checked" && (
        <p className="mt-2 flex items-center gap-1.5 text-[0.7rem] text-emerald-500">
          <CheckCircle2 className="h-3 w-3" />
          Verification commands passed
        </p>
      )}
    </div>
  );
}

/* ── Single proposal card ─────────────────────────────────────────────── */
type ActionState = "idle" | "approving" | "executing";

export function ProposalCard({
  proposal: initial,
  onDone,
}: {
  proposal: AIOpsProposal;
  onDone: (updated: AIOpsProposal) => void;
}) {
  const [proposal, setProposal] = useState(initial);
  const [expanded, setExpanded]   = useState(false);
  const [actionState, setActionState] = useState<ActionState>("idle");
  const [execution, setExecution] = useState<AIOpsExecution | null>(null);
  const [error, setError]         = useState<string | null>(null);

  const incidentNo = proposal.incident_no ?? "";

  async function handleApprove() {
    setError(null);
    setActionState("approving");
    try {
      const detail = await approveProposal(incidentNo, "lab-operator");
      if (detail.proposal) {
        const updated = { ...proposal, ...detail.proposal, incident_no: incidentNo };
        setProposal(updated);
        onDone(updated);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Approve failed");
    } finally {
      setActionState("idle");
    }
  }

  async function handleExecute() {
    setError(null);
    setActionState("executing");
    setExpanded(true); // always expand before call so user sees progress/result
    try {
      const detail = await executeProposal(incidentNo, "lab-operator");
      if (detail.proposal) {
        const updated = { ...proposal, ...detail.proposal, incident_no: incidentNo };
        setProposal(updated);
        onDone(updated);
      }
      if (detail.execution) {
        setExecution(detail.execution);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Execute failed — check backend logs");
    } finally {
      setActionState("idle");
    }
  }

  const isPending   = proposal.status === "pending";
  const isApproved  = proposal.status === "approved";
  const isCancelled = proposal.status === "cancelled";
  const isDone      = proposal.status === "executed" || !!execution;
  const isBusy      = actionState !== "idle";

  return (
    <div className="border-b border-white/[0.05] last:border-0">
      {/* Header row */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-3 px-5 py-4 text-left transition hover:bg-white/[0.02]"
      >
        {isCancelled ? (
          <Ban className="mt-0.5 h-4 w-4 shrink-0 text-slate-600" />
        ) : (
          <ShieldAlert className={`mt-0.5 h-4 w-4 shrink-0 ${isDone ? "text-slate-600" : "text-fuchsia-400"}`} />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[0.66rem] font-semibold uppercase tracking-widest text-slate-600">
              {proposal.incident_no}
            </span>
            <StatusBadge value={proposal.status} />
            <RiskBadge level={proposal.risk_level} />
          </div>
          <p className="mt-1 text-[0.84rem] font-semibold text-slate-100">{proposal.title}</p>
          <p className="mt-0.5 text-[0.75rem] leading-5 text-slate-500 line-clamp-2">{proposal.rationale}</p>
          {/* Inline status indicators visible even when collapsed */}
          {actionState !== "idle" && (
            <p className="mt-1 flex items-center gap-1.5 text-[0.73rem] text-amber-400 animate-pulse">
              <Loader2 className="h-3 w-3 animate-spin" />
              {actionState === "approving" ? "Approving…" : "Executing — SSH into device, applying config…"}
            </p>
          )}
          {error && actionState === "idle" && (
            <p className="mt-1 flex items-center gap-1.5 text-[0.73rem] text-rose-400">
              <XCircle className="h-3 w-3 shrink-0" />
              {error}
            </p>
          )}
        </div>
        <div className="ml-2 flex shrink-0 items-center gap-3">
          <span className="hidden text-[0.7rem] text-slate-600 sm:block">
            {new Date(proposal.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
          {expanded ? (
            <ChevronUp className="h-4 w-4 text-slate-600" />
          ) : (
            <ChevronDown className="h-4 w-4 text-slate-600" />
          )}
        </div>
      </button>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-white/[0.05] bg-white/[0.015] px-5 pb-5 pt-4">
          <div className="space-y-4">

            {/* Full rationale */}
            {proposal.rationale && (
              <p className="text-[0.78rem] leading-relaxed text-slate-400">{proposal.rationale}</p>
            )}

            {/* Target devices */}
            {proposal.target_devices?.length ? (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">
                  <Server className="h-3 w-3" />
                  Target devices
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {proposal.target_devices.map((d) => (
                    <span
                      key={d}
                      className="rounded border border-cyan-500/20 bg-cyan-500/[0.06] px-2 py-0.5 font-mono text-[0.75rem] text-cyan-300"
                    >
                      {d}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}

            {/* Commands */}
            <CommandBlock label="Commands to execute" commands={proposal.commands} icon={Terminal} />

            {/* Verification */}
            <CommandBlock label="Verification commands" commands={proposal.verification_commands} icon={Eye} />

            {/* Rollback */}
            {proposal.rollback_plan && (
              <div>
                <p className="mb-1.5 flex items-center gap-1.5 text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">
                  <RotateCcw className="h-3 w-3" />
                  Rollback plan
                </p>
                <p className="rounded-md border border-white/[0.07] bg-[#0a0f1a] p-3 text-[0.78rem] leading-relaxed text-amber-300/80">
                  {proposal.rollback_plan}
                </p>
              </div>
            )}

            {/* Cancelled notice */}
            {isCancelled && (
              <p className="flex items-center gap-2 rounded border border-slate-700/50 bg-slate-800/40 px-3 py-2.5 text-[0.78rem] text-slate-500">
                <Ban className="h-3.5 w-3.5 shrink-0 text-slate-600" />
                {proposal.cancelled_reason === "incident_auto_resolved"
                  ? "Proposal superseded — incident was auto-resolved before execution."
                  : "This proposal was cancelled."}
                <Link
                  href={`/aiops/incidents/${incidentNo}`}
                  className="ml-1 text-slate-400 underline-offset-2 hover:underline"
                >
                  View incident →
                </Link>
              </p>
            )}

            {/* Execution result */}
            {execution && <ExecutionResult execution={execution} />}

            {/* Error */}
            {error && (
              <p className="flex items-center gap-1.5 rounded border border-rose-500/20 bg-rose-500/[0.06] px-3 py-2 text-[0.78rem] text-rose-300">
                <XCircle className="h-3.5 w-3.5 shrink-0" />
                {error}
              </p>
            )}

            {/* Action buttons */}
            {!isDone && !isCancelled && (
              <div className="flex items-center gap-3 pt-1">
                {isPending && (
                  <button
                    onClick={handleApprove}
                    disabled={isBusy}
                    className="inline-flex items-center gap-2 rounded border border-fuchsia-500/30 bg-fuchsia-500/10 px-4 py-2 text-[0.8rem] font-semibold text-fuchsia-300 transition hover:bg-fuchsia-500/20 disabled:opacity-40"
                  >
                    {actionState === "approving" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Check className="h-3.5 w-3.5" />
                    )}
                    {actionState === "approving" ? "Approving…" : "Approve"}
                  </button>
                )}

                {isApproved && (
                  <button
                    onClick={handleExecute}
                    disabled={isBusy}
                    className="inline-flex items-center gap-2 rounded border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-[0.8rem] font-semibold text-emerald-300 transition hover:bg-emerald-500/20 disabled:opacity-40"
                  >
                    {actionState === "executing" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                    {actionState === "executing" ? "Executing on device…" : "Execute Now"}
                  </button>
                )}

                {actionState === "executing" && (
                  <span className="text-[0.75rem] text-slate-500 animate-pulse">
                    SSH-ing into device, applying config…
                  </span>
                )}

                <Link
                  href={`/aiops/incidents/${incidentNo}`}
                  className="ml-auto text-[0.72rem] text-slate-600 underline-offset-2 hover:text-slate-400 hover:underline"
                >
                  View incident →
                </Link>
              </div>
            )}

            {isDone && !execution && (
              <p className="flex items-center gap-1.5 text-[0.78rem] text-slate-500">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                Already executed — check incident timeline for output.
                <Link
                  href={`/aiops/incidents/${incidentNo}`}
                  className="ml-1 text-cyan-500 underline-offset-2 hover:underline"
                >
                  View incident
                </Link>
              </p>
            )}

            {isDone && execution && (
              <div className={`mt-2 flex items-center justify-between gap-3 rounded border px-3 py-2 ${execution.status === "completed" ? "border-emerald-500/25 bg-emerald-500/[0.05]" : "border-rose-500/20 bg-rose-500/[0.04]"}`}>
                <p className={`flex items-center gap-1.5 text-[0.78rem] font-medium ${execution.status === "completed" ? "text-emerald-300" : "text-rose-400"}`}>
                  {execution.status === "completed" ? (
                    <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 shrink-0" />
                  )}
                  {execution.status === "completed"
                    ? "Executed successfully — go to incident to confirm recovery"
                    : "Execution failed — go to incident for details"}
                </p>
                <Link
                  href={`/aiops/incidents/${incidentNo}`}
                  className={`shrink-0 rounded border px-3 py-1.5 text-[0.78rem] font-semibold transition ${execution.status === "completed" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20" : "border-rose-500/25 bg-rose-500/[0.07] text-rose-400 hover:bg-rose-500/15"}`}
                >
                  {execution.status === "completed" ? "Confirm Recovery →" : "View Incident →"}
                </Link>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

type FilterStatus = "all" | "pending" | "approved" | "executed" | "cancelled";

const FILTER_OPTIONS: { value: FilterStatus; label: string }[] = [
  { value: "all",       label: "All" },
  { value: "pending",   label: "Pending" },
  { value: "approved",  label: "Approved" },
  { value: "executed",  label: "Executed" },
  { value: "cancelled", label: "Cancelled" },
];

/* ── Main approvals client ────────────────────────────────────────────── */
export function ApprovalsClient({ initialApprovals }: { initialApprovals: AIOpsProposal[] }) {
  const [approvals, setApprovals] = useState(initialApprovals);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<FilterStatus>("all");

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setApprovals(await fetchApprovals());
    } catch {
      /* ignore */
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const id = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  function handleProposalUpdated(updated: AIOpsProposal) {
    setApprovals((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p)),
    );
  }

  // Count per status for filter badges
  const counts = {
    pending:   approvals.filter((p) => p.status === "pending").length,
    approved:  approvals.filter((p) => p.status === "approved").length,
    executed:  approvals.filter((p) => p.status === "executed").length,
    cancelled: approvals.filter((p) => p.status === "cancelled").length,
  };

  const filtered = approvals
    .filter((p) => filter === "all" || p.status === filter)
    .sort((a, b) => {
      // pending → approved → executed → cancelled
      const rank: Record<string, number> = { pending: 0, approved: 1, executed: 2, cancelled: 3 };
      return (rank[a.status] ?? 9) - (rank[b.status] ?? 9);
    });

  const actionableCount = counts.pending + counts.approved;

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
      {/* Filter bar */}
      <div className="flex items-center gap-1.5 border-b border-white/[0.05] px-5 py-3">
        {FILTER_OPTIONS.map(({ value, label }) => {
          const count = value === "all" ? approvals.length : counts[value as keyof typeof counts];
          const active = filter === value;
          return (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[0.7rem] font-medium transition ${
                active
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-500 hover:text-slate-300"
              }`}
            >
              {label}
              {count > 0 && (
                <span className={`rounded px-1 text-[0.62rem] font-semibold ${
                  active ? "bg-white/10 text-slate-300" : "text-slate-600"
                }`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* List */}
      {filtered.length ? (
        <div className={`divide-y divide-white/[0.05] ${filter !== "all" && filter !== "pending" && filter !== "approved" ? "opacity-75" : ""}`}>
          {filtered.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              onDone={handleProposalUpdated}
            />
          ))}
        </div>
      ) : (
        <div className="px-4 py-10 text-center">
          <ShieldAlert className="mx-auto h-8 w-8 text-slate-700" />
          {actionableCount === 0 && filter === "all" ? (
            <>
              <p className="mt-3 text-[0.84rem] font-medium text-slate-400">No pending proposals</p>
              <p className="mt-1 text-[0.76rem] text-slate-600">
                Proposals appear here after AI troubleshooting identifies a config-fixable issue.
              </p>
            </>
          ) : (
            <p className="mt-3 text-[0.84rem] font-medium text-slate-400">
              No {filter} proposals
            </p>
          )}
        </div>
      )}
    </SectionCard>
  );
}
