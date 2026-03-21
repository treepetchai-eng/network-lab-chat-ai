import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

interface OpsEmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  className?: string;
}

export function OpsEmptyState({ icon: Icon, title, description, className }: OpsEmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-5 py-10 text-center", className)}>
      {Icon && <Icon className="mb-3 size-8 text-slate-600" />}
      <p className="text-sm font-medium text-slate-400">{title}</p>
      {description && <p className="mt-1 text-xs text-slate-500">{description}</p>}
    </div>
  );
}
