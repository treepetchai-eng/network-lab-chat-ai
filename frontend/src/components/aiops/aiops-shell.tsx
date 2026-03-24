"use client";

import Link from "next/link";
import {
  Activity,
  CheckCircle2,
  ClipboardList,
  LayoutDashboard,
  FileText,
  MessageSquare,
  Router,
  ShieldAlert,
  Wifi,
} from "lucide-react";
import { type ReactNode, Suspense } from "react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { fetchDashboard } from "@/lib/aiops-api";

/* ── Nav structure ────────────────────────────────────────────────────── */
const PRIMARY_NAV = [
  { href: "/aiops",           label: "Dashboard",  icon: LayoutDashboard, countKey: null },
  { href: "/aiops/incidents", label: "Incidents",  icon: Activity,        countKey: "active_incidents" as const },
  { href: "/aiops/approvals", label: "Approvals",  icon: ClipboardList,   countKey: "pending_approvals" as const },
  { href: "/aiops/history",   label: "History",    icon: CheckCircle2,    countKey: null },
] as const;

const TOOLS_NAV = [
  { href: "/aiops/logs",             label: "Logs",            icon: FileText },
  { href: "/aiops/devices",          label: "Devices",         icon: Router },
  { href: "/aiops/vulnerabilities",  label: "Vulnerabilities", icon: ShieldAlert },
] as const;

/* ── Live counts from dashboard API ────────────────────────────────────── */
type Counts = { active_incidents: number; pending_approvals: number };

function useLiveCounts(): Counts {
  const [counts, setCounts] = useState<Counts>({ active_incidents: 0, pending_approvals: 0 });
  useEffect(() => {
    const load = async () => {
      try {
        const d = await fetchDashboard();
        setCounts({
          active_incidents:  d.metrics?.active_incidents  ?? 0,
          pending_approvals: d.metrics?.pending_approvals ?? 0,
        });
      } catch (_) { /* counts stay at previous value */ }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);
  return counts;
}

/* ── NavItem ─────────────────────────────────────────────────────────── */
function NavItem({
  href, label, icon: Icon, count, active, mobile = false,
}: {
  href: string; label: string; icon: React.ElementType;
  count?: number; active: boolean; mobile?: boolean;
}) {
  if (mobile) {
    return (
      <Link href={href} className={cn("relative flex flex-col items-center gap-0.5 px-2 py-1 text-[0.58rem] font-medium", active ? "text-cyan-400" : "text-slate-500")}>
        <Icon className="h-4 w-4" />
        <span>{label}</span>
        {count ? (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-rose-500 text-[0.55rem] font-bold text-white">
            {count > 9 ? "9+" : count}
          </span>
        ) : null}
      </Link>
    );
  }

  return (
    <Link
      href={href}
      className={cn(
        "group flex items-center justify-between rounded px-3 py-2 text-[0.82rem] font-medium transition-colors",
        active
          ? "bg-cyan-500/10 text-cyan-300 ring-1 ring-inset ring-cyan-500/20"
          : "text-slate-400 hover:bg-white/[0.05] hover:text-slate-200",
      )}
    >
      <span className="flex items-center gap-2.5">
        <Icon className={cn("h-3.5 w-3.5 shrink-0 transition-colors", active ? "text-cyan-400" : "text-slate-500 group-hover:text-slate-300")} />
        {label}
      </span>
      {count ? (
        <span className={cn(
          "flex h-4.5 min-w-[1.1rem] items-center justify-center rounded px-1 text-[0.63rem] font-bold",
          count > 0 && label === "Approvals"
            ? "bg-fuchsia-500/20 text-fuchsia-300"
            : "bg-rose-500/20 text-rose-300",
        )}>
          {count}
        </span>
      ) : null}
    </Link>
  );
}

/* ── Shell ───────────────────────────────────────────────────────────── */
export function AIOpsShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const counts   = useLiveCounts();

  const isActive = (href: string) =>
    href === "/aiops" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);

  return (
    <div className="relative min-h-dvh bg-[#080d16] text-white">
      {/* Top bar */}
      <header className="sticky top-0 z-30 flex h-12 items-center gap-4 border-b border-white/[0.07] bg-[#080d16]/95 px-4 backdrop-blur-sm sm:px-6">
        <div className="flex items-center gap-2.5">
          <div className="flex h-6 w-6 items-center justify-center rounded bg-cyan-500/15 ring-1 ring-cyan-500/30">
            <Wifi className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <span className="text-[0.8rem] font-semibold tracking-tight text-white">AIOps Console</span>
          <span className="hidden text-white/20 sm:block">·</span>
          <span className="hidden text-[0.75rem] text-slate-500 sm:block">Network Incident Management</span>
        </div>
        <div className="flex flex-1 items-center justify-end gap-3">
          {counts.active_incidents > 0 && (
            <Link href="/aiops/incidents"
              className="hidden items-center gap-1.5 rounded border border-rose-500/20 bg-rose-500/[0.07] px-2.5 py-0.5 text-[0.7rem] font-medium text-rose-300 transition hover:bg-rose-500/[0.12] sm:flex">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-rose-400" />
              {counts.active_incidents} active incident{counts.active_incidents !== 1 ? "s" : ""}
            </Link>
          )}
          {counts.pending_approvals > 0 && (
            <Link href="/aiops/approvals"
              className="hidden items-center gap-1.5 rounded border border-fuchsia-500/20 bg-fuchsia-500/[0.07] px-2.5 py-0.5 text-[0.7rem] font-medium text-fuchsia-300 transition hover:bg-fuchsia-500/[0.12] sm:flex">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-fuchsia-400" />
              {counts.pending_approvals} awaiting approval
            </Link>
          )}
          <div className="flex items-center gap-1.5 rounded border border-emerald-500/20 bg-emerald-500/8 px-2 py-0.5">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            <span className="text-[0.65rem] font-medium uppercase tracking-widest text-emerald-400">Live</span>
          </div>
          <Link href="/"
            className="flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1 text-[0.75rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200">
            <MessageSquare className="h-3 w-3" />
            <span>Chat</span>
          </Link>
        </div>
      </header>

      <div className="flex">
        {/* Sidebar */}
        <aside className="hidden w-52 shrink-0 border-r border-white/[0.07] lg:block">
          <nav className="sticky top-12 p-3">

            {/* Primary workflow */}
            <p className="mb-1.5 px-3 text-[0.62rem] font-semibold uppercase tracking-widest text-slate-700">Workflow</p>
            <div className="space-y-0.5">
              {PRIMARY_NAV.map(({ href, label, icon, countKey }) => (
                <NavItem
                  key={href}
                  href={href}
                  label={label}
                  icon={icon}
                  active={isActive(href)}
                  count={countKey ? counts[countKey] : undefined}
                />
              ))}
            </div>

            {/* Tools */}
            <div className="my-3 border-t border-white/[0.06]" />
            <p className="mb-1.5 px-3 text-[0.62rem] font-semibold uppercase tracking-widest text-slate-700">Tools</p>
            <div className="space-y-0.5">
              {TOOLS_NAV.map(({ href, label, icon }) => (
                <NavItem key={href} href={href} label={label} icon={icon} active={isActive(href)} />
              ))}
            </div>

            {/* Pipeline hint */}
            <div className="my-3 border-t border-white/[0.06]" />
            <div className="px-3">
              <p className="text-[0.63rem] font-semibold uppercase tracking-widest text-slate-700">Pipeline</p>
              <div className="mt-1.5 space-y-0.5 text-[0.68rem] text-slate-700">
                {["Syslog in", "Parse & group", "LLM decide", "Troubleshoot", "Approval gate", "Execute & verify", "Resolved"].map((step, i, arr) => (
                  <div key={step} className="flex items-center gap-1.5">
                    <span className={cn("h-1.5 w-1.5 rounded-full", i === arr.length - 1 ? "bg-emerald-600" : "bg-slate-700")} />
                    {step}
                  </div>
                ))}
              </div>
            </div>
          </nav>
        </aside>

        {/* Main */}
        <main className="min-w-0 flex-1 pb-16 lg:pb-0">
          <Suspense>
            <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6">
              {children}
            </div>
          </Suspense>
        </main>
      </div>

      {/* Mobile bottom nav */}
      <nav className="fixed inset-x-0 bottom-0 z-30 flex items-center justify-around border-t border-white/[0.07] bg-[#080d16]/96 py-2 backdrop-blur-sm lg:hidden">
        {[...PRIMARY_NAV, ...TOOLS_NAV].map(({ href, label, icon }) => {
          const countKey = "countKey" in { href, label, icon } ? undefined : undefined;
          void countKey;
          const count = (href === "/aiops/incidents" ? counts.active_incidents
                       : href === "/aiops/approvals"  ? counts.pending_approvals
                       : 0) || undefined;
          return (
            <NavItem key={href} href={href} label={label} icon={icon} active={isActive(href)} count={count} mobile />
          );
        })}
      </nav>
    </div>
  );
}
