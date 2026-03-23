"use client";

import Image from "next/image";
import Link from "next/link";
import { motion } from "framer-motion";
import { LayoutDashboard, Plus } from "lucide-react";
import { useState } from "react";
import { ConfirmDialog } from "@/components/layout/confirm-dialog";

interface TopHeaderProps {
  onNewChat: () => void;
  hasMessages: boolean;
}

export function TopHeader({ onNewChat, hasMessages }: TopHeaderProps) {
  const [showConfirm, setShowConfirm] = useState(false);

  const handleNewChat = () => {
    if (hasMessages) {
      setShowConfirm(true);
      return;
    }
    onNewChat();
  };

  return (
    <>
      <motion.header
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="sticky top-0 z-20 border-b border-white/8 bg-[linear-gradient(180deg,rgba(6,11,20,0.92),rgba(6,11,20,0.72))] backdrop-blur-2xl"
      >
        <div className="mx-auto w-full max-w-[92rem] px-3 py-2.5 sm:px-4 sm:py-3 md:px-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2.5 sm:gap-3">
              <div className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-cyan-300/16 bg-white/[0.04] shadow-[0_0_32px_rgba(34,211,238,0.14)] backdrop-blur-xl sm:h-10 sm:w-10 sm:rounded-2xl">
                <div className="absolute inset-1 rounded-[10px] bg-[radial-gradient(circle_at_30%_30%,rgba(103,232,249,0.18),transparent_62%)] sm:rounded-[14px]" />
                <Image src="/logo.svg" alt="Network Copilot" width={20} height={20} className="relative z-10 sm:h-[22px] sm:w-[22px]" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-[0.9rem] font-semibold tracking-[0.01em] text-white sm:text-[1rem]">Network Copilot</h1>
                  <div className="flex items-center gap-1.5 rounded-full border border-emerald-300/16 bg-emerald-400/8 px-2 py-0.5">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    </span>
                    <span className="text-[0.58rem] font-medium uppercase tracking-[0.12em] text-emerald-300/80">Online</span>
                  </div>
                </div>
                <p className="hidden text-[0.72rem] text-slate-500 sm:block">AI-powered network chat assistant</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Link
                href="/aiops"
                className="group inline-flex items-center gap-1.5 rounded-xl border border-cyan-300/16 bg-cyan-400/[0.07] px-2.5 py-1.5 text-xs text-cyan-100 transition-all duration-300 hover:border-cyan-300/28 hover:bg-cyan-400/[0.12] hover:text-white hover:shadow-[0_0_22px_rgba(34,211,238,0.12)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/30 active:scale-[0.97] sm:gap-2 sm:rounded-2xl sm:px-3.5 sm:py-2 sm:text-sm"
              >
                <LayoutDashboard className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                <span className="hidden xs:inline">AIOps Console</span>
              </Link>
              <button
                onClick={handleNewChat}
                className="group inline-flex items-center gap-1.5 rounded-xl border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs text-slate-300 transition-all duration-300 hover:border-cyan-300/22 hover:bg-cyan-400/[0.07] hover:text-white hover:shadow-[0_0_20px_rgba(34,211,238,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/30 active:scale-[0.97] sm:gap-2 sm:rounded-2xl sm:px-3.5 sm:py-2 sm:text-sm"
              >
                <Plus className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                <span className="hidden xs:inline">New Chat</span>
              </button>
            </div>
          </div>
        </div>
      </motion.header>
      <ConfirmDialog
        open={showConfirm}
        onOpenChange={setShowConfirm}
        onConfirm={() => {
          setShowConfirm(false);
          onNewChat();
        }}
      />
    </>
  );
}
