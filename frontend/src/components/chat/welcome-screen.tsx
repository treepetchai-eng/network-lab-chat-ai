"use client";

import { motion } from "framer-motion";
import { Network, Cpu, ShieldCheck, Route, Search } from "lucide-react";

const SUGGESTIONS = [
  { icon: Search, text: "List all devices in inventory" },
  { icon: Route, text: "Show BGP summary on core_router" },
  { icon: Cpu, text: "Check interface status on dist_switch" },
  { icon: ShieldCheck, text: "Is core_router reachable via SSH?" },
];

interface WelcomeScreenProps {
  onSuggestion?: (text: string) => void;
}

export function WelcomeScreen({ onSuggestion }: WelcomeScreenProps) {
  return (
    <div className="flex min-h-full items-center justify-center">
      <div className="mx-auto flex w-full max-w-[52rem] flex-col items-center px-3 py-8 sm:px-4 sm:py-16 md:px-6 text-center">
        {/* Animated logo */}
        <motion.div
          initial={{ scale: 0.8, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="relative"
        >
          <div className="absolute inset-0 scale-150 rounded-full bg-cyan-400/15 blur-2xl" />
          <div className="relative flex h-14 w-14 sm:h-[4.5rem] sm:w-[4.5rem] items-center justify-center rounded-2xl sm:rounded-3xl border border-cyan-300/20 bg-[linear-gradient(135deg,rgba(8,42,64,0.95),rgba(12,132,176,0.82))] shadow-[0_0_40px_rgba(34,211,238,0.2),inset_0_1px_0_rgba(255,255,255,0.12)]">
            <Network className="h-6 w-6 sm:h-8 sm:w-8 text-white drop-shadow-[0_0_8px_rgba(103,232,249,0.5)]" />
          </div>
        </motion.div>

        {/* Title with gradient */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.5 }}
        >
          <h2 className="mt-5 sm:mt-7 text-xl sm:text-3xl md:text-[2.2rem] font-semibold tracking-[-0.025em]">
            <span className="bg-[linear-gradient(135deg,#ffffff_0%,#a5f3fc_50%,#67e8f9_100%)] bg-clip-text text-transparent">
              Network Copilot
            </span>
          </h2>
          <p className="mt-2 text-[0.82rem] sm:text-[0.92rem] font-medium text-slate-400">
            AI-powered network operations assistant
          </p>
        </motion.div>

        {/* Description */}
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.5 }}
          className="mt-3 sm:mt-4 max-w-lg text-[0.84rem] sm:text-[0.94rem] leading-7 sm:leading-8 text-slate-500"
        >
          Ask about routes, protocols, device health, or troubleshooting — I&apos;ll SSH into your lab devices and gather evidence in real time.
        </motion.p>

        {/* Suggestion cards */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4, duration: 0.5 }}
          className="mt-8 sm:mt-10 grid w-full max-w-xl grid-cols-1 gap-2.5 sm:grid-cols-2 sm:gap-3"
        >
          {SUGGESTIONS.map(({ icon: Icon, text }, index) => (
            <motion.button
              key={text}
              type="button"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.45 + index * 0.07 }}
              onClick={() => onSuggestion?.(text)}
              className="group flex items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.025] px-4 py-3.5 text-left text-[0.84rem] sm:text-[0.88rem] text-slate-300 transition-all duration-300 hover:border-cyan-300/20 hover:bg-cyan-400/[0.06] hover:text-cyan-50 hover:shadow-[0_0_0_1px_rgba(34,211,238,0.06),0_12px_28px_rgba(2,7,18,0.28)] active:scale-[0.98]"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] transition-colors group-hover:border-cyan-300/20 group-hover:bg-cyan-400/10">
                <Icon className="h-3.5 w-3.5 text-slate-400 transition-colors group-hover:text-cyan-200" />
              </span>
              <span className="leading-snug">{text}</span>
            </motion.button>
          ))}
        </motion.div>

        {/* Subtle hint */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
          className="mt-6 sm:mt-8 flex items-center gap-2 text-[0.68rem] sm:text-[0.72rem] text-slate-600"
        >
          <kbd className="rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.6rem] text-slate-500">Enter</kbd>
          <span>to send</span>
          <span className="mx-1 text-slate-700">·</span>
          <kbd className="rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 font-mono text-[0.6rem] text-slate-500">Shift+Enter</kbd>
          <span>for new line</span>
        </motion.div>
      </div>
    </div>
  );
}
