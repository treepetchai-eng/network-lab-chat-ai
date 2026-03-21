"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot } from "lucide-react";
import { APP_NAV_ITEMS, isNavItemActive } from "@/lib/app-nav";
import { OPS_ACTION_LINK_CLASS } from "@/lib/ops-ui";
import { cn } from "@/lib/utils";

export function OpsShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const navItems = APP_NAV_ITEMS.filter((item) => item.section === "start");

  return (
    <div className="min-h-dvh bg-[linear-gradient(180deg,#08111a_0%,#09131b_28%,#050a11_100%)] text-white">
      <div className="mx-auto max-w-[1520px] px-4 py-4 lg:px-6">
        <div className="rounded-2xl border border-white/10 bg-[#0a141d] px-4 py-3 shadow-[0_20px_60px_rgba(0,0,0,0.24)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[0.64rem] uppercase tracking-[0.18em] text-cyan-100/55">Ops Console</p>
              <h1 className="mt-1 text-xl font-semibold tracking-tight text-white">Network Operations</h1>
            </div>
            <Link
              href="/"
              className={OPS_ACTION_LINK_CLASS}
            >
              <Bot className="size-4" />
              Open chat
            </Link>
          </div>
        </div>
      </div>

      <div className="mx-auto grid min-h-dvh max-w-[1520px] gap-4 px-4 pb-4 lg:grid-cols-[15rem_minmax(0,1fr)] lg:px-6">
        <aside className="h-fit rounded-2xl border border-white/10 bg-[#0a141d] p-3 shadow-[0_28px_90px_rgba(0,0,0,0.28)] lg:sticky lg:top-4">
          <nav className="mt-2 space-y-1.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = isNavItemActive(pathname, item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "block cursor-pointer rounded-xl border px-3 py-2.5 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
                    active
                      ? "border-cyan-300/35 bg-cyan-300/12 text-white"
                      : "border-white/8 bg-white/[0.03] text-slate-200 hover:border-white/16 hover:bg-white/[0.06] hover:text-white",
                  )}
                >
                  <div className="flex items-center gap-3">
                    <Icon className="size-4 shrink-0" />
                    <p className="min-w-0 text-sm font-medium">{item.label}</p>
                  </div>
                </Link>
              );
            })}
          </nav>
        </aside>

        <div className="min-w-0 rounded-2xl border border-white/8 bg-[#08121b] shadow-[0_32px_110px_rgba(0,0,0,0.34)]">
          {children}
        </div>
      </div>
    </div>
  );
}
