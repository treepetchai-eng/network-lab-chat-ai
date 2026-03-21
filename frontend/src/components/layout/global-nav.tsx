"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { APP_NAV_ITEMS, isNavItemActive } from "@/lib/app-nav";
import { cn } from "@/lib/utils";

interface GlobalNavProps {
  className?: string;
  tone?: "chat" | "ops";
}

export function GlobalNav({ className, tone = "chat" }: GlobalNavProps) {
  const pathname = usePathname();

  return (
    <nav className={cn("overflow-x-auto pb-1 scrollbar-none", className)} aria-label="Primary navigation">
      <div className="flex min-w-max items-center gap-2">
        {APP_NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = isNavItemActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "inline-flex cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35 sm:text-sm",
                tone === "ops"
                  ? active
                    ? "border-cyan-300/28 bg-cyan-300/12 text-white shadow-[0_12px_34px_rgba(34,211,238,0.14)]"
                    : "border-white/8 bg-white/[0.03] text-slate-300 hover:border-white/16 hover:bg-white/[0.06] hover:text-white"
                  : active
                    ? "border-amber-300/24 bg-amber-300/[0.12] text-white shadow-[0_12px_34px_rgba(251,191,36,0.12)]"
                    : "border-white/8 bg-white/[0.03] text-slate-300 hover:border-white/16 hover:bg-white/[0.06] hover:text-white",
              )}
            >
              <Icon className="size-3.5 sm:size-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
