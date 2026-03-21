import { cn } from "@/lib/utils";

const styles: Record<string, { badge: string; dot: string }> = {
  // Incident statuses
  new:           { badge: "border-sky-400/25 bg-sky-400/10 text-sky-100",      dot: "bg-sky-400" },
  acknowledged:  { badge: "border-violet-400/25 bg-violet-400/10 text-violet-100", dot: "bg-violet-400" },
  in_progress:   { badge: "border-cyan-400/25 bg-cyan-400/10 text-cyan-100",   dot: "bg-cyan-400" },
  open:          { badge: "border-sky-400/25 bg-sky-400/10 text-sky-100",     dot: "bg-sky-400" },
  investigating: { badge: "border-cyan-400/25 bg-cyan-400/10 text-cyan-100",   dot: "bg-cyan-400 animate-pulse" },
  monitoring:    { badge: "border-amber-400/25 bg-amber-400/10 text-amber-100",dot: "bg-amber-400" },
  resolved:      { badge: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100", dot: "bg-emerald-400" },
  // Approval statuses
  pending:       { badge: "border-amber-400/25 bg-amber-400/10 text-amber-100",dot: "bg-amber-400 animate-pulse" },
  approved:      { badge: "border-cyan-400/25 bg-cyan-400/10 text-cyan-100",   dot: "bg-cyan-400" },
  rejected:      { badge: "border-rose-400/25 bg-rose-400/10 text-rose-100",   dot: "bg-rose-400" },
  executed:      { badge: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100", dot: "bg-emerald-400" },
  awaiting_approval: { badge: "border-amber-400/25 bg-amber-400/10 text-amber-100", dot: "bg-amber-400 animate-pulse" },
  awaiting_second_approval: { badge: "border-amber-400/25 bg-amber-400/10 text-amber-100", dot: "bg-amber-400 animate-pulse" },
  "auto-execute": { badge: "border-teal-400/25 bg-teal-400/10 text-teal-100",  dot: "bg-teal-400" },
  auto_execute:  { badge: "border-teal-400/25 bg-teal-400/10 text-teal-100",  dot: "bg-teal-400" },
  escalation_needed: { badge: "border-fuchsia-400/25 bg-fuchsia-400/10 text-fuchsia-100", dot: "bg-fuchsia-400 animate-pulse" },
  // Job/Task statuses
  queued:        { badge: "border-slate-400/25 bg-slate-400/10 text-slate-200",dot: "bg-slate-400" },
  running:       { badge: "border-cyan-400/25 bg-cyan-400/10 text-cyan-100",   dot: "bg-cyan-400 animate-pulse" },
  succeeded:     { badge: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100", dot: "bg-emerald-400" },
  failed:        { badge: "border-rose-400/25 bg-rose-400/10 text-rose-100",   dot: "bg-rose-400" },
  // Severity
  high:          { badge: "border-rose-400/25 bg-rose-400/10 text-rose-100",   dot: "bg-rose-400" },
  critical:      { badge: "border-fuchsia-400/25 bg-fuchsia-400/10 text-fuchsia-100", dot: "bg-fuchsia-400" },
  medium:        { badge: "border-amber-400/25 bg-amber-400/10 text-amber-100",dot: "bg-amber-400" },
  low:           { badge: "border-emerald-400/25 bg-emerald-400/10 text-emerald-100", dot: "bg-emerald-400" },
};

const fallback = { badge: "border-white/10 bg-white/[0.04] text-slate-200", dot: "bg-slate-400" };

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const key = (value ?? "unknown").toLowerCase();
  const s = styles[key] ?? fallback;
  const display = (value ?? "unknown").replace(/_/g, " ");
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.72rem] font-medium tracking-[0.18em]",
      s.badge,
    )}>
      <span className={cn("inline-block size-1.5 shrink-0 rounded-full", s.dot)} />
      {display}
    </span>
  );
}
