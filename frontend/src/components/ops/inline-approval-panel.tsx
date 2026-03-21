"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Terminal, RotateCcw, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ops/status-badge";
import {
  approveOpsApproval,
  rejectOpsApproval,
  executeOpsApproval,
  getErrorMessage,
} from "@/lib/ops-api";
import { OPS_INNER_CARD_CLASS, OPS_CONTROL_CLASS, OPS_ERROR_CLASS } from "@/lib/ops-ui";
import type { OpsApproval, LabRole } from "@/lib/ops-types";

interface InlineApprovalPanelProps {
  approval: OpsApproval;
  actorName: string;
  actorRole: LabRole | string;
  onActionComplete: () => void;
}

function CommandBlock({ label, text }: { label: string; text: string | null }) {
  const [open, setOpen] = useState(false);
  if (!text?.trim()) return null;
  return (
    <div className={OPS_INNER_CARD_CLASS + " !p-3"}>
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setOpen(!open)}
      >
        {open
          ? <ChevronDown className="size-3.5 shrink-0 text-slate-500" />
          : <ChevronRight className="size-3.5 shrink-0 text-slate-500" />}
        <span className="text-xs font-medium text-slate-300">{label}</span>
      </button>
      {open && (
        <pre className="mt-2 max-h-48 overflow-auto rounded border border-white/6 bg-black/30 p-2 font-mono text-xs text-slate-300 whitespace-pre-wrap">
          {text}
        </pre>
      )}
    </div>
  );
}

export function InlineApprovalPanel({
  approval,
  actorName,
  actorRole,
  onActionComplete,
}: InlineApprovalPanelProps) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canApprove = approval.status === "pending" || approval.status === "awaiting_second_approval";
  const canExecute = approval.status === "approved" && approval.execution_status === "approved";

  async function handleApprove() {
    setBusy(true);
    setError(null);
    try {
      await approveOpsApproval(approval.id, actorName, actorRole as LabRole, comment || undefined);
      setComment("");
      onActionComplete();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    const trimmed = comment.trim();
    if (!trimmed) {
      setError("Rejection reason is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await rejectOpsApproval(approval.id, actorName, actorRole as LabRole, trimmed);
      setComment("");
      onActionComplete();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleExecute() {
    setBusy(true);
    setError(null);
    try {
      await executeOpsApproval(approval.id, actorName, actorRole as LabRole);
      onActionComplete();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.04] p-5 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start gap-3">
        <ShieldCheck className="size-4 mt-0.5 shrink-0 text-amber-400" />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-white">{approval.title}</h3>
          <div className="mt-1 flex flex-wrap gap-2">
            <StatusBadge value={approval.status} />
            <StatusBadge value={approval.risk_level} />
          </div>
        </div>
      </div>

      {/* Details */}
      <div className="grid gap-1.5 text-xs text-slate-300 sm:grid-cols-2">
        {approval.target_host && (
          <p><span className="text-slate-500">Target: </span>{approval.target_host}</p>
        )}
        <p><span className="text-slate-500">Readiness: </span>{approval.readiness_score}/100</p>
        {approval.rationale && (
          <p className="sm:col-span-2"><span className="text-slate-500">Rationale: </span>{approval.rationale}</p>
        )}
      </div>

      {/* Commands */}
      <div className="space-y-1.5">
        <CommandBlock label="Commands to execute" text={approval.commands_text} />
        <CommandBlock label="Verify commands" text={approval.verify_commands_text} />
        <CommandBlock label="Rollback commands" text={approval.rollback_commands_text} />
      </div>

      {/* Comment box (for approve/reject) */}
      {(canApprove || canExecute) && !canExecute && (
        <textarea
          rows={2}
          placeholder="Comment (required for rejection, optional for approval)…"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          className={`${OPS_CONTROL_CLASS} resize-none text-xs`}
        />
      )}

      {/* Error */}
      {error && (
        <p className={OPS_ERROR_CLASS}>
          {error}
        </p>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {canApprove && (
          <>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={handleApprove}
              className="border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10"
            >
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
              Approve
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={handleReject}
              className="border-rose-500/30 text-rose-300 hover:bg-rose-500/10"
            >
              Reject
            </Button>
          </>
        )}
        {canExecute && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={handleExecute}
            className="border-sky-500/30 text-sky-300 hover:bg-sky-500/10"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin mr-1" /> : <Terminal className="size-3.5 mr-1" />}
            {busy ? "Executing…" : "Execute"}
          </Button>
        )}
        {approval.status === "rejected" && (
          <div className="flex items-center gap-1.5 text-xs text-rose-300">
            <RotateCcw className="size-3.5" />
            Proposal rejected — use Re-troubleshoot to generate a new proposal.
          </div>
        )}
      </div>
    </div>
  );
}
