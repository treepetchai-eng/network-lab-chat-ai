import { cn } from "@/lib/utils";

/* ── Human-readable labels ──────────────────────────────────────────────── */
const LABELS: Record<string, string> = {
  // Incident lifecycle phases (7 clear states)
  new:               "New",
  investigating:     "Investigating",
  active:            "Active",
  escalated:         "Escalated",
  awaiting_approval: "Needs Approval",
  approved:          "Approved",
  executing:         "Executing",
  verifying:         "Verifying",
  recovering:        "Recovering",
  monitoring:        "Monitoring",
  reopened:          "Reopened",
  resolved:          "Resolved",
  closed:            "Closed",
  triaged:           "Triaged",
  // Severity
  critical:          "Critical",
  warning:           "Warning",
  info:              "Info",
  // Proposal / execution
  pending:           "Pending",
  rejected:          "Rejected",
  completed:         "Completed",
  failed:            "Failed",
  cancelled:         "Cancelled",
  // Disposition
  no_action_needed:   "No Action Needed",
  needs_human_review: "Human Review",
  self_recovered:     "Self Recovered",
  monitor_further:    "Monitor",
  physical_issue:     "Physical Issue",
  external_issue:     "External",
  config_fix_possible:"Config Fixable",
  // Event states
  up:   "Up",
  down: "Down",
};

/* ── Colour map ─────────────────────────────────────────────────────────── */
const STYLES: Record<string, string> = {
  // Severity
  critical:          "border-rose-500/30   bg-rose-500/10   text-rose-300",
  warning:           "border-amber-500/30  bg-amber-500/10  text-amber-300",
  info:              "border-sky-500/30    bg-sky-500/10    text-sky-300",

  // Phase 1 – Detected
  new:               "border-sky-500/30    bg-sky-500/10    text-sky-300",
  reopened:          "border-red-500/30    bg-red-500/10    text-red-300",

  // Phase 2 – Analysis
  triaged:           "border-cyan-500/30   bg-cyan-500/10   text-cyan-300",
  investigating:     "border-indigo-500/30 bg-indigo-500/10 text-indigo-300",

  // Phase 3 – Open / Needs action
  active:            "border-rose-500/30   bg-rose-500/10   text-rose-300",
  escalated:         "border-orange-500/30 bg-orange-500/10 text-orange-300",

  // Phase 4 – Remediation gate
  awaiting_approval: "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300",
  approved:          "border-violet-500/30  bg-violet-500/10  text-violet-300",
  executing:         "border-orange-500/30  bg-orange-500/10  text-orange-300",
  verifying:         "border-cyan-500/30    bg-cyan-500/10    text-cyan-300",

  // Phase 5 – Recovery watch
  recovering:        "border-amber-500/30  bg-amber-500/10  text-amber-300",
  monitoring:        "border-yellow-500/30 bg-yellow-500/10 text-yellow-300",

  // Phase 6 – Closed (History)
  resolved:          "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  closed:            "border-slate-500/30   bg-slate-500/10   text-slate-400",

  // Proposal / execution
  pending:           "border-slate-500/30  bg-slate-500/10  text-slate-300",
  rejected:          "border-red-500/30    bg-red-500/10    text-red-300",
  completed:         "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  failed:            "border-rose-500/30   bg-rose-500/10   text-rose-300",
  cancelled:         "border-slate-700/40  bg-slate-700/20  text-slate-500",

  // Disposition
  no_action_needed:   "border-slate-500/30   bg-slate-500/10   text-slate-400",
  needs_human_review: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  self_recovered:     "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  monitor_further:    "border-yellow-500/30  bg-yellow-500/10  text-yellow-300",
  physical_issue:     "border-orange-500/30  bg-orange-500/10  text-orange-300",
  external_issue:     "border-slate-500/30   bg-slate-500/10   text-slate-300",
  config_fix_possible:"border-violet-500/30  bg-violet-500/10  text-violet-300",

  // Event states
  up:   "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  down: "border-rose-500/30    bg-rose-500/10    text-rose-300",

  // Timeline kinds
  decision:    "border-indigo-500/30 bg-indigo-500/10 text-indigo-300",
  event:       "border-sky-500/30    bg-sky-500/10    text-sky-300",
  summary:     "border-cyan-500/30   bg-cyan-500/10   text-cyan-300",
  troubleshoot:"border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-300",
  proposal:    "border-violet-500/30  bg-violet-500/10  text-violet-300",
  approval:    "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  execution:   "border-orange-500/30  bg-orange-500/10  text-orange-300",
  recovery:    "border-teal-500/30    bg-teal-500/10    text-teal-300",
};

const DOTS: Record<string, string> = {
  critical:   "bg-rose-400",
  active:     "bg-rose-400",
  warning:    "bg-amber-400",
  escalated:  "bg-orange-400",
  recovering: "bg-amber-400",
  monitoring: "bg-yellow-400",
  resolved:   "bg-emerald-400",
  closed:     "bg-slate-500",
  new:        "bg-sky-400",
  investigating: "bg-indigo-400",
  awaiting_approval: "bg-fuchsia-400",
  down:       "bg-rose-400",
  up:         "bg-emerald-400",
};

interface StatusBadgeProps {
  value: string;
  className?: string;
  showDot?: boolean;
}

export function StatusBadge({ value, className, showDot = false }: StatusBadgeProps) {
  const dotColor = DOTS[value];
  const label = LABELS[value] ?? value.replaceAll("_", " ");

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[0.67rem] font-semibold uppercase tracking-[0.06em]",
        STYLES[value] ?? "border-white/10 bg-white/[0.05] text-slate-400",
        className,
      )}
    >
      {(showDot || dotColor) && dotColor ? (
        <span className={cn("h-1.5 w-1.5 rounded-full", dotColor)} />
      ) : null}
      {label}
    </span>
  );
}
