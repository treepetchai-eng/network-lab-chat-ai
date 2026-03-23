import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface SectionCardProps {
  title: string;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  noPadding?: boolean;
}

export function SectionCard({ title, eyebrow, actions, children, className, noPadding }: SectionCardProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-white/[0.08] bg-[#0c1220]",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-4 border-b border-white/[0.07] px-4 py-3">
        <div className="flex items-center gap-2.5">
          {eyebrow ? (
            <span className="rounded border border-white/8 bg-white/[0.04] px-1.5 py-0.5 text-[0.62rem] font-semibold uppercase tracking-widest text-slate-500">
              {eyebrow}
            </span>
          ) : null}
          <h2 className="text-[0.88rem] font-semibold text-white">{title}</h2>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className={noPadding ? "" : "px-4 py-4"}>{children}</div>
    </section>
  );
}
