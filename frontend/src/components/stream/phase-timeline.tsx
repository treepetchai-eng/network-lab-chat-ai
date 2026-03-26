"use client";

import { cn } from "@/lib/utils";

type Phase = "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";

interface PhaseTimelineProps {
  phase: Phase;
}

const PHASES: Array<{ id: Phase; label: string }> = [
  { id: "planning", label: "Analyze" },
  { id: "grounding", label: "Ground" },
  { id: "executing", label: "Execute" },
  { id: "summarizing", label: "Answer" },
];

function phaseRank(phase: Phase): number {
  switch (phase) {
    case "grounding":
      return 1;
    case "executing":
      return 2;
    case "summarizing":
      return 3;
    default:
      return 0;
  }
}

export function PhaseTimeline({ phase }: PhaseTimelineProps) {
  const activeRank = phaseRank(phase);

  return (
    <div className="mt-2 rounded-xl sm:rounded-[14px] border border-white/7 bg-white/[0.028] px-2 py-1.5 sm:px-2.5 sm:py-2">
      <div className="mb-1 sm:mb-1.5 text-[0.5rem] sm:text-[0.56rem] uppercase tracking-[0.18em] text-cyan-100/40">Execution</div>
      <div className="grid grid-cols-4 gap-1.5 sm:gap-2">
        {PHASES.map((item, index) => {
          const complete = index < activeRank;
          const active = index === activeRank;
          return (
            <div key={item.id} className="relative">
              {index < PHASES.length - 1 ? (
                <div className="absolute left-[calc(50%+0.75rem)] right-[-0.6rem] top-[0.42rem] h-px bg-white/10" />
              ) : null}
              <div className="relative flex flex-col items-start gap-1">
                <span
                  className={cn(
                    "relative h-3.5 w-3.5 rounded-full border",
                    complete
                      ? "border-emerald-300/30 bg-emerald-300/18"
                      : active
                        ? "border-cyan-200/45 bg-cyan-300/18 animate-gentle-pulse"
                        : "border-white/12 bg-white/[0.03]",
                  )}
                >
                  {active ? (
                    <span className="absolute inset-[2px] rounded-full bg-cyan-100 shadow-[0_0_10px_rgba(165,243,252,0.72)]" />
                  ) : complete ? (
                    <span className="absolute inset-[2px] rounded-full bg-emerald-200" />
                  ) : null}
                </span>
                <span
                  className={cn(
                    "text-[0.56rem] sm:text-[0.64rem] font-medium tracking-[0.01em]",
                    complete
                      ? "text-emerald-100/92"
                      : active
                        ? "text-cyan-50"
                        : "text-slate-500",
                  )}
                >
                  {item.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
