"use client";

import { motion } from "framer-motion";
import { Network, Cpu, ShieldCheck, Route, Search, Wifi } from "lucide-react";

const SUGGESTIONS = [
  { icon: Search,      text: "List all devices in inventory" },
  { icon: Route,       text: "Show BGP summary on all core routers" },
  { icon: Cpu,         text: "Check interface status on all distribution switches" },
  { icon: ShieldCheck, text: "Is every core router reachable via SSH?" },
];

interface WelcomeScreenProps {
  onSuggestion?: (text: string) => void;
}

export function WelcomeScreen({ onSuggestion }: WelcomeScreenProps) {
  return (
    <div className="flex min-h-full items-center justify-center">
      <div className="mx-auto flex w-full max-w-[48rem] flex-col items-center px-4 py-12 text-center sm:py-20">

        {/* Icon */}
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="flex h-12 w-12 items-center justify-center rounded-xl border border-cyan-500/25 bg-cyan-500/10 ring-1 ring-inset ring-cyan-500/10"
        >
          <Wifi className="h-5 w-5 text-cyan-400" />
        </motion.div>

        {/* Title */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1, duration: 0.4 }}
          className="mt-5"
        >
          <h2 className="text-xl font-semibold tracking-tight text-white sm:text-2xl">
            Network Copilot
          </h2>
          <p className="mt-1.5 text-[0.82rem] text-slate-500">
            Ask anything — I'll SSH into your devices and gather evidence in real time.
          </p>
        </motion.div>

        {/* Suggestion cards */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.22, duration: 0.4 }}
          className="mt-8 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2"
        >
          {SUGGESTIONS.map(({ icon: Icon, text }, i) => (
            <motion.button
              key={text}
              type="button"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.28 + i * 0.06 }}
              onClick={() => onSuggestion?.(text)}
              className="group flex items-center gap-3 rounded-lg border border-white/[0.07] bg-white/[0.03] px-4 py-3 text-left text-[0.82rem] text-slate-400 transition hover:border-cyan-500/20 hover:bg-cyan-500/[0.05] hover:text-slate-200"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/8 bg-white/[0.04] transition group-hover:border-cyan-500/20 group-hover:bg-cyan-500/10">
                <Icon className="h-3.5 w-3.5 text-slate-500 transition group-hover:text-cyan-400" />
              </span>
              {text}
            </motion.button>
          ))}
        </motion.div>

        {/* Hint */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="mt-6 flex items-center gap-2 text-[0.7rem] text-slate-700"
        >
          <kbd className="rounded border border-white/8 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[0.6rem] text-slate-600">Enter</kbd>
          <span>to send</span>
          <span className="mx-1">·</span>
          <kbd className="rounded border border-white/8 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[0.6rem] text-slate-600">Shift+Enter</kbd>
          <span>new line</span>
        </motion.div>
      </div>
    </div>
  );
}
