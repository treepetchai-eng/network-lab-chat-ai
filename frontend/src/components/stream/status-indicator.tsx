import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Activity, ServerCog, Zap } from "lucide-react";
import { PhaseTimeline } from "@/components/stream/phase-timeline";
import { cn } from "@/lib/utils";

type Phase = "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";

interface Props {
  text: string;
  history?: string[];
  phase: Phase;
  progress?: { current: number; total: number } | null;
}

/* ------------------------------------------------------------------ */
/*  Phase-based estimated progress                                     */
/*  When there's no explicit current/total from the backend, we        */
/*  simulate a smooth progress bar that advances through "waypoints"   */
/*  based on which phase we're in — so the UI always feels alive.      */
/* ------------------------------------------------------------------ */

/** Target percentage for each phase (min..max range the bar crawls through). */
function phaseRange(phase: Phase): [number, number] {
  switch (phase) {
    case "planning":   return [5, 20];
    case "grounding":  return [20, 40];
    case "executing":  return [40, 78];
    case "summarizing": return [78, 96];
    default:           return [2, 10];
  }
}

function phaseAccent(phase: Phase) {
  if (phase === "executing") return "from-cyan-400/70 via-emerald-400/50 to-emerald-300/20";
  if (phase === "summarizing") return "from-amber-400/60 via-amber-300/40 to-cyan-300/15";
  if (phase === "grounding") return "from-sky-400/60 via-cyan-400/40 to-cyan-300/18";
  return "from-cyan-400/50 via-cyan-300/30 to-white/0";
}

const PHASE_ICONS = {
  idle: Activity,
  listening: Activity,
  grounding: ServerCog,
  planning: Activity,
  executing: Zap,
  summarizing: Activity,
} satisfies Record<Phase, typeof Activity>;

export function StatusIndicator({ text, history = [], phase, progress = null }: Props) {
  const trail = history.filter((item) => item !== text).slice(-2).reverse();
  const Icon = PHASE_ICONS[phase];

  // Explicit progress from backend (batch CLI)
  const explicitPct = progress && progress.total > 0
    ? Math.max(0, Math.min(100, Math.round((progress.current / progress.total) * 100)))
    : null;

  // ---------- Simulated progress crawl ----------
  const [simPct, setSimPct] = useState(2);
  const rafRef = useRef<number | null>(null);
  const startRef = useRef(0);

  // Reset clock when phase changes
  useEffect(() => {
    startRef.current = 0;
  }, [phase]);

  // Animate simulated progress
  useEffect(() => {
    // If we have explicit progress, don't simulate
    if (explicitPct !== null) {
      return;
    }

    const [lo, hi] = phaseRange(phase);
    const crawlDuration = 25_000; // ms to crawl from lo to hi

    const tick = () => {
      const now = performance.now();
      if (!startRef.current) {
        startRef.current = now;
      }
      const elapsed = now - startRef.current;
      // Ease-out: fast start, slows down as it approaches the ceiling
      const t = Math.min(elapsed / crawlDuration, 1);
      const eased = 1 - Math.pow(1 - t, 2.5);
      const next = lo + (hi - lo) * eased;
      setSimPct((prev) => Math.max(prev, next)); // never go backwards
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [phase, explicitPct]);

  const displayPct = explicitPct !== null ? explicitPct : Math.round(simPct);

  return (
    <div className="rounded-2xl sm:rounded-[18px] border border-cyan-300/14 bg-[linear-gradient(180deg,rgba(12,34,50,0.94),rgba(8,25,40,0.86))] px-3 py-2.5 sm:px-3.5 sm:py-3 text-[0.84rem] sm:text-[0.88rem] font-medium tracking-[0.01em] text-cyan-50 shadow-[0_0_24px_rgba(34,211,238,0.08)]">
      <div className="flex items-start gap-2.5 sm:gap-3">
        <div className="relative flex h-6 w-6 shrink-0 items-center justify-center">
          <span className="absolute inset-0 rounded-full border border-cyan-300/18 bg-cyan-400/8 animate-beacon-breathe" />
          <span className="absolute inset-[2px] rounded-full border border-cyan-200/30 border-t-cyan-100/85 animate-beacon-spin" />
          <Icon className="relative h-3 w-3 text-cyan-50" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-2 sm:gap-3">
            <span className="text-[0.54rem] sm:text-[0.58rem] uppercase tracking-[0.2em] text-cyan-100/54">Live Processing</span>
            <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.05] px-2 py-0.5 text-[0.54rem] sm:text-[0.58rem] tabular-nums uppercase tracking-[0.16em] text-cyan-100/68">
              {progress ? `${progress.current}/${progress.total}` : `${displayPct}%`}
            </span>
          </div>
          <span className="mt-0.5 text-[0.82rem] sm:text-[0.88rem] leading-5 text-cyan-50 line-clamp-2">{text}</span>

          {/* ---- Progress bar ---- */}
          <div className="relative mt-2 h-2 sm:h-2.5 overflow-hidden rounded-full bg-white/[0.06]">
            {/* Shimmer overlay */}
            <div className="absolute inset-0 z-10 animate-[shimmer_2s_linear_infinite] bg-[linear-gradient(110deg,transparent_30%,rgba(255,255,255,0.12)_50%,transparent_70%)] bg-[length:200%_100%]" />
            {/* Fill */}
            <motion.div
              className={cn("relative h-full rounded-full bg-gradient-to-r shadow-[0_0_12px_rgba(34,211,238,0.25)]", phaseAccent(phase))}
              initial={false}
              animate={{ width: `${displayPct}%` }}
              transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
            />
          </div>

          <PhaseTimeline phase={phase} />
          {trail.length > 0 && (
            <div className="mt-1.5 space-y-0.5">
              {trail.map((item) => (
                <div key={item} className="truncate text-[0.68rem] sm:text-[0.72rem] text-cyan-100/52">
                  {item}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
