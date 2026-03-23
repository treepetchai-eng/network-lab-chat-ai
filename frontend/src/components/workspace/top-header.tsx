"use client";

import Image from "next/image";
import Link from "next/link";
import { LayoutDashboard, Plus, Activity, ClipboardList, Wifi } from "lucide-react";
import { useState, useEffect } from "react";
import { ConfirmDialog } from "@/components/layout/confirm-dialog";
import { fetchDashboard } from "@/lib/aiops-api";

interface TopHeaderProps {
  onNewChat: () => void;
  hasMessages: boolean;
}

function useLiveCounts() {
  const [counts, setCounts] = useState({ active_incidents: 0, pending_approvals: 0 });
  useEffect(() => {
    const load = async () => {
      try {
        const d = await fetchDashboard();
        setCounts({
          active_incidents:  d.metrics?.active_incidents  ?? 0,
          pending_approvals: d.metrics?.pending_approvals ?? 0,
        });
      } catch { /* ignore */ }
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);
  return counts;
}

export function TopHeader({ onNewChat, hasMessages }: TopHeaderProps) {
  const [showConfirm, setShowConfirm] = useState(false);
  const counts = useLiveCounts();

  const handleNewChat = () => {
    if (hasMessages) { setShowConfirm(true); return; }
    onNewChat();
  };

  return (
    <>
      <header className="sticky top-0 z-20 flex h-12 items-center gap-4 border-b border-white/[0.07] bg-[#080d16]/95 px-4 backdrop-blur-sm sm:px-6">
        {/* Brand */}
        <div className="flex items-center gap-2.5">
          <div className="flex h-6 w-6 items-center justify-center rounded bg-cyan-500/15 ring-1 ring-cyan-500/30">
            <Wifi className="h-3.5 w-3.5 text-cyan-400" />
          </div>
          <span className="text-[0.8rem] font-semibold tracking-tight text-white">Network Copilot</span>
          <span className="hidden text-white/20 sm:block">·</span>
          <span className="hidden text-[0.75rem] text-slate-500 sm:block">AI Chat Assistant</span>
        </div>

        {/* Right side */}
        <div className="flex flex-1 items-center justify-end gap-2">
          {/* Live incident pills */}
          {counts.active_incidents > 0 && (
            <Link
              href="/aiops/incidents"
              className="hidden items-center gap-1.5 rounded border border-rose-500/20 bg-rose-500/[0.07] px-2.5 py-0.5 text-[0.7rem] font-medium text-rose-300 transition hover:bg-rose-500/[0.12] sm:flex"
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-rose-400" />
              {counts.active_incidents} active incident{counts.active_incidents !== 1 ? "s" : ""}
            </Link>
          )}
          {counts.pending_approvals > 0 && (
            <Link
              href="/aiops/approvals"
              className="hidden items-center gap-1.5 rounded border border-fuchsia-500/20 bg-fuchsia-500/[0.07] px-2.5 py-0.5 text-[0.7rem] font-medium text-fuchsia-300 transition hover:bg-fuchsia-500/[0.12] sm:flex"
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-fuchsia-400" />
              {counts.pending_approvals} awaiting approval
            </Link>
          )}

          {/* New chat */}
          <button
            onClick={handleNewChat}
            className="inline-flex items-center gap-1.5 rounded border border-white/8 bg-white/[0.04] px-2.5 py-1 text-[0.75rem] text-slate-400 transition hover:border-white/14 hover:text-slate-200"
          >
            <Plus className="h-3 w-3" />
            <span className="hidden sm:inline">New Chat</span>
          </button>

          {/* AIOps Console link */}
          <Link
            href="/aiops"
            className="inline-flex items-center gap-1.5 rounded border border-cyan-500/20 bg-cyan-500/[0.07] px-2.5 py-1 text-[0.75rem] text-cyan-300 transition hover:bg-cyan-500/[0.12] hover:text-cyan-100"
          >
            <LayoutDashboard className="h-3 w-3" />
            <span className="hidden sm:inline">AIOps Console</span>
          </Link>
        </div>
      </header>

      <ConfirmDialog
        open={showConfirm}
        onOpenChange={setShowConfirm}
        onConfirm={() => { setShowConfirm(false); onNewChat(); }}
      />
    </>
  );
}
