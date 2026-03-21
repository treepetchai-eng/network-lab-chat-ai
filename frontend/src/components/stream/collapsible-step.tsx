"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Check, ChevronDown, Copy, Network, Server, Terminal } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { StepData } from "@/lib/types";

interface Props {
  step: StepData;
}

function StepIcon({ toolName }: { toolName: string }) {
  const lower = toolName.toLowerCase();

  if (lower.includes("ssh") || lower.includes("cli")) {
    return <Terminal className="h-3.5 w-3.5 shrink-0 text-cyan-100/80" />;
  }
  if (lower.includes("inventory") || lower.includes("lookup")) {
    return <Server className="h-3.5 w-3.5 shrink-0 text-cyan-100/80" />;
  }
  return <Network className="h-3.5 w-3.5 shrink-0 text-cyan-100/80" />;
}

function parseStepChips(step: StepData): { command: string; target: string } | null {
  const rawName = step.name.replace(/^FAILED\s+—\s+/, "");
  const parts = rawName.split(" @ ");
  if (parts.length !== 2) return null;
  return { command: parts[0].trim(), target: parts[1].trim() };
}

export function CollapsibleStep({ step }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const chips = parseStepChips(step);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(step.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen} className="my-1">
      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      >
        <CollapsibleTrigger
          className={cn(
            "group relative w-full overflow-hidden rounded-2xl sm:rounded-[22px] border px-3 py-2.5 sm:px-4 sm:py-3 text-left transition duration-300",
            step.isError
              ? "border-rose-400/18 bg-rose-500/10 text-rose-100"
              : "border-white/8 bg-white/[0.04] text-slate-200 hover:border-cyan-300/14 hover:bg-white/[0.06]",
          )}
        >
          {!step.isError ? (
            <motion.div
              className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,rgba(34,211,238,0),rgba(103,232,249,0.9),rgba(34,211,238,0))]"
              initial={{ x: "-60%" }}
              animate={{ x: "220%" }}
              transition={{ duration: 2.2, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
            />
          ) : null}
          <div className="flex items-center gap-2 sm:gap-3">
            <span
              className={cn(
                "relative flex h-7 w-7 sm:h-8 sm:w-8 items-center justify-center rounded-full border",
                step.isError
                  ? "border-rose-300/30 bg-rose-400/12"
                  : "border-cyan-300/18 bg-cyan-400/10 shadow-[0_0_24px_rgba(34,211,238,0.14)]",
              )}
            >
              {step.isError ? (
                <motion.span
                  className="h-2.5 w-2.5 rounded-full bg-rose-200"
                  animate={{ opacity: [0.7, 1, 0.75], scale: [0.92, 1.15, 0.95] }}
                  transition={{ duration: 1.4, repeat: Number.POSITIVE_INFINITY }}
                />
              ) : (
                <>
                  <motion.span
                    className="absolute inset-0 rounded-full border border-cyan-200/28"
                    animate={{ scale: [1, 1.35], opacity: [0.45, 0] }}
                    transition={{ duration: 1.6, repeat: Number.POSITIVE_INFINITY, ease: "easeOut" }}
                  />
                  <Check className="relative h-4 w-4 text-cyan-50" />
                </>
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <StepIcon toolName={step.toolName} />
                <span className="truncate text-[0.84rem] sm:text-[0.94rem] font-semibold tracking-[0.005em]">{step.name}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-0.5 sm:px-2.5 sm:py-1 text-[0.56rem] sm:text-[0.62rem] uppercase tracking-[0.18em] text-slate-400">
                  {step.toolName}
                </span>
                {chips ? (
                  <>
                    <span className="rounded-full border border-cyan-300/12 bg-cyan-400/10 px-2.5 py-1 text-[0.68rem] text-cyan-50">
                      {chips.command}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[0.68rem] text-slate-300">
                      {chips.target}
                    </span>
                  </>
                ) : null}
                <span
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.62rem] uppercase tracking-[0.18em]",
                    step.isError
                      ? "border border-rose-300/18 bg-rose-400/12 text-rose-100"
                      : "border border-emerald-300/18 bg-emerald-400/10 text-emerald-100",
                  )}
                >
                  {step.isError ? <AlertTriangle className="h-3 w-3" /> : <Check className="h-3 w-3" />}
                  {step.isError ? "Issue" : "OK"}
                </span>
              </div>
            </div>
            <ChevronDown className={cn("h-4 w-4 shrink-0 text-slate-500 transition-transform duration-300", isOpen && "rotate-180")} />
          </div>
        </CollapsibleTrigger>
      </motion.div>
      <AnimatePresence initial={false}>
        {isOpen ? (
          <CollapsibleContent>
            <motion.div
              initial={{ opacity: 0, height: 0, y: -4 }}
              animate={{ opacity: 1, height: "auto", y: 0 }}
              exit={{ opacity: 0, height: 0, y: -4 }}
              className="overflow-hidden"
            >
              <div
                className={cn(
                  "mx-1 sm:mx-2 mt-2 sm:mt-3 overflow-hidden rounded-2xl sm:rounded-[22px] border",
                  step.isError ? "border-rose-400/18 bg-rose-500/8" : "border-white/8 bg-[#08111e]/88",
                )}
              >
                <div className="flex items-center justify-between border-b border-white/8 bg-white/[0.03] px-4 py-2.5 text-[0.68rem] uppercase tracking-[0.22em] text-slate-500">
                  <span>Trace output</span>
                  <button
                    type="button"
                    onClick={handleCopy}
                    className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[0.64rem] text-slate-300 transition hover:border-cyan-300/18 hover:text-cyan-50"
                  >
                    {copied ? <Check className="h-3 w-3 text-emerald-300" /> : <Copy className="h-3 w-3" />}
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
                <pre className="overflow-x-auto p-3 sm:p-4 font-mono text-[0.68rem] sm:text-[0.76rem] leading-5 sm:leading-6 text-slate-300">
                  <code>{step.content}</code>
                </pre>
              </div>
            </motion.div>
          </CollapsibleContent>
        ) : null}
      </AnimatePresence>
    </Collapsible>
  );
}
