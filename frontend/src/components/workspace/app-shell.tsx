"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";

interface AppShellProps {
  header: ReactNode;
  children: ReactNode;
  error?: string | null;
}

export function AppShell({ header, children, error }: AppShellProps) {
  return (
    <div className="relative h-dvh overflow-hidden bg-[#080d16] text-white">
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
        <div className="absolute -left-24 top-0 h-[28rem] w-[28rem] rounded-full bg-cyan-400/[0.06] blur-[160px]" />
        <div className="absolute right-0 top-20 h-[24rem] w-[24rem] rounded-full bg-blue-500/[0.05] blur-[160px]" />
      </div>
      <div className="relative z-10 flex h-dvh flex-col overflow-hidden">
        {header}
        <main className="flex-1 min-h-0 overflow-hidden">
          <div className="mx-auto flex h-full w-full max-w-[92rem] min-h-0 flex-col px-2 pb-2 pt-2 sm:px-4 sm:pb-4 sm:pt-4 md:px-6 md:pb-5">
            <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
          </div>
        </main>
        <AnimatePresence>
          {error ? (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="pointer-events-none fixed bottom-28 left-1/2 z-40 -translate-x-1/2"
            >
              <div className="rounded-2xl border border-rose-400/30 bg-rose-500/10 px-5 py-3 text-sm text-rose-100 shadow-[0_18px_50px_rgba(244,63,94,0.18)] backdrop-blur-xl">
                {error}
              </div>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </div>
  );
}
