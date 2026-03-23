import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "default" | "critical" | "warning" | "success" | "info";

interface MetricCardProps {
  label: string;
  value: number | string;
  icon: LucideIcon;
  variant?: Variant;
  sub?: string;
}

const VARIANT_STYLES: Record<Variant, { icon: string; value: string; ring: string; bg: string }> = {
  default:  { icon: "text-slate-400",   value: "text-white",       ring: "ring-white/8",          bg: "bg-white/[0.03]"     },
  critical: { icon: "text-rose-400",    value: "text-rose-200",    ring: "ring-rose-500/20",      bg: "bg-rose-500/[0.06]"  },
  warning:  { icon: "text-amber-400",   value: "text-amber-200",   ring: "ring-amber-500/20",     bg: "bg-amber-500/[0.06]" },
  success:  { icon: "text-emerald-400", value: "text-emerald-200", ring: "ring-emerald-500/20",   bg: "bg-emerald-500/[0.05]" },
  info:     { icon: "text-sky-400",     value: "text-sky-200",     ring: "ring-sky-500/20",       bg: "bg-sky-500/[0.05]"   },
};

export function MetricCard({ label, value, icon: Icon, variant = "default", sub }: MetricCardProps) {
  const s = VARIANT_STYLES[variant];
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg p-3.5 ring-1 transition",
        s.bg,
        s.ring,
      )}
    >
      <div className={cn("mt-0.5 shrink-0 rounded p-1.5 ring-1 ring-inset", s.bg, s.ring)}>
        <Icon className={cn("h-3.5 w-3.5", s.icon)} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[0.68rem] font-medium uppercase tracking-widest text-slate-500">{label}</p>
        <p className={cn("mt-0.5 text-[1.45rem] font-semibold leading-none tracking-tight", s.value)}>{value}</p>
        {sub ? <p className="mt-1 text-[0.72rem] text-slate-600">{sub}</p> : null}
      </div>
    </div>
  );
}
