"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { ReactNode } from "react";
import { BackgroundAtmosphere } from "@/components/workspace/background-atmosphere";

interface AppShellProps {
  header: ReactNode;
  children: ReactNode;
  error?: string | null;
}

export function AppShell({ header, children, error }: AppShellProps) {
  return (
    <div className="relative h-dvh overflow-hidden bg-[#04070d] text-white">
      <BackgroundAtmosphere />
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
