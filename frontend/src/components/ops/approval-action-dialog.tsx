import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { TimestampItem } from "@/components/ops/timestamp-item";
import { StatusBadge } from "@/components/ops/status-badge";
import { OPS_CONTROL_CLASS } from "@/lib/ops-ui";
import type { OpsApproval } from "@/lib/ops-types";

interface ApprovalActionDialogProps {
  approval: OpsApproval | null;
  action: "approve" | "reject" | "execute" | null;
  open: boolean;
  busy: boolean;
  actorName: string;
  actorRole: string;
  comment: string;
  onCommentChange: (value: string) => void;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

const ACTION_COPY = {
  approve: {
    title: "Approve change proposal?",
    description: "This will approve the proposal and immediately start execution in the lab workflow.",
    button: "Approve and run",
    variant: "outline" as const,
  },
  reject: {
    title: "Reject change proposal?",
    description: "This will stop the proposal from being executed until a new one is created.",
    button: "Reject proposal",
    variant: "destructive" as const,
  },
  execute: {
    title: "Execute approved change?",
    description: "Fallback execution path for proposals that were approved earlier but not run yet.",
    button: "Execute fallback",
    variant: "default" as const,
  },
};

export function ApprovalActionDialog({
  approval,
  action,
  open,
  busy,
  actorName,
  actorRole,
  comment,
  onCommentChange,
  onOpenChange,
  onConfirm,
}: ApprovalActionDialogProps) {
  if (!approval || !action) {
    return null;
  }

  const copy = ACTION_COPY[action];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl border-white/10 bg-[linear-gradient(180deg,rgba(8,13,24,0.96),rgba(7,11,20,0.9))] text-white shadow-[0_30px_80px_rgba(2,7,18,0.58)] backdrop-blur-2xl">
        <DialogHeader>
          <DialogTitle className="text-lg text-white">{copy.title}</DialogTitle>
          <DialogDescription className="text-slate-400">
            {copy.description}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={approval.status} />
            <StatusBadge value={approval.risk_level} />
          </div>

          <div className="rounded-2xl border border-white/8 bg-[#0c1520] p-4">
            <h3 className="text-base font-semibold text-white">{approval.title}</h3>
            <div className="mt-3 grid gap-3 text-sm text-slate-300 md:grid-cols-2">
              <p><span className="text-slate-500">Target:</span> {approval.target_host ?? "-"}</p>
              <p><span className="text-slate-500">Incident:</span> {approval.incident_title ?? "-"}</p>
              <p><span className="text-slate-500">Requested by:</span> {approval.requested_by}</p>
              <p><span className="text-slate-500">Action:</span> {approval.action?.label ?? approval.action_id}</p>
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <TimestampItem label="Requested" value={approval.requested_at} emptyLabel="No request time" />
            <TimestampItem label="Decided" value={approval.decided_at} emptyLabel="Awaiting decision" />
            <TimestampItem label="Executed" value={approval.executed_at} emptyLabel="Not executed" />
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl border border-white/8 bg-[#0c1520] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Risk and readiness</p>
              <p className="mt-2 text-sm text-slate-200">Risk: {approval.risk_level}</p>
              <p className="mt-1 text-sm text-slate-200">Readiness: {approval.readiness} ({approval.readiness_score})</p>
              <p className="mt-1 text-sm text-slate-400">Approval role: {approval.required_approval_role}</p>
              <p className="mt-1 text-sm text-slate-400">Execution role: {approval.required_execution_role}</p>
            </div>
            <div className="rounded-2xl border border-white/8 bg-[#0c1520] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Decision context</p>
              <p className="mt-2 text-sm text-slate-200">Actor: {actorName}</p>
              <p className="mt-1 text-sm uppercase tracking-[0.18em] text-cyan-200">{actorRole}</p>
              <p className="mt-2 text-sm text-slate-400">
                {approval.action?.description ?? "No action description available."}
              </p>
            </div>
          </div>

          {approval.rationale ? (
            <div className="rounded-2xl border border-white/8 bg-[#0c1520] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Rationale</p>
              <p className="mt-2 text-sm leading-6 text-slate-200">{approval.rationale}</p>
            </div>
          ) : null}

          {approval.commands_text ? (
            <div className="rounded-2xl border border-white/8 bg-[#09111a] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Commands</p>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-emerald-100">{approval.commands_text}</pre>
            </div>
          ) : null}

          {approval.verify_commands_text ? (
            <div className="rounded-2xl border border-white/8 bg-[#09111a] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Verification</p>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-cyan-100">{approval.verify_commands_text}</pre>
            </div>
          ) : null}

          {approval.rollback_commands_text ? (
            <div className="rounded-2xl border border-white/8 bg-[#09111a] p-4">
              <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Rollback</p>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-amber-100">{approval.rollback_commands_text}</pre>
            </div>
          ) : null}

          <div className="rounded-2xl border border-white/8 bg-[#0c1520] p-4">
            <p className="text-[0.72rem] uppercase tracking-[0.18em] text-slate-500">Reviewer note</p>
            <textarea
              value={comment}
              onChange={(event) => onCommentChange(event.target.value)}
              placeholder={action === "reject" ? "Why are you rejecting this proposal?" : "Optional review comment"}
              rows={3}
              className={`mt-3 ${OPS_CONTROL_CLASS}`}
            />
            {action === "reject" && comment.trim() === "" && (
              <p className="mt-1 text-xs text-red-400">A rejection reason is required.</p>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="border border-white/10 bg-white/[0.03] text-slate-200 hover:bg-white/[0.06]">
            Cancel
          </Button>
          <Button variant={copy.variant} onClick={onConfirm} disabled={busy || (action === "reject" && comment.trim() === "")}>
            {busy ? "Working..." : copy.button}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
