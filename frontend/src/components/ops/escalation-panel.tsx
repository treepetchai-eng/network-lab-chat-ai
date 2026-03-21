"use client";

import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";
import { OPS_INNER_CARD_CLASS } from "@/lib/ops-ui";

interface EscalationPanelProps {
  reason: string;
  escalationContext: {
    analysis: string;
    root_cause: string;
    confidence_score: number;
    created_at: string | null;
  } | null;
  availableActions: string[];
  busy: boolean;
  onRetrigger: (mode: "full" | "troubleshoot_only") => void;
  onResolve: () => void;
}

export function EscalationPanel({
  reason,
  escalationContext,
  availableActions,
  busy,
  onRetrigger,
  onResolve,
}: EscalationPanelProps) {
  return (
    <div className="rounded-xl border border-orange-500/25 bg-orange-500/[0.04] p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start gap-3">
        <AlertTriangle className="size-4 mt-0.5 shrink-0 text-orange-400" />
        <div>
          <h3 className="text-sm font-semibold text-orange-100">Escalation Required</h3>
          <p className="mt-0.5 text-xs text-slate-400">{reason || "AI could not propose an automated fix for this incident."}</p>
        </div>
      </div>

      {/* AI analysis */}
      {escalationContext && (escalationContext.analysis || escalationContext.root_cause) && (
        <div className={OPS_INNER_CARD_CLASS + " space-y-3"}>
          <h4 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400">AI Analysis</h4>
          {escalationContext.root_cause && (
            <div>
              <p className="text-xs font-medium text-slate-400">Likely cause</p>
              <div className="mt-0.5 text-sm text-slate-200">
                <MarkdownRenderer content={escalationContext.root_cause} />
              </div>
            </div>
          )}
          {escalationContext.analysis && (
            <div>
              <p className="text-xs font-medium text-slate-400">Summary</p>
              <div className="mt-0.5 text-sm text-slate-300">
                <MarkdownRenderer content={escalationContext.analysis} />
              </div>
            </div>
          )}
          {escalationContext.confidence_score > 0 && (
            <p className="text-xs text-slate-500">
              Confidence: {escalationContext.confidence_score}%
            </p>
          )}
        </div>
      )}

      {/* Operator guidance */}
      <div className={OPS_INNER_CARD_CLASS}>
        <h4 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-400 mb-2">Suggested Next Steps</h4>
        <ul className="space-y-1 text-xs text-slate-300">
          <li>• Check physical connectivity (cables, optics, patch panels)</li>
          <li>• Verify hardware LEDs on the affected device and upstream switches</li>
          <li>• If a provider link — raise a support ticket with carrier</li>
          <li>• If you have additional context, use Re-investigate to refresh AI analysis</li>
        </ul>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {availableActions.includes("retrigger_full") && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onRetrigger("full")}
            className="border-sky-500/30 text-sky-300 hover:bg-sky-500/10"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin mr-1" /> : <RefreshCw className="size-3.5 mr-1" />}
            Re-investigate &amp; Troubleshoot
          </Button>
        )}
        {availableActions.includes("retrigger_troubleshoot") && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onRetrigger("troubleshoot_only")}
            className="border-sky-500/30 text-sky-300 hover:bg-sky-500/10"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin mr-1" /> : <RefreshCw className="size-3.5 mr-1" />}
            Re-troubleshoot
          </Button>
        )}
        {availableActions.includes("resolve_manual") && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={onResolve}
            className="border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/10"
          >
            Resolve Manually
          </Button>
        )}
      </div>
    </div>
  );
}
