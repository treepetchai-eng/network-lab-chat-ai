import Link from "next/link";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

const accentMap: Record<string, { icon: string; value: string; subtitle: string }> = {
  rose:   { icon: "text-rose-400",   value: "text-rose-50",   subtitle: "text-rose-300" },
  amber:  { icon: "text-amber-400",  value: "text-amber-50",  subtitle: "text-amber-300" },
  cyan:   { icon: "text-cyan-400",   value: "text-cyan-50",   subtitle: "text-cyan-300" },
  sky:    { icon: "text-sky-400",    value: "text-sky-50",    subtitle: "text-sky-300" },
  emerald:{ icon: "text-emerald-400",value: "text-emerald-50",subtitle: "text-emerald-300" },
  slate:  { icon: "text-slate-400",  value: "text-white",     subtitle: "text-slate-400" },
};

interface OpsKpiCardProps {
  label: string;
  value: number;
  icon: LucideIcon;
  href: string;
  accentColor?: string;
  subtitle?: string;
}

export function OpsKpiCard({ label, value, icon: Icon, href, accentColor = "slate", subtitle }: OpsKpiCardProps) {
  const accent = accentMap[accentColor] ?? accentMap.slate;
  return (
    <Link
      href={href}
      className={cn(
        "block rounded-xl border border-white/8 bg-white/[0.03] p-5",
        "transition hover:border-white/16 hover:bg-white/[0.05]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</p>
        <Icon className={cn("size-4 shrink-0", accent.icon)} />
      </div>
      <p className={cn("mt-3 text-4xl font-semibold tabular-nums", accent.value)}>{value}</p>
      {subtitle && (
        <p className={cn("mt-1.5 text-xs", accent.subtitle)}>{subtitle}</p>
      )}
    </Link>
  );
}
